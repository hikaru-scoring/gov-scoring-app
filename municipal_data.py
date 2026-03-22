# municipal_data.py
"""Census Bureau Individual Unit Finance file parser and municipal scoring logic."""

import os
import streamlit as st

# ---------------------------------------------------------------------------
# Data directory and file paths
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE_DIR, "census_data")

def _data_files():
    return {
        2022: {
            "pid": os.path.join(DATA_DIR, "2022", "2022_Individual_Unit_File", "Fin_PID_2022.txt"),
            "finance": os.path.join(DATA_DIR, "2022", "2022_Individual_Unit_File", "2022FinEstDAT_06052025modp_pu.txt"),
        },
        2023: {
            "pid": os.path.join(DATA_DIR, "2023", "2023_Individual_Unit_Files", "Fin_PID_2023.txt"),
            "finance": os.path.join(DATA_DIR, "2023", "2023_Individual_Unit_Files", "2023FinEstDAT_06052025modp_pu.txt"),
        },
    }

DATA_FILES = _data_files()

# ---------------------------------------------------------------------------
# State FIPS -> abbreviation lookup
# ---------------------------------------------------------------------------
STATE_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}

# ---------------------------------------------------------------------------
# Scoring axis labels and descriptions
# ---------------------------------------------------------------------------
MUNICIPAL_AXES_LABELS = [
    "Budget Balance",
    "Tax Base Strength",
    "Revenue Independence",
    "Spending Efficiency",
    "Economic Health",
]

MUNICIPAL_LOGIC_DESC = {
    "Budget Balance": (
        "Measures whether revenue exceeds expenditure. "
        "ratio = (revenue - expenditure) / revenue. "
        "Surplus: 100 + ratio*500; Deficit: 100 + ratio*300. Clamped 0-200."
    ),
    "Tax Base Strength": (
        "Ratio of self-generated tax revenue to total revenue. "
        "score = tax_ratio * 300, clamped 0-200. Higher = stronger fiscal base."
    ),
    "Revenue Independence": (
        "Measures reliance on intergovernmental transfers. "
        "score = 200 - ig_ratio * 400, clamped 0-200. Less dependency = higher score."
    ),
    "Spending Efficiency": (
        "How well revenue covers expenditure. "
        "score = (revenue / expenditure) * 120, clamped 0-200."
    ),
    "Economic Health": (
        "Median income x Poverty rate x Unemployment rate. "
        "Combines ACS median income, poverty rate, and BLS unemployment. "
        "Falls back to revenue per capita if ACS/BLS data unavailable."
    ),
}

# ---------------------------------------------------------------------------
# Key item codes from Census Bureau finance data
# ---------------------------------------------------------------------------
ITEM_TOTAL_REVENUE = "19U"
ITEM_TOTAL_EXPENDITURE = "49U"
ITEM_TOTAL_TAXES = "T01"
ITEM_IG_REVENUE = "39U"  # Total Intergovernmental Revenue


# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------
def parse_pid_file(filepath: str) -> dict:
    """Parse a Census Bureau PID file and return municipality records.

    Returns dict mapping gov_id (12-char) to:
        {name, state_fips, type_code, county, population, fips_place}

    Only type_code starting with '2' (municipalities) are included.
    """
    result = {}
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if len(line.rstrip("\n\r")) < 126:
                continue
            type_code = line[2]
            if type_code != "2":
                continue
            gov_id = line[0:12]
            name = line[12:76].strip()
            state_fips = line[0:2]
            county = line[76:111].strip()
            fips_place = line[111:116].strip()
            # Population is positions 116-124 (9 chars); 124-125 is year suffix
            pop_str = line[116:125].strip()
            try:
                population = int(pop_str)
            except (ValueError, IndexError):
                population = 0
            result[gov_id] = {
                "name": name,
                "state_fips": state_fips,
                "type_code": type_code,
                "county": county,
                "population": population,
                "fips_place": fips_place,
            }
    return result


def parse_finance_file(filepath: str) -> dict:
    """Parse a Census Bureau finance file.

    Returns dict mapping gov_id to {item_code: amount_in_dollars, ...}.
    Amounts are stored in thousands in the file; we multiply by 1000.
    """
    result = {}
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.rstrip("\n\r")
            if len(stripped) < 27:
                continue
            gov_id = stripped[0:12]
            item_code = stripped[12:15]
            amount_str = stripped[15:27].strip()
            try:
                amount = int(amount_str) * 1000
            except ValueError:
                continue
            if gov_id not in result:
                result[gov_id] = {}
            result[gov_id][item_code] = amount
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _clamp(value: float, lo: float = 0.0, hi: float = 200.0) -> float:
    return max(lo, min(hi, value))


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_acs_county_data(year: int = 2022) -> dict:
    """Fetch county-level economic data from Census ACS 5-Year API.

    Returns dict keyed by 5-digit county FIPS:
    {fips: {"median_income": int, "per_capita_income": int, "population": int, "poverty_pop": int}, ...}
    """
    import requests
    url = f"https://api.census.gov/data/{year}/acs/acs5"
    params = {
        "get": "NAME,B19013_001E,B19301_001E,B01003_001E,B17001_002E",
        "for": "county:*",
    }
    try:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return {}

    result = {}
    # First row is headers
    for row in rows[1:]:
        # row: [NAME, median_income, per_capita_income, population, poverty_pop, state, county]
        state_fips = row[5]
        county_fips = row[6]
        fips = f"{state_fips}{county_fips}"
        try:
            result[fips] = {
                "name": row[0],
                "median_income": int(row[1]) if row[1] else 0,
                "per_capita_income": int(row[2]) if row[2] else 0,
                "population": int(row[3]) if row[3] else 0,
                "poverty_pop": int(row[4]) if row[4] else 0,
            }
        except (ValueError, TypeError):
            continue
    return result


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_bls_county_unemployment() -> dict:
    """Fetch annual average unemployment rate for all counties from BLS LAUS.

    Returns dict keyed by 5-digit county FIPS: {fips: unemployment_rate, ...}

    Uses BLS API v2 (no key required for basic access).
    Fetches in batches of 25 series per request.
    """
    import requests
    import time

    # Get all county FIPS from ACS data
    acs = fetch_acs_county_data()
    if not acs:
        return {}

    all_fips = list(acs.keys())
    result = {}

    # BLS allows 25 series per request
    batch_size = 25
    for i in range(0, len(all_fips), batch_size):
        batch_fips = all_fips[i:i + batch_size]
        series_ids = [f"LAUCN{fips}0000000003" for fips in batch_fips]

        try:
            r = requests.post(
                "https://api.bls.gov/publicAPI/v2/timeseries/data/",
                json={"seriesid": series_ids, "startyear": "2022", "endyear": "2022"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()

            if data.get("status") == "REQUEST_SUCCEEDED":
                for series in data.get("Results", {}).get("series", []):
                    sid = series["seriesID"]
                    # Extract FIPS from series ID: LAUCN{5-digit FIPS}0000000003
                    fips = sid[5:10]
                    # Get annual average (M13)
                    for d in series.get("data", []):
                        if d.get("period") == "M13":  # Annual average
                            try:
                                result[fips] = float(d["value"])
                            except (ValueError, TypeError):
                                pass
                            break
        except Exception:
            pass

        # Rate limiting - be respectful
        if i + batch_size < len(all_fips):
            time.sleep(1)

    return result


def score_municipality(gov_id: str, pid_data: dict, finance_data: dict,
                       acs_data: dict | None = None, bls_data: dict | None = None) -> dict | None:
    """Score a single municipality across 5 fiscal axes (0-200 each, total 0-1000).

    Returns None if the gov_id is not found in pid_data.
    """
    if gov_id not in pid_data:
        return None

    info = pid_data[gov_id]
    fin = finance_data.get(gov_id, {})

    revenue = fin.get(ITEM_TOTAL_REVENUE, 0)
    expenditure = fin.get(ITEM_TOTAL_EXPENDITURE, 0)
    taxes = fin.get(ITEM_TOTAL_TAXES, 0)
    ig_revenue = fin.get(ITEM_IG_REVENUE, 0)
    population = info["population"]

    # Axis 1: Budget Balance (200)
    if revenue > 0:
        ratio = (revenue - expenditure) / revenue
        if ratio >= 0:
            ax1 = _clamp(100 + ratio * 500)
        else:
            ax1 = _clamp(100 + ratio * 300)
    else:
        ax1 = 0.0

    # Axis 2: Tax Base Strength (200)
    if revenue > 0:
        tax_ratio = taxes / revenue
        ax2 = _clamp(tax_ratio * 300)
    else:
        ax2 = 0.0

    # Axis 3: Revenue Independence (200)
    if revenue > 0:
        ig_ratio = ig_revenue / revenue
        ax3 = _clamp(200 - ig_ratio * 400)
    else:
        ax3 = 0.0

    # Axis 4: Spending Efficiency (200)
    ax4 = _clamp((revenue / max(expenditure, 1)) * 120)

    # Axis 5: Economic Health (200)
    # Try ACS/BLS data first (municipality uses fips_place mapped via county)
    fips = info.get("fips_county") or info.get("fips_place", "")
    _ax5_set = False
    if acs_data and fips in acs_data:
        acs = acs_data[fips]
        pop = acs["population"] or 1
        poverty_rate = acs["poverty_pop"] / pop
        income_score = _clamp(acs["median_income"] / 500, 0, 100)
        poverty_score = _clamp(100 - poverty_rate * 500, 0, 100)

        if bls_data and fips in bls_data:
            unemp = bls_data[fips]
            unemp_score = _clamp(100 - unemp * 15, 0, 100)
            ax5 = _clamp((income_score * 0.4 + poverty_score * 0.3 + unemp_score * 0.3) * 2, 0, 200)
        else:
            ax5 = _clamp((income_score * 0.5 + poverty_score * 0.5) * 2, 0, 200)
        _ax5_set = True

    if not _ax5_set:
        # Fallback to old revenue per capita method
        if population > 0:
            revenue_per_capita = revenue / population
            ax5 = _clamp(revenue_per_capita / 50)
        else:
            ax5 = 0.0

    axes = {
        "Budget Balance": round(ax1, 1),
        "Tax Base Strength": round(ax2, 1),
        "Revenue Independence": round(ax3, 1),
        "Spending Efficiency": round(ax4, 1),
        "Economic Health": round(ax5, 1),
    }

    return {
        "name": info["name"],
        "gov_id": gov_id,
        "state_fips": info["state_fips"],
        "state_abbr": STATE_ABBR.get(info["state_fips"], "??"),
        "population": population,
        "axes": axes,
        "total": round(sum(axes.values()), 1),
        "revenue": revenue,
        "expenditure": expenditure,
        "taxes": taxes,
        "ig_revenue": ig_revenue,
    }


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400)
def load_and_score_top_cities(n: int = 100, year: int = 2022) -> list[dict]:
    """Load Census data, score top-n municipalities by population, return sorted by total score."""
    if year not in DATA_FILES:
        raise ValueError(f"No data files configured for year {year}")

    paths = DATA_FILES[year]
    pid_data = parse_pid_file(paths["pid"])
    finance_data = parse_finance_file(paths["finance"])

    # Sort municipalities by population descending and take top n
    sorted_munis = sorted(
        pid_data.items(),
        key=lambda kv: kv[1]["population"],
        reverse=True,
    )
    top_ids = [gov_id for gov_id, _ in sorted_munis[:n]]

    # Score each municipality
    scored = []
    for gov_id in top_ids:
        result = score_municipality(gov_id, pid_data, finance_data)
        if result is not None:
            scored.append(result)

    # Sort by total score descending
    scored.sort(key=lambda x: x["total"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# County scoring (type_code '1')
# ---------------------------------------------------------------------------
COUNTY_AXES_LABELS = list(MUNICIPAL_AXES_LABELS)
COUNTY_LOGIC_DESC = dict(MUNICIPAL_LOGIC_DESC)


def parse_pid_file_counties(filepath: str) -> dict:
    """Parse PID file for county governments (type_code '1').

    Returns dict mapping gov_id to {name, state_fips, county_fips, population, fips_county}.
    The 5-digit county FIPS = state_fips (2 chars, positions 0-1) + county code (positions 3-5).
    """
    result = {}
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if len(line.rstrip("\n\r")) < 126:
                continue
            type_code = line[2]
            if type_code != "1":
                continue
            gov_id = line[0:12]
            name = line[12:76].strip()
            state_fips = line[0:2]
            county_code = line[3:6]  # positions 3-5 (3 chars)
            pop_str = line[116:125].strip()
            try:
                population = int(pop_str)
            except (ValueError, IndexError):
                population = 0
            fips_county = f"{state_fips}{county_code}"  # 5-digit FIPS
            result[gov_id] = {
                "name": name,
                "state_fips": state_fips,
                "county_fips": county_code,
                "population": population,
                "fips_county": fips_county,
            }
    return result


def score_county(gov_id: str, pid_data: dict, finance_data: dict,
                 acs_data: dict | None = None, bls_data: dict | None = None) -> dict | None:
    """Score a county using same 5 axes as municipalities.

    Returns dict with additional 'fips_county' field (5-digit: state_fips + county_fips).
    """
    if gov_id not in pid_data:
        return None

    info = pid_data[gov_id]
    fin = finance_data.get(gov_id, {})

    revenue = fin.get(ITEM_TOTAL_REVENUE, 0)
    expenditure = fin.get(ITEM_TOTAL_EXPENDITURE, 0)
    taxes = fin.get(ITEM_TOTAL_TAXES, 0)
    ig_revenue = fin.get(ITEM_IG_REVENUE, 0)
    population = info["population"]

    # Axis 1: Budget Balance (200)
    if revenue > 0:
        ratio = (revenue - expenditure) / revenue
        if ratio >= 0:
            ax1 = _clamp(100 + ratio * 500)
        else:
            ax1 = _clamp(100 + ratio * 300)
    else:
        ax1 = 0.0

    # Axis 2: Tax Base Strength (200)
    if revenue > 0:
        tax_ratio = taxes / revenue
        ax2 = _clamp(tax_ratio * 300)
    else:
        ax2 = 0.0

    # Axis 3: Revenue Independence (200)
    if revenue > 0:
        ig_ratio = ig_revenue / revenue
        ax3 = _clamp(200 - ig_ratio * 400)
    else:
        ax3 = 0.0

    # Axis 4: Spending Efficiency (200)
    ax4 = _clamp((revenue / max(expenditure, 1)) * 120)

    # Axis 5: Economic Health (200)
    fips = info["fips_county"]
    _ax5_set = False
    if acs_data and fips in acs_data:
        acs = acs_data[fips]
        pop = acs["population"] or 1
        poverty_rate = acs["poverty_pop"] / pop
        income_score = _clamp(acs["median_income"] / 500, 0, 100)
        poverty_score = _clamp(100 - poverty_rate * 500, 0, 100)

        if bls_data and fips in bls_data:
            unemp = bls_data[fips]
            unemp_score = _clamp(100 - unemp * 15, 0, 100)
            ax5 = _clamp((income_score * 0.4 + poverty_score * 0.3 + unemp_score * 0.3) * 2, 0, 200)
        else:
            ax5 = _clamp((income_score * 0.5 + poverty_score * 0.5) * 2, 0, 200)
        _ax5_set = True

    if not _ax5_set:
        # Fallback to old revenue per capita method
        if population > 0:
            revenue_per_capita = revenue / population
            ax5 = _clamp(revenue_per_capita / 50)
        else:
            ax5 = 0.0

    axes = {
        "Budget Balance": round(ax1, 1),
        "Tax Base Strength": round(ax2, 1),
        "Revenue Independence": round(ax3, 1),
        "Spending Efficiency": round(ax4, 1),
        "Economic Health": round(ax5, 1),
    }

    return {
        "name": info["name"],
        "gov_id": gov_id,
        "state_fips": info["state_fips"],
        "state_abbr": STATE_ABBR.get(info["state_fips"], "??"),
        "population": population,
        "axes": axes,
        "total": round(sum(axes.values()), 1),
        "revenue": revenue,
        "expenditure": expenditure,
        "taxes": taxes,
        "ig_revenue": ig_revenue,
        "fips_county": info["fips_county"],
    }


@st.cache_data(ttl=86400)
def load_and_score_all_counties(year: int = 2022) -> list[dict]:
    """Load and score ALL counties. Returns list sorted by total score descending."""
    if year not in DATA_FILES:
        raise ValueError(f"No data files configured for year {year}")

    paths = DATA_FILES[year]
    pid_data = parse_pid_file_counties(paths["pid"])
    finance_data = parse_finance_file(paths["finance"])
    acs_data = fetch_acs_county_data(year)
    bls_data = fetch_bls_county_unemployment()

    # Score counties that have Census fiscal data
    scored = []
    scored_fips = set()
    for gov_id in pid_data:
        result = score_county(gov_id, pid_data, finance_data, acs_data, bls_data)
        if result is not None and result["total"] > 0:
            scored.append(result)
            scored_fips.add(result["fips_county"])

    # NOTE: Counties without Census fiscal data remain unscored (white on map).
    # We do NOT fill them with neutral values — that would be misleading.

    scored.sort(key=lambda x: x["total"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# CLI quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print("Loading and scoring top 10 municipalities (2022)...\n")
    pid = parse_pid_file(DATA_FILES[2022]["pid"])
    fin = parse_finance_file(DATA_FILES[2022]["finance"])

    # Top 10 by population
    sorted_munis = sorted(pid.items(), key=lambda kv: kv[1]["population"], reverse=True)
    print(f"Total municipalities parsed: {len(pid)}\n")

    print(f"{'Rank':<5} {'City':<35} {'State':<6} {'Pop':>12} {'Score':>7}  Axes")
    print("-" * 110)
    results = []
    for gov_id, info in sorted_munis[:10]:
        r = score_municipality(gov_id, pid, fin)
        if r:
            results.append(r)

    results.sort(key=lambda x: x["total"], reverse=True)
    for i, r in enumerate(results, 1):
        axes_str = "  ".join(f"{k[:6]}:{v}" for k, v in r["axes"].items())
        print(
            f"{i:<5} {r['name']:<35} {r['state_abbr']:<6} "
            f"{r['population']:>12,} {r['total']:>7.1f}  {axes_str}"
        )

    print("\n--- Revenue & Expenditure ---")
    for r in results:
        print(
            f"  {r['name']:<35} Rev: ${r['revenue']:>15,}  "
            f"Exp: ${r['expenditure']:>15,}  "
            f"Tax: ${r['taxes']:>15,}  "
            f"IG: ${r['ig_revenue']:>15,}"
        )
