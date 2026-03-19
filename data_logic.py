# data_logic.py
"""USASpending.gov API data fetching and GOV-1000 scoring logic."""
import json
import os
import time
import streamlit as st
import requests

# ---------------------------------------------------------------------------
# Target agencies (15 major federal departments)
# ---------------------------------------------------------------------------
AGENCIES = {
    "012": {"name": "Department of Agriculture", "abbr": "USDA"},
    "013": {"name": "Department of Commerce", "abbr": "DOC"},
    "097": {"name": "Department of Defense", "abbr": "DOD"},
    "091": {"name": "Department of Education", "abbr": "ED"},
    "089": {"name": "Department of Energy", "abbr": "DOE"},
    "075": {"name": "Department of Health and Human Services", "abbr": "HHS"},
    "086": {"name": "Department of Housing and Urban Development", "abbr": "HUD"},
    "015": {"name": "Department of Justice", "abbr": "DOJ"},
    "1601": {"name": "Department of Labor", "abbr": "DOL"},
    "014": {"name": "Department of the Interior", "abbr": "DOI"},
    "019": {"name": "Department of State", "abbr": "DOS"},
    "020": {"name": "Department of the Treasury", "abbr": "TREAS"},
    "069": {"name": "Department of Transportation", "abbr": "DOT"},
    "036": {"name": "Department of Veterans Affairs", "abbr": "VA"},
    "028": {"name": "Social Security Administration", "abbr": "SSA"},
}

AXES_LABELS = [
    "Budget Efficiency",
    "Transparency",
    "Performance",
    "Fiscal Discipline",
    "Accountability",
]

API_BASE = "https://api.usaspending.gov/api/v2"

# ---------------------------------------------------------------------------
# Low-level API helpers (cached 24 h)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_agency_list() -> list[dict] | None:
    """GET /api/v2/references/toptier_agencies/ — all agencies with financials."""
    try:
        r = requests.get(f"{API_BASE}/references/toptier_agencies/", timeout=30)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_agency_overview(toptier_code: str) -> dict | None:
    """GET /api/v2/agency/{toptier_code}/ — mission, website, sub-agency count."""
    try:
        r = requests.get(f"{API_BASE}/agency/{toptier_code}/", timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_agency_budgetary_resources(toptier_code: str) -> list[dict] | None:
    """GET /api/v2/agency/{toptier_code}/budgetary_resources/ — multi-year budget."""
    try:
        r = requests.get(
            f"{API_BASE}/agency/{toptier_code}/budgetary_resources/",
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("agency_data_by_year", [])
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_agency_sub_components(toptier_code: str) -> dict | None:
    """GET /api/v2/agency/{toptier_code}/sub_agency/ — sub-agency breakdown."""
    try:
        r = requests.get(
            f"{API_BASE}/agency/{toptier_code}/sub_agency/?fiscal_year={_current_fy()}",
            timeout=30,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GAO findings (manual JSON)
# ---------------------------------------------------------------------------
_GAO_FILE = os.path.join(os.path.dirname(__file__), "gao_findings.json")


def _load_gao_findings() -> dict:
    if os.path.exists(_GAO_FILE):
        with open(_GAO_FILE, "r") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_fy() -> int:
    """Return the current US federal fiscal year."""
    from datetime import date
    today = date.today()
    return today.year if today.month >= 10 else today.year


def _clamp(value: float, lo: float = 0, hi: float = 200) -> int:
    return int(min(max(value, lo), hi))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_agency(toptier_code: str) -> dict | None:
    """Compute GOV-1000 score for one agency. Returns standard dict or None."""
    agency_info = AGENCIES.get(toptier_code)
    if not agency_info:
        return None

    # Fetch all data sources
    agency_list = fetch_agency_list()
    overview = fetch_agency_overview(toptier_code)
    budget_history = fetch_agency_budgetary_resources(toptier_code)
    sub_components = fetch_agency_sub_components(toptier_code)

    # Find this agency in the list
    agency_row = None
    if agency_list:
        for a in agency_list:
            if str(a.get("toptier_code", "")) == toptier_code:
                agency_row = a
                break

    if agency_row is None:
        return None

    # ---------------------------------------------------------------
    # Axis 1: Budget Efficiency (200)
    # ---------------------------------------------------------------
    budget_auth = agency_row.get("budget_authority_amount") or 0
    obligated = agency_row.get("obligated_amount") or 0
    outlay = agency_row.get("outlay_amount") or 0

    if budget_auth > 0:
        outlay_ratio = outlay / budget_auth
        obligation_ratio = obligated / budget_auth
        budget_efficiency = _clamp(outlay_ratio * 120 + obligation_ratio * 80)
    else:
        budget_efficiency = 100  # neutral

    # ---------------------------------------------------------------
    # Axis 2: Transparency (200)
    # ---------------------------------------------------------------
    cj_score = 0
    sub_score = 0
    completeness_score = 0

    if overview:
        # Congressional justification URL
        cj_url = overview.get("congressional_justification_url")
        cj_score = 60 if cj_url else 0

        # Sub-agency reporting count
        subtier_count = overview.get("subtier_agency_count") or 0
        sub_score = _clamp(subtier_count * 5, 0, 80)

        # Data completeness (count non-null important fields)
        fields_to_check = [
            "mission", "website", "icon_filename",
            "congressional_justification_url",
        ]
        non_null = sum(1 for f in fields_to_check if overview.get(f))
        completeness_score = _clamp((non_null / len(fields_to_check)) * 60, 0, 60)
    else:
        cj_score = 30  # neutral fallback

    transparency = _clamp(cj_score + sub_score + completeness_score)

    # ---------------------------------------------------------------
    # Axis 3: Performance (200)
    # ---------------------------------------------------------------
    if sub_components:
        results = sub_components.get("results", [])
        total_transactions = sum(r.get("transaction_count", 0) or 0 for r in results)
        total_awards = sum(r.get("new_award_count", 0) or 0 for r in results)

        tx_score = _clamp(total_transactions / 50000 * 100, 0, 120)
        award_score = _clamp(total_awards / 10000 * 80, 0, 80)
        performance = _clamp(tx_score + award_score)
    else:
        performance = 100  # neutral

    # ---------------------------------------------------------------
    # Axis 4: Fiscal Discipline (200)
    # ---------------------------------------------------------------
    if budget_history and len(budget_history) >= 2:
        # Sort by fiscal year descending
        sorted_years = sorted(budget_history, key=lambda x: x.get("fiscal_year", 0), reverse=True)
        current_yr = sorted_years[0]
        previous_yr = sorted_years[1]

        curr_budget = current_yr.get("agency_budgetary_resources") or 0
        prev_budget = previous_yr.get("agency_budgetary_resources") or 0

        if prev_budget > 0:
            yoy_growth = ((curr_budget / prev_budget) - 1) * 100
        else:
            yoy_growth = 0

        growth_score = _clamp(150 - abs(yoy_growth) * 5, 0, 150)

        curr_obligated = current_yr.get("agency_total_obligated") or 0
        if curr_budget > 0:
            unobligated_ratio = (curr_budget - curr_obligated) / curr_budget
            unobligated_score = _clamp(50 - unobligated_ratio * 100, 0, 50)
        else:
            unobligated_score = 25

        fiscal_discipline = _clamp(growth_score + unobligated_score)
    else:
        fiscal_discipline = 100  # neutral

    # ---------------------------------------------------------------
    # Axis 5: Accountability (200)
    # ---------------------------------------------------------------
    gao_data = _load_gao_findings()
    finding_count = gao_data.get(toptier_code, 5)  # default moderate
    accountability = _clamp(200 - finding_count * 20)

    # ---------------------------------------------------------------
    # Assemble result
    # ---------------------------------------------------------------
    axes = {
        "Budget Efficiency": budget_efficiency,
        "Transparency": transparency,
        "Performance": performance,
        "Fiscal Discipline": fiscal_discipline,
        "Accountability": accountability,
    }
    total = sum(axes.values())

    return {
        "name": agency_info["name"],
        "abbr": agency_info["abbr"],
        "toptier_code": toptier_code,
        "axes": axes,
        "total": total,
        "budget_authority": budget_auth,
        "obligated": obligated,
        "outlay": outlay,
        "pct_of_total": (agency_row.get("percentage_of_total_budget_authority") or 0) * 100,
    }
