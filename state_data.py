# state_data.py
"""Census Bureau API data fetching and state-level GOV-1000 scoring logic."""
import time
import streamlit as st
import requests

# ---------------------------------------------------------------------------
# 50 US States – FIPS code -> name & abbreviation (DC excluded)
# ---------------------------------------------------------------------------
STATES = {
    "01": {"name": "Alabama", "abbr": "AL"},
    "02": {"name": "Alaska", "abbr": "AK"},
    "04": {"name": "Arizona", "abbr": "AZ"},
    "05": {"name": "Arkansas", "abbr": "AR"},
    "06": {"name": "California", "abbr": "CA"},
    "08": {"name": "Colorado", "abbr": "CO"},
    "09": {"name": "Connecticut", "abbr": "CT"},
    "10": {"name": "Delaware", "abbr": "DE"},
    "12": {"name": "Florida", "abbr": "FL"},
    "13": {"name": "Georgia", "abbr": "GA"},
    "15": {"name": "Hawaii", "abbr": "HI"},
    "16": {"name": "Idaho", "abbr": "ID"},
    "17": {"name": "Illinois", "abbr": "IL"},
    "18": {"name": "Indiana", "abbr": "IN"},
    "19": {"name": "Iowa", "abbr": "IA"},
    "20": {"name": "Kansas", "abbr": "KS"},
    "21": {"name": "Kentucky", "abbr": "KY"},
    "22": {"name": "Louisiana", "abbr": "LA"},
    "23": {"name": "Maine", "abbr": "ME"},
    "24": {"name": "Maryland", "abbr": "MD"},
    "25": {"name": "Massachusetts", "abbr": "MA"},
    "26": {"name": "Michigan", "abbr": "MI"},
    "27": {"name": "Minnesota", "abbr": "MN"},
    "28": {"name": "Mississippi", "abbr": "MS"},
    "29": {"name": "Missouri", "abbr": "MO"},
    "30": {"name": "Montana", "abbr": "MT"},
    "31": {"name": "Nebraska", "abbr": "NE"},
    "32": {"name": "Nevada", "abbr": "NV"},
    "33": {"name": "New Hampshire", "abbr": "NH"},
    "34": {"name": "New Jersey", "abbr": "NJ"},
    "35": {"name": "New Mexico", "abbr": "NM"},
    "36": {"name": "New York", "abbr": "NY"},
    "37": {"name": "North Carolina", "abbr": "NC"},
    "38": {"name": "North Dakota", "abbr": "ND"},
    "39": {"name": "Ohio", "abbr": "OH"},
    "40": {"name": "Oklahoma", "abbr": "OK"},
    "41": {"name": "Oregon", "abbr": "OR"},
    "42": {"name": "Pennsylvania", "abbr": "PA"},
    "44": {"name": "Rhode Island", "abbr": "RI"},
    "45": {"name": "South Carolina", "abbr": "SC"},
    "46": {"name": "South Dakota", "abbr": "SD"},
    "47": {"name": "Tennessee", "abbr": "TN"},
    "48": {"name": "Texas", "abbr": "TX"},
    "49": {"name": "Utah", "abbr": "UT"},
    "50": {"name": "Vermont", "abbr": "VT"},
    "51": {"name": "Virginia", "abbr": "VA"},
    "53": {"name": "Washington", "abbr": "WA"},
    "54": {"name": "West Virginia", "abbr": "WV"},
    "55": {"name": "Wisconsin", "abbr": "WI"},
    "56": {"name": "Wyoming", "abbr": "WY"},
}

# ---------------------------------------------------------------------------
# Scoring axes
# ---------------------------------------------------------------------------
STATE_AXES_LABELS = [
    "Budget Balance",
    "Debt Burden",
    "Revenue Independence",
    "Spending Efficiency",
    "Fiscal Reserve",
]

STATE_LOGIC_DESC = {
    "Budget Balance": "Revenue vs Expenditure ratio",
    "Debt Burden": "Total debt relative to revenue",
    "Revenue Independence": "Self-generated vs Federal funding ratio",
    "Spending Efficiency": "Low interest cost x High capital investment",
    "Fiscal Reserve": "Cash & securities relative to spending",
}

# ---------------------------------------------------------------------------
# Census Bureau API – variable codes
# ---------------------------------------------------------------------------
CENSUS_BASE = "https://api.census.gov/data/timeseries/govs"

_VARIABLE_CODES = {
    "revenue":         "LF0001",  # Total Revenue
    "expenditure":     "LF0089",  # Total Expenditure
    "taxes":           "LF0008",  # Total Taxes
    "federal_revenue": "LF0004",  # Federal Intergovernmental Revenue
    "interest":        "LF0098",  # Interest on Debt
    "debt":            "LF0230",  # Total Debt Outstanding
    "cash_holdings":   "LF0236",  # Cash & Security Holdings
    "capital_outlay":  "LF0094",  # Capital Outlay
}

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _clamp(value, lo=0, hi=200):
    return int(min(max(value, lo), hi))


# ---------------------------------------------------------------------------
# Cached API fetch – one call per variable, all states at once
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_state_finances(year: int = 2023) -> dict | None:
    """Fetch all needed financial variables for all states from Census Bureau.

    Returns dict keyed by FIPS code (zero-padded string):
        {fips: {"revenue": int, "expenditure": int, ...}, ...}
    All amounts are converted to actual dollars (multiplied by 1000).
    Returns None if any critical API call fails.
    """
    # Initialise result dict for every state
    result: dict[str, dict] = {}
    for fips in STATES:
        result[fips] = {}

    for idx, (field, agg_code) in enumerate(_VARIABLE_CODES.items()):
        # Respect Census API rate limits – pause between calls
        if idx > 0:
            time.sleep(3)

        url = (
            f"{CENSUS_BASE}"
            f"?get=NAME,AMOUNT"
            f"&for=state:*"
            f"&time={year}"
            f"&SVY_COMP=04"
            f"&AGG_DESC={agg_code}"
            f"&GOVTYPE=002"
        )

        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            rows = r.json()
        except Exception:
            return None

        # First row is headers: ["NAME", "AMOUNT", "time", "state"]
        if not rows or len(rows) < 2:
            return None

        for row in rows[1:]:
            # row layout: [name, amount, time, state_fips]
            state_fips = row[-1].zfill(2)
            if state_fips not in STATES:
                continue  # skip DC and territories
            try:
                amount = int(float(row[1])) * 1000  # thousands -> actual dollars
            except (ValueError, TypeError):
                amount = 0
            result[state_fips][field] = amount

    # Validate that every state got all fields
    expected_fields = set(_VARIABLE_CODES.keys())
    for fips in list(result.keys()):
        if set(result[fips].keys()) != expected_fields:
            # Missing data for this state – fill zeros so scoring still works
            for f in expected_fields:
                result[fips].setdefault(f, 0)

    return result


# ---------------------------------------------------------------------------
# Score a single state
# ---------------------------------------------------------------------------

def score_state(fips_code: str, finances: dict | None = None) -> dict | None:
    """Compute 5-axis score (0-1000) for one state.

    Parameters
    ----------
    fips_code : str
        Two-digit FIPS code (zero-padded).
    finances : dict, optional
        Pre-fetched finance data from fetch_state_finances(). If None, it will
        be fetched (cached).

    Returns dict with axes scores and raw financials, or None on failure.
    """
    if fips_code not in STATES:
        return None

    if finances is None:
        finances = fetch_state_finances()
    if finances is None:
        return None

    data = finances.get(fips_code)
    if data is None:
        return None

    revenue = data.get("revenue", 0)
    expenditure = data.get("expenditure", 0)
    debt = data.get("debt", 0)
    federal_rev = data.get("federal_revenue", 0)
    interest = data.get("interest", 0)
    capital = data.get("capital_outlay", 0)
    cash = data.get("cash_holdings", 0)
    taxes = data.get("taxes", 0)

    # --- Axis 1: Budget Balance (200) ---
    if revenue > 0:
        ratio = (revenue - expenditure) / revenue
        if ratio >= 0:
            ax1 = _clamp(100 + ratio * 500, 0, 200)
        else:
            ax1 = _clamp(100 + ratio * 300, 0, 200)
    else:
        ax1 = 0

    # --- Axis 2: Debt Burden (200) ---
    if revenue > 0:
        debt_ratio = debt / revenue
        ax2 = _clamp(200 - debt_ratio * 100, 0, 200)
    else:
        ax2 = 0

    # --- Axis 3: Revenue Independence (200) ---
    if revenue > 0:
        fed_dep = federal_rev / revenue
        ax3 = _clamp(200 - fed_dep * 400, 0, 200)
    else:
        ax3 = 0

    # --- Axis 4: Spending Efficiency (200) ---
    if expenditure > 0:
        int_ratio = interest / expenditure
        cap_ratio = capital / expenditure
        ax4 = _clamp((1 - int_ratio) * 120 + cap_ratio * 400, 0, 200)
    else:
        ax4 = 0

    # --- Axis 5: Fiscal Reserve (200) ---
    if expenditure > 0:
        reserve_ratio = cash / expenditure
        ax5 = _clamp(reserve_ratio * 100, 0, 200)
    else:
        ax5 = 0

    axes = {
        "Budget Balance": ax1,
        "Debt Burden": ax2,
        "Revenue Independence": ax3,
        "Spending Efficiency": ax4,
        "Fiscal Reserve": ax5,
    }

    state_info = STATES[fips_code]
    return {
        "name": state_info["name"],
        "abbr": state_info["abbr"],
        "fips": fips_code,
        "axes": axes,
        "total": sum(axes.values()),
        "revenue": revenue,
        "expenditure": expenditure,
        "debt": debt,
        "federal_revenue": federal_rev,
        "interest": interest,
        "capital_outlay": capital,
        "cash_holdings": cash,
        "taxes": taxes,
    }


# ---------------------------------------------------------------------------
# Score all 50 states
# ---------------------------------------------------------------------------

def score_all_states(year: int = 2023) -> list[dict] | None:
    """Score all 50 states, return list sorted by total score descending.

    Finance data is fetched once (cached), then scoring runs in-memory
    for each state – no need for ThreadPoolExecutor since the work is
    purely computational after the single cached fetch.
    """
    finances = fetch_state_finances(year)
    if finances is None:
        return None

    results = []
    for fips in STATES:
        scored = score_state(fips, finances)
        if scored is not None:
            results.append(scored)

    results.sort(key=lambda s: s["total"], reverse=True)
    return results
