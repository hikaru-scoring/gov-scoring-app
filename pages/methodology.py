# pages/methodology.py
"""GOV-1000 Scoring Methodology documentation page."""
import streamlit as st

st.set_page_config(page_title="GOV-1000 Methodology", layout="wide")

st.markdown("""
<style>
.block-container { max-width: 900px; padding-top: 2rem; font-family: 'Inter', sans-serif; }
</style>
""", unsafe_allow_html=True)

st.markdown("# GOV-1000 Scoring Methodology")

st.markdown("""
## Overview

GOV-1000 scores 15 major US federal agencies on a **1000-point scale** across **5 dimensions**
(200 points each). All financial data is sourced from **USASpending.gov**, the official US
government spending transparency platform.

---

## Scoring Axes

### 1. Budget Efficiency (0-200)
Measures how effectively an agency executes its allocated budget.

| Metric | Points | Formula |
|--------|--------|---------|
| Outlay Ratio | 0-120 | `(outlay / budget_authority) * 120` |
| Obligation Ratio | 0-80 | `(obligated / budget_authority) * 80` |

**Data Source:** USASpending.gov `/api/v2/references/toptier_agencies/`

Higher outlay and obligation ratios indicate better budget execution.

---

### 2. Transparency (0-200)
Measures how openly and completely an agency reports its data.

| Metric | Points | Formula |
|--------|--------|---------|
| Congressional Justification | 0-60 | 60 if URL exists, 0 otherwise |
| Sub-agency Reporting | 0-80 | `min(subtier_count * 5, 80)` |
| Data Completeness | 0-60 | `(non_null_fields / total_fields) * 60` |

**Data Source:** USASpending.gov `/api/v2/agency/{code}/`

Agencies that publish congressional justifications and have more sub-agencies actively reporting
receive higher transparency scores.

---

### 3. Performance (0-200)
Measures operational throughput and activity volume.

| Metric | Points | Formula |
|--------|--------|---------|
| Transaction Volume | 0-120 | `min(total_transactions / 50000 * 100, 120)` |
| Award Activity | 0-80 | `min(new_awards / 10000 * 80, 80)` |

**Data Source:** USASpending.gov `/api/v2/agency/{code}/sub_agency/`

Higher transaction counts and new award volumes indicate more active procurement and operations.

---

### 4. Fiscal Discipline (0-200)
Measures how responsibly an agency manages its budget growth and unobligated balances.

| Metric | Points | Formula |
|--------|--------|---------|
| Budget Growth Rate | 0-150 | `max(0, 150 - abs(yoy_growth) * 5)` |
| Unobligated Balance | 0-50 | `max(0, 50 - unobligated_ratio * 100)` |

**Data Source:** USASpending.gov `/api/v2/agency/{code}/budgetary_resources/`

Agencies with stable budgets (low year-over-year growth) and minimal unobligated balances
score higher on fiscal discipline.

---

### 5. Accountability (0-200)
Measures oversight compliance based on GAO (Government Accountability Office) audit findings.

| Metric | Points | Formula |
|--------|--------|---------|
| GAO Findings | 0-200 | `max(0, 200 - finding_count * 20)` |

**Data Source:** Manually curated from GAO reports (`gao_findings.json`)

Fewer audit findings indicate stronger accountability. Each finding reduces the score by 20 points.

---

## Score Grades

| Grade | Score Range | Interpretation |
|-------|-----------|----------------|
| A | 800-1000 | Strong across most dimensions |
| B | 600-799 | Solid, with some areas to watch |
| C | 400-599 | Mixed signals - warrants deeper analysis |
| D | 0-399 | Significant weaknesses in multiple areas |

---

## Data Update Frequency

- **USASpending.gov**: Updated quarterly by fiscal year
- **GAO Findings**: Updated periodically (manual curation)
- **Score History**: Recorded daily via GitHub Actions

---

## Limitations

1. **Budget execution timing**: Early in a fiscal year, outlay ratios may appear low simply
   because spending hasn't occurred yet, not because of poor execution.
2. **GAO data**: Currently manually curated. Automated GAO data integration is planned for v2.
3. **Performance metrics**: Transaction counts favor larger agencies. This is a known limitation
   and may be normalized by agency size in future versions.
4. **Quarterly updates**: USASpending.gov data updates quarterly, so daily score changes reflect
   quarterly data refreshes rather than real-time changes.
""")
