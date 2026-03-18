#!/usr/bin/env python3
"""Daily score recorder for GOV-1000 (standalone, no Streamlit dependency)."""
import json
import os
import sys
import time
from datetime import date

import requests

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "scores_history.json")
API_BASE = "https://api.usaspending.gov/api/v2"

AGENCIES = {
    "012": "Department of Agriculture",
    "013": "Department of Commerce",
    "097": "Department of Defense",
    "091": "Department of Education",
    "089": "Department of Energy",
    "075": "Department of Health and Human Services",
    "086": "Department of Housing and Urban Development",
    "015": "Department of Justice",
    "1601": "Department of Labor",
    "014": "Department of the Interior",
    "019": "Department of State",
    "020": "Department of the Treasury",
    "069": "Department of Transportation",
    "036": "Department of Veterans Affairs",
    "028": "Social Security Administration",
}


def _current_fy() -> int:
    today = date.today()
    return today.year + 1 if today.month >= 10 else today.year


def _clamp(value: float, lo: float = 0, hi: float = 200) -> int:
    return int(min(max(value, lo), hi))


def _fetch_json(url: str, method: str = "GET", payload: dict = None) -> dict | None:
    """Fetch JSON with retry logic and delay."""
    for attempt in range(3):
        try:
            if method == "POST":
                r = requests.post(url, json=payload, timeout=60)
            else:
                r = requests.get(url, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                # Rate limited - wait longer
                print(f"    Rate limited, waiting {10 * (attempt + 1)}s...")
                time.sleep(10 * (attempt + 1))
                continue
            print(f"    HTTP {r.status_code} for {url}")
            return None
        except requests.exceptions.Timeout:
            print(f"    Timeout (attempt {attempt + 1}/3) for {url}")
            time.sleep(5)
        except Exception as e:
            print(f"    Error (attempt {attempt + 1}/3): {e}")
            time.sleep(5)
    return None


def _load_gao() -> dict:
    gao_file = os.path.join(os.path.dirname(__file__), "gao_findings.json")
    if os.path.exists(gao_file):
        with open(gao_file, "r") as f:
            return json.load(f)
    return {}


def score_one(toptier_code: str, agency_list: list) -> int | None:
    """Score one agency without Streamlit. Returns total score or None."""
    # Find in agency list
    agency_row = None
    for a in agency_list:
        if str(a.get("toptier_code", "")) == toptier_code:
            agency_row = a
            break
    if not agency_row:
        print(f"    Agency code {toptier_code} not found in agency list")
        return None

    # Axis 1: Budget Efficiency
    budget_auth = agency_row.get("budget_authority_amount") or 0
    obligated = agency_row.get("obligated_amount") or 0
    outlay = agency_row.get("outlay_amount") or 0
    if budget_auth > 0:
        budget_efficiency = _clamp(
            (outlay / budget_auth) * 120 + (obligated / budget_auth) * 80
        )
    else:
        budget_efficiency = 100

    time.sleep(1)  # Delay between API calls

    # Axis 2: Transparency
    overview = _fetch_json(f"{API_BASE}/agency/{toptier_code}/")
    if overview:
        cj = 60 if overview.get("congressional_justification_url") else 0
        sub_count = overview.get("subtier_agency_count") or 0
        sub_s = _clamp(sub_count * 5, 0, 80)
        fields = ["mission", "website", "icon_filename", "congressional_justification_url"]
        non_null = sum(1 for f in fields if overview.get(f))
        comp = _clamp((non_null / len(fields)) * 60, 0, 60)
        transparency = _clamp(cj + sub_s + comp)
    else:
        transparency = 100

    time.sleep(1)

    # Axis 3: Performance
    sub_data = _fetch_json(
        f"{API_BASE}/agency/{toptier_code}/sub_agency/",
        method="POST",
        payload={"fiscal_year": _current_fy(), "limit": 100, "page": 1},
    )
    if sub_data:
        results = sub_data.get("results", [])
        total_tx = sum(r.get("transaction_count", 0) or 0 for r in results)
        total_aw = sum(r.get("new_award_count", 0) or 0 for r in results)
        performance = _clamp(
            _clamp(total_tx / 50000 * 100, 0, 120) + _clamp(total_aw / 10000 * 80, 0, 80)
        )
    else:
        performance = 100

    time.sleep(1)

    # Axis 4: Fiscal Discipline
    budget_hist_data = _fetch_json(f"{API_BASE}/agency/{toptier_code}/budgetary_resources/")
    budget_hist = budget_hist_data.get("agency_data_by_year", []) if budget_hist_data else []
    if len(budget_hist) >= 2:
        sorted_years = sorted(budget_hist, key=lambda x: x.get("fiscal_year", 0), reverse=True)
        curr_b = sorted_years[0].get("agency_budgetary_resources") or 0
        prev_b = sorted_years[1].get("agency_budgetary_resources") or 0
        yoy = ((curr_b / prev_b) - 1) * 100 if prev_b > 0 else 0
        growth_s = _clamp(150 - abs(yoy) * 5, 0, 150)
        curr_obl = sorted_years[0].get("agency_total_obligated") or 0
        unob_ratio = (curr_b - curr_obl) / curr_b if curr_b > 0 else 0.5
        unob_s = _clamp(50 - unob_ratio * 100, 0, 50)
        fiscal_discipline = _clamp(growth_s + unob_s)
    else:
        fiscal_discipline = 100

    # Axis 5: Accountability
    gao = _load_gao()
    findings = gao.get(toptier_code, 5)
    accountability = _clamp(200 - findings * 20)

    total = budget_efficiency + transparency + performance + fiscal_discipline + accountability
    return total


def main():
    today_str = date.today().isoformat()
    print(f"[GOV-1000] Recording scores for {today_str}")

    # Load history
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
    else:
        history = {}

    # Skip if already recorded today
    if today_str in history:
        print(f"[GOV-1000] Scores already recorded for {today_str}, skipping")
        sys.exit(0)

    # Fetch agency list with retry
    print("  Fetching agency list...")
    data = None
    for attempt in range(3):
        data = _fetch_json(f"{API_BASE}/references/toptier_agencies/")
        if data:
            break
        print(f"  Retry {attempt + 1}/3 for agency list...")
        time.sleep(10)

    if not data:
        print("ERROR: Failed to fetch agency list after 3 attempts")
        sys.exit(1)
    agency_list = data.get("results", [])
    print(f"  Found {len(agency_list)} agencies in API")

    day_scores = {}
    success = 0
    for code, name in AGENCIES.items():
        print(f"  Scoring {name}...")
        score = score_one(code, agency_list)
        if score is not None:
            day_scores[name] = score
            success += 1
            print(f"    {name}: {score}")
        else:
            print(f"    {name}: FAILED")
        time.sleep(2)  # Delay between agencies

    if success < 5:
        print(f"ERROR: Only {success}/15 agencies scored, skipping save")
        sys.exit(1)

    history[today_str] = day_scores

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

    print(f"[GOV-1000] Saved {success}/15 scores for {today_str}")


if __name__ == "__main__":
    main()
