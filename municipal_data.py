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
    "Fiscal Capacity",
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
    "Fiscal Capacity": (
        "Revenue per capita as a proxy for fiscal capacity. "
        "score = revenue_per_capita / 50, clamped 0-200. "
        "$10,000/capita = 200 points."
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


def score_municipality(gov_id: str, pid_data: dict, finance_data: dict) -> dict | None:
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

    # Axis 5: Fiscal Capacity (200)
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
        "Fiscal Capacity": round(ax5, 1),
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


def score_county(gov_id: str, pid_data: dict, finance_data: dict) -> dict | None:
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

    # Axis 5: Fiscal Capacity (200)
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
        "Fiscal Capacity": round(ax5, 1),
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

    scored = []
    for gov_id in pid_data:
        result = score_county(gov_id, pid_data, finance_data)
        if result is not None and result["total"] > 0:
            scored.append(result)

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
