# app.py
"""GOV-1000 — US Government Agency Scoring Platform."""
import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_logic import AGENCIES, AXES_LABELS, score_agency, fetch_agency_budgetary_resources
from state_data import STATES, STATE_AXES_LABELS, STATE_LOGIC_DESC, score_all_states, score_state, fetch_state_finances, fetch_state_score_history
from ui_components import inject_css, render_radar_chart
from pdf_report import generate_pdf

APP_TITLE = "GOV-1000 — Agency Scoring Platform"
st.set_page_config(page_title=APP_TITLE, layout="wide")

# ---------------------------------------------------------------------------
# Score history
# ---------------------------------------------------------------------------
SCORES_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "scores_history.json")


def _load_scores_history() -> dict:
    if os.path.exists(SCORES_HISTORY_FILE):
        with open(SCORES_HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}


def render_score_delta(asset_name: str, current_total: int):
    history = _load_scores_history()
    if not history:
        return
    dates = sorted(history.keys(), reverse=True)
    prev_score = None
    for d in dates:
        s = history[d].get(asset_name)
        if s is not None:
            prev_score = s
            break
    if prev_score is None:
        return
    delta = current_total - prev_score
    if delta > 0:
        color, arrow = "#10b981", "&#9650;"
    elif delta < 0:
        color, arrow = "#ef4444", "&#9660;"
    else:
        color, arrow = "#94a3b8", "&#9644;"
    st.markdown(
        f'<div style="text-align:center; font-size:1.1em; font-weight:700; color:{color}; margin-top:-8px; margin-bottom:10px;">'
        f'{arrow} {delta:+d} from last record ({prev_score})'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_daily_score_tracker(asset_name: str):
    history = _load_scores_history()
    if not history:
        st.caption("No daily score records yet.")
        return
    dates = sorted(history.keys())
    values, valid_dates = [], []
    for d in dates:
        score = history[d].get(asset_name)
        if score is not None:
            valid_dates.append(d)
            values.append(score)
    if len(valid_dates) < 2:
        st.caption(f"Not enough daily records for {asset_name} yet.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=valid_dates, y=values, mode='lines+markers', name=asset_name,
        line=dict(color='#2E7BE6', width=2), marker=dict(size=5),
        fill='tozeroy', fillcolor='rgba(46,123,230,0.05)',
    ))
    fig.update_layout(
        yaxis=dict(range=[0, 1000], title="Score"), height=250,
        margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='white',
        hovermode="x unified", clickmode='none', dragmode=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def generate_csv(data: dict, axes_labels: list, logic_descriptions: dict = None,
                 snapshot: dict = None) -> bytes:
    rows = []
    for k in axes_labels:
        desc = logic_descriptions.get(k, "") if logic_descriptions else ""
        rows.append({"Axis": k, "Score": int(data["axes"].get(k, 0)), "Description": desc})
    rows.append({"Axis": "TOTAL", "Score": int(data.get("total", 0)), "Description": ""})
    if snapshot:
        rows.append({"Axis": "", "Score": "", "Description": ""})
        for label, value in snapshot.items():
            rows.append({"Axis": label, "Score": value, "Description": ""})
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Logic descriptions for each axis
# ---------------------------------------------------------------------------
LOGIC_DESC = {
    "Budget Efficiency": "Outlay-to-budget x Obligation-to-budget ratios",
    "Transparency": "Justification x Reporting x Data quality",
    "Performance": "Transaction volume x New award count",
    "Fiscal Discipline": "YoY budget growth x Unobligated balance ratio",
    "Accountability": "GAO audit findings (fewer = higher score)",
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_gov_news(query: str, max_items: int = 5) -> list[dict]:
    """Fetch news from Google News RSS for a given query."""
    try:
        url = f"https://news.google.com/rss/search?q={quote(query)}+US+government&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = root.findall(".//item")
        results = []
        for item in items[:max_items]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            source = item.findtext("source", "")
            pub_date = item.findtext("pubDate", "")
            if pub_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    pub_str = dt.strftime("%b %d, %Y")
                except Exception:
                    pub_str = pub_date[:16]
            else:
                pub_str = ""
            if title and link:
                results.append({"title": title, "link": link, "publisher": source, "date": pub_str})
        return results
    except Exception:
        return []


def _fmt_budget(amount: float) -> str:
    """Format large dollar amounts."""
    if amount >= 1e12:
        return f"${amount / 1e12:.1f}T"
    if amount >= 1e9:
        return f"${amount / 1e9:.1f}B"
    if amount >= 1e6:
        return f"${amount / 1e6:.0f}M"
    return f"${amount:,.0f}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # CSS (exact copy from FRS-1000)
    inject_css()
    st.markdown("""
    <style>
    .block-container { padding-top: 1rem !important; }
    header[data-testid="stHeader"] { display: none !important; }
    footer { display: none !important; }
    #MainMenu { display: none !important; }
    .viewerBadge_container__r5tak { display: none !important; }
    .styles_viewerBadge__CvC9N { display: none !important; }
    [data-testid="stActionButtonIcon"] { display: none !important; }
    [data-testid="manage-app-button"] { display: none !important; }
    a[href*="github.com"] img { display: none !important; }
    div[class*="viewerBadge"] { display: none !important; }
    div[class*="StatusWidget"] { display: none !important; }
    div[data-testid="stStatusWidget"] { display: none !important; }
    iframe[title="streamlit_lottie.streamlit_lottie"] { display: none !important; }
    .stDeployButton { display: none !important; }
    div[class*="stToolbar"] { display: none !important; }
    div.embeddedAppMetaInfoBar_container__DxxL1 { display: none !important; }
    div[class*="embeddedAppMetaInfoBar"] { display: none !important; }
    </style>
    """, unsafe_allow_html=True)

    # Session state
    if "saved_agency_data" not in st.session_state:
        st.session_state.saved_agency_data = None

    # Sidebar
    with st.sidebar:
        st.markdown("**PDF White Label**")
        company_name = st.text_input("Company name for PDF", value="",
                                     placeholder="e.g. Acme Research LLC", key="wl_company")

    # Tabs
    if "saved_state_data" not in st.session_state:
        st.session_state.saved_state_data = None

    tab_dash, tab_agency, tab_states, tab_rankings = st.tabs(["Dashboard", "Agency Detail", "State Scores", "Rankings"])

    # ===================================================================
    # DASHBOARD TAB
    # ===================================================================
    with tab_dash:
        st.markdown(
            "<div style='font-size:1.5em; font-weight:900; color:#1e3a8a; margin-bottom:5px;'>"
            "Government Dashboard</div>"
            "<p style='color:#64748b; margin-bottom:20px;'>"
            "Real-time overview of 15 major US federal agencies scored on a 1000-point scale.</p>",
            unsafe_allow_html=True,
        )

        with st.expander("How to use GOV-1000"):
            st.markdown("""
**Explore Scores** — Browse 15 major US federal agencies scored across 5 dimensions: Budget Efficiency, Transparency, Performance, Fiscal Discipline, and Accountability.

**Compare Agencies** — Use the Agency Detail tab to deep-dive into individual agencies and compare them side-by-side using Save/Clear.

**Export Reports** — Download PDF or CSV reports for any agency.

**Data Source** — All financial data comes from USASpending.gov (public, no authentication required). GAO audit findings are manually curated.
""")

        # Parallel fetch all agencies
        all_scores = []
        with st.spinner("Loading agency data from USASpending.gov..."):
            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = {}
                for code in AGENCIES:
                    f = executor.submit(score_agency, code)
                    futures[f] = code
                for future in as_completed(futures):
                    try:
                        d = future.result()
                        if d:
                            all_scores.append(d)
                    except Exception:
                        pass

        if all_scores:
            # Sort by total score descending
            all_scores.sort(key=lambda x: x["total"], reverse=True)

            # Government Health Score
            avg_score = int(sum(s["total"] for s in all_scores) / len(all_scores))
            if avg_score >= 700:
                health_color, health_label = "#10b981", "STRONG"
            elif avg_score >= 500:
                health_color, health_label = "#2E7BE6", "MODERATE"
            else:
                health_color, health_label = "#ef4444", "WEAK"

            st.markdown(
                f"""<div style="text-align:center; padding:25px; background:linear-gradient(135deg, #f8fafc, #e2e8f0);
                border-radius:20px; margin-bottom:25px;">
                <div style="font-size:0.9em; color:#64748b; font-weight:700; letter-spacing:2px;">
                GOVERNMENT HEALTH SCORE</div>
                <div style="font-size:4em; font-weight:900; color:{health_color}; line-height:1.1;">
                {avg_score}</div>
                <div style="font-size:1em; font-weight:700; color:{health_color};">{health_label}</div>
                <div style="font-size:0.8em; color:#94a3b8; margin-top:5px;">
                Average across {len(all_scores)} agencies</div>
                </div>""",
                unsafe_allow_html=True,
            )

            # Top / Bottom movers (from history — styled like FRS-1000)
            history = _load_scores_history()
            if history:
                dates = sorted(history.keys(), reverse=True)
                if dates:
                    prev = history[dates[0]]
                    deltas = []
                    for s in all_scores:
                        prev_score = prev.get(s["name"])
                        if prev_score is not None:
                            deltas.append({"name": s["abbr"], "delta": s["total"] - prev_score})
                    if deltas and any(d["delta"] != 0 for d in deltas):
                        deltas.sort(key=lambda x: x["delta"], reverse=True)
                        top_movers = deltas[:3]
                        bottom_movers = sorted(deltas, key=lambda x: x["delta"])[:3]

                        mv1, mv2 = st.columns(2)
                        with mv1:
                            st.markdown("<div style='font-size:1em; font-weight:700; color:#10b981; margin-bottom:10px;'>Top Movers</div>", unsafe_allow_html=True)
                            for m in top_movers:
                                if m["delta"] > 0:
                                    st.markdown(f"""
                                    <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:#f0fdf4; border-radius:8px; margin-bottom:6px;">
                                        <span style="font-weight:600; color:#1e293b;">{m['name']}</span>
                                        <span style="font-weight:700; color:#10b981;">&#9650; {m['delta']:+d}</span>
                                    </div>
                                    """, unsafe_allow_html=True)
                        with mv2:
                            st.markdown("<div style='font-size:1em; font-weight:700; color:#ef4444; margin-bottom:10px;'>Bottom Movers</div>", unsafe_allow_html=True)
                            for m in bottom_movers:
                                if m["delta"] < 0:
                                    st.markdown(f"""
                                    <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:#fef2f2; border-radius:8px; margin-bottom:6px;">
                                        <span style="font-weight:600; color:#1e293b;">{m['name']}</span>
                                        <span style="font-weight:700; color:#ef4444;">&#9660; {m['delta']:+d}</span>
                                    </div>
                                    """, unsafe_allow_html=True)

                        st.markdown("<div style='height:15px;'></div>", unsafe_allow_html=True)

            # Agency cards grid
            st.markdown("<div class='section-title'>All Agencies</div>",
                        unsafe_allow_html=True)

            cols = st.columns(3)
            for i, s in enumerate(all_scores):
                score = s["total"]
                if score >= 700:
                    border_color = "#10b981"
                elif score >= 500:
                    border_color = "#2E7BE6"
                elif score >= 300:
                    border_color = "#f59e0b"
                else:
                    border_color = "#ef4444"

                with cols[i % 3]:
                    st.markdown(
                        f"""<div style="background:#fff; border-radius:12px; padding:18px; margin-bottom:12px;
                        border-left:4px solid {border_color}; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
                        <div style="font-size:0.75em; color:#94a3b8; font-weight:600;">{s['abbr']}</div>
                        <div style="font-size:0.95em; font-weight:700; color:#1e293b; margin:2px 0;">
                        {s['name']}</div>
                        <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <span style="font-size:1.8em; font-weight:900; color:{border_color};">{score}</span>
                        <span style="font-size:0.8em; color:#94a3b8;">{_fmt_budget(s['budget_authority'])}</span>
                        </div></div>""",
                        unsafe_allow_html=True,
                    )
        else:
            st.error("Failed to load agency data. Please check your internet connection.")

        # --- State Scores Map ---
        st.markdown("<div class='section-title' style='margin-top:40px;'>State Fiscal Health Map (2023)</div>",
                    unsafe_allow_html=True)

        with st.spinner("Loading state data from Census Bureau..."):
            finances_data = fetch_state_finances()
            state_scores = score_all_states() if finances_data else None

        if state_scores:
            # Build score lookup for text labels
            score_by_abbr = {s["abbr"]: s["total"] for s in state_scores}

            abbrs = [s["abbr"] for s in state_scores]
            totals = [s["total"] for s in state_scores]
            hovers = [f"{s['name']} ({s['abbr']})" for s in state_scores]

            fig_map = go.Figure()

            # Choropleth layer
            fig_map.add_trace(go.Choropleth(
                locations=abbrs,
                locationmode="USA-states",
                z=totals,
                colorscale=[
                    [0.0, "#ef4444"],
                    [0.1, "#f97316"],
                    [0.2, "#f59e0b"],
                    [0.33, "#eab308"],
                    [0.34, "#93c5fd"],
                    [0.45, "#60a5fa"],
                    [0.55, "#3b82f6"],
                    [0.66, "#2563eb"],
                    [0.67, "#34d399"],
                    [0.8, "#10b981"],
                    [0.9, "#059669"],
                    [1.0, "#047857"],
                ],
                zmin=300, zmax=900,
                text=hovers,
                hoverinfo="text",
                colorbar=dict(title="Score", tickvals=[300, 500, 700, 900], len=0.6),
            ))

            # Score text on each state
            lats = [STATES[s["fips"]]["lat"] for s in state_scores]
            lons = [STATES[s["fips"]]["lon"] + 1.5 for s in state_scores]
            labels = [str(s["total"]) for s in state_scores]

            fig_map.add_trace(go.Scattergeo(
                locationmode="USA-states",
                lat=lats,
                lon=lons,
                text=labels,
                mode="markers+text",
                marker=dict(
                    size=32,
                    color="white",
                    opacity=0.85,
                    line=dict(width=0),
                ),
                textfont=dict(size=10, color="#1e293b", family="Arial Black"),
                hoverinfo="skip",
                showlegend=False,
            ))

            fig_map.update_layout(
                geo=dict(
                    scope="usa",
                    bgcolor="rgba(0,0,0,0)",
                    lakecolor="white",
                ),
                margin=dict(l=0, r=0, t=0, b=0),
                height=500,
                paper_bgcolor="white",
                dragmode=False,
            )
            # Capture click events on map
            map_event = st.plotly_chart(fig_map, use_container_width=True, config={
                "displayModeBar": False,
                "scrollZoom": False,
                "doubleClick": False,
            }, on_select="rerun", key="state_map")

            # Check if a state was clicked
            clicked_fips = None
            if map_event and map_event.selection and map_event.selection.points:
                pt = map_event.selection.points[0]
                # Choropleth click returns location (abbr)
                clicked_abbr = pt.get("location", None)
                if clicked_abbr:
                    for fips, info in STATES.items():
                        if info["abbr"] == clicked_abbr:
                            clicked_fips = fips
                            break

            if clicked_fips:
                clicked_data = score_state(clicked_fips, finances_data)
                if clicked_data:
                    st.markdown(f"""
                    <div style="text-align:center; margin:10px 0;">
                        <div style="font-size:14px; letter-spacing:2px; color:#666;">
                            {clicked_data['name']} ({clicked_data['abbr']})
                        </div>
                        <div style="font-size:70px; font-weight:800; color:#2E7BE6; line-height:1;">
                            {clicked_data['total']}
                            <span style="font-size:28px; color:#BBB;">/ 1000</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Radar + Metrics inline
                    cl_left, cl_right = st.columns([1.5, 1])
                    with cl_left:
                        st.markdown("<div style='font-size:1.1em; font-weight:bold; color:#333; margin-bottom:5px;'>Intelligence Radar</div>", unsafe_allow_html=True)
                        fig_cl = render_radar_chart(clicked_data, None, STATE_AXES_LABELS)
                        st.plotly_chart(fig_cl, use_container_width=True, config={"displayModeBar": False})
                    with cl_right:
                        st.markdown(
                            "<div style='font-size:0.9em; font-weight:bold; color:#333; margin-bottom:15px; border-left:3px solid #2E7BE6; padding-left:8px;'>SCORE METRICS</div>",
                            unsafe_allow_html=True
                        )
                        for axis in STATE_AXES_LABELS:
                            v = clicked_data["axes"][axis]
                            desc = STATE_LOGIC_DESC.get(axis, "")
                            st.markdown(f"""
                            <div style="background:#fff; padding:16px; border-radius:12px; margin-bottom:10px;
                                border:1px solid #e0e0e0; border-left:8px solid #2E7BE6; box-shadow:2px 2px 5px rgba(0,0,0,0.07);">
                                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                                    <span style="font-size:1.3em; font-weight:800; color:#333;">{axis}</span>
                                    <span style="font-size:1.7em; font-weight:900; color:#2E7BE6;">{int(v)}</span>
                                </div>
                                <p style="font-size:1em; color:#777; margin:0;">{desc}</p>
                            </div>""", unsafe_allow_html=True)

                    # Fiscal Snapshot
                    fs1, fs2, fs3, fs4 = st.columns(4)
                    for col, label, val in [
                        (fs1, "REVENUE", _fmt_budget(clicked_data["revenue"])),
                        (fs2, "EXPENDITURE", _fmt_budget(clicked_data["expenditure"])),
                        (fs3, "DEBT", _fmt_budget(clicked_data["debt"])),
                        (fs4, "RESERVES", _fmt_budget(clicked_data["cash_holdings"])),
                    ]:
                        col.markdown(f"""
                        <div style="background:#fff; padding:16px; border-radius:12px; text-align:center;
                            border:1px solid #e2e8f0; box-shadow:2px 2px 5px rgba(0,0,0,0.04);">
                            <div style="font-size:0.7em; font-weight:700; color:#94a3b8; letter-spacing:1px;">{label}</div>
                            <div style="font-size:1.5em; font-weight:900; color:#2E7BE6; line-height:1.3;">{val}</div>
                        </div>""", unsafe_allow_html=True)

                    st.markdown("---")

            # All states ranking
            st.markdown("<div style='font-size:1em; font-weight:700; color:#1e3a8a; margin-top:20px; margin-bottom:10px;'>ALL STATES</div>", unsafe_allow_html=True)
            st_cols = st.columns(3)
            for idx, s in enumerate(state_scores):
                score = s["total"]
                if score >= 700:
                    border_color = "#10b981"
                elif score >= 500:
                    border_color = "#2E7BE6"
                elif score >= 300:
                    border_color = "#f59e0b"
                else:
                    border_color = "#ef4444"
                with st_cols[idx % 3]:
                    st.markdown(
                        f"""<div style="background:#fff; border-radius:12px; padding:14px; margin-bottom:10px;
                        border-left:4px solid {border_color}; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
                        <div style="font-size:0.75em; color:#94a3b8; font-weight:600;">{s['abbr']}</div>
                        <div style="font-size:0.95em; font-weight:700; color:#1e293b; margin:2px 0;">
                        {s['name']}</div>
                        <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <span style="font-size:1.8em; font-weight:900; color:{border_color};">{score}</span>
                        <span style="font-size:0.8em; color:#94a3b8;">{_fmt_budget(s['revenue'])}</span>
                        </div></div>""",
                        unsafe_allow_html=True,
                    )
        else:
            st.warning("Could not load state data from Census Bureau.")

    # ===================================================================
    # AGENCY DETAIL TAB
    # ===================================================================
    with tab_agency:
        # Agency selector
        agency_options = {f"{v['abbr']} — {v['name']}": k for k, v in AGENCIES.items()}
        selected_label = st.selectbox("Select Agency", list(agency_options.keys()))
        selected_code = agency_options[selected_label]

        with st.spinner("Loading agency data..."):
            data = score_agency(selected_code)

        if data:
            total = data["total"]

            snapshot = {
                "Budget Authority": _fmt_budget(data["budget_authority"]),
                "Obligated": _fmt_budget(data["obligated"]),
                "Outlays": _fmt_budget(data["outlay"]),
                "% of Federal Budget": f"{data['pct_of_total']:.1f}%",
            }

            # --- Buttons row (matches FRS-1000: Save | Clear | PDF | CSV) ---
            col_btn1, col_btn2, col_btn3, col_btn4, col_btn_rest = st.columns([1, 1, 1.5, 1.5, 5.5])
            with col_btn1:
                save_it = st.button("Save")
            with col_btn2:
                clear_it = st.button("Clear")
            with col_btn3:
                pdf_bytes = generate_pdf(data, AXES_LABELS, "Federal Agency",
                                         LOGIC_DESC, snapshot, company_name)
                st.download_button("PDF", pdf_bytes,
                                   file_name=f"GOV1000_{data['abbr']}.pdf",
                                   mime="application/pdf")
            with col_btn4:
                csv_bytes = generate_csv(data, AXES_LABELS, LOGIC_DESC, snapshot)
                st.download_button("CSV", csv_bytes,
                                   file_name=f"GOV1000_{data['abbr']}.csv",
                                   mime="text/csv")

            if save_it:
                st.session_state.saved_agency_data = data
                st.rerun()
            if clear_it:
                st.session_state.saved_agency_data = None
                st.rerun()

            # --- Total Score ---
            st.markdown(f"""
            <div style="text-align:center; margin-top:4px; margin-bottom:10px;">
                <div style="font-size:14px; letter-spacing:2px; color:#666;">TOTAL SCORE</div>
                <div style="font-size:90px; font-weight:800; color:#2E7BE6; line-height:1;">
                    {total}
                    <span style="font-size:35px; color:#BBB;">/ 1000</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            render_score_delta(data["name"], total)

            # --- I. Radar + II. Score Metrics ---
            col_radar, col_axes = st.columns([1.5, 1])

            with col_radar:
                st.markdown("<div style='font-size: 1.1em; font-weight: bold; color: #333; margin-top: -10px; margin-bottom: 5px;'>I. Intelligence Radar</div>", unsafe_allow_html=True)
                fig = render_radar_chart(data, st.session_state.saved_agency_data, AXES_LABELS)
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            with col_axes:
                st.markdown(
                    "<div style='font-size: 0.9em; font-weight: bold; color: #333; margin-top: -10px; margin-bottom: 15px; border-left: 3px solid #2E7BE6; padding-left: 8px;'>II. ANALYSIS SCORE METRICS</div>",
                    unsafe_allow_html=True
                )

                saved = st.session_state.saved_agency_data
                for axis in AXES_LABELS:
                    v1 = data["axes"][axis]
                    v2 = saved["axes"].get(axis, 0) if saved else None
                    desc_text = LOGIC_DESC.get(axis, "")

                    score_html = f'<span style="color: #2E7BE6;">{int(v1)}</span>'
                    if v2 is not None:
                        score_html += f' <span style="color: #ccc; font-size: 0.9em; font-weight:bold; margin: 0 6px;">vs</span> <span style="color: #F4A261;">{int(v2)}</span>'

                    st.markdown(
                        f"""
                        <div style="
                            background-color: #FFFFFF;
                            padding: 20px;
                            border-radius: 12px;
                            margin-bottom: 12px;
                            border: 1px solid #E0E0E0;
                            border-left: 8px solid #2E7BE6;
                            box-shadow: 2px 2px 5px rgba(0,0,0,0.07);
                        ">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <span style="font-size: 1.4em; font-weight: 800; color: #333333;">{axis}</span>
                                <span style="font-size: 1.9em; font-weight: 900; line-height: 1;">{score_html}</span>
                            </div>
                            <p style="font-size: 1.05em; color: #777777; margin: 0; line-height: 1.3; font-weight: 500;">{desc_text}</p>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            # --- III. Budget Snapshot (styled cards like FRS-1000) ---
            st.markdown("<div class='section-title'>III. Budget Snapshot</div>",
                        unsafe_allow_html=True)
            bs1, bs2, bs3, bs4 = st.columns(4)
            budget_items = [
                (bs1, "BUDGET AUTHORITY", _fmt_budget(data["budget_authority"])),
                (bs2, "OBLIGATED", _fmt_budget(data["obligated"])),
                (bs3, "OUTLAYS", _fmt_budget(data["outlay"])),
                (bs4, "% OF FEDERAL BUDGET", f"{data['pct_of_total']:.1f}%"),
            ]
            for col, label, value in budget_items:
                col.markdown(f"""
                <div style="background:#fff; padding:20px; border-radius:12px; text-align:center; border:1px solid #e2e8f0; box-shadow:2px 2px 5px rgba(0,0,0,0.04);">
                    <div style="font-size:0.7em; font-weight:700; color:#94a3b8; letter-spacing:1px;">{label}</div>
                    <div style="font-size:1.8em; font-weight:900; color:#2E7BE6; line-height:1.3;">{value}</div>
                </div>
                """, unsafe_allow_html=True)

            # --- IV. Budget History ---
            st.markdown("<div class='section-title'>IV. Budget History</div>",
                        unsafe_allow_html=True)
            budget_history = fetch_agency_budgetary_resources(selected_code)
            if budget_history:
                sorted_bh = sorted(budget_history, key=lambda x: x.get("fiscal_year", 0))
                bh_years = [h["fiscal_year"] for h in sorted_bh if h.get("fiscal_year")]
                bh_budget = [h.get("agency_budgetary_resources", 0) for h in sorted_bh if h.get("fiscal_year")]
                bh_obligated = [h.get("agency_total_obligated", 0) for h in sorted_bh if h.get("fiscal_year")]

                fig_bh = go.Figure()
                fig_bh.add_trace(go.Scatter(
                    x=bh_years, y=bh_budget,
                    mode="lines+markers", name="Budget Authority",
                    line=dict(color="#2E7BE6", width=3),
                    marker=dict(size=8),
                ))
                fig_bh.add_trace(go.Scatter(
                    x=bh_years, y=bh_obligated,
                    mode="lines+markers", name="Obligated",
                    line=dict(color="#10b981", width=3),
                    marker=dict(size=8),
                ))
                fig_bh.update_layout(
                    height=350,
                    margin=dict(l=0, r=0, t=10, b=0),
                    plot_bgcolor="white",
                    yaxis=dict(gridcolor="#f0f0f0", tickformat="$.2s"),
                    xaxis=dict(gridcolor="#f0f0f0", dtick=1),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    dragmode=False,
                )
                st.plotly_chart(fig_bh, use_container_width=True, config={"displayModeBar": False})

            # --- V. Daily Score Tracker ---
            st.markdown("<div class='section-title'>V. Daily Score Tracker</div>",
                        unsafe_allow_html=True)
            render_daily_score_tracker(data["name"])

            # --- VI. Score Comparison ---
            st.markdown("<div class='section-title'>VI. Score Comparison</div>",
                        unsafe_allow_html=True)

            sc1, sc2, sc3 = st.columns(3)
            t1 = int(data.get("total", 0))
            t_html = f'<span style="color:#2E7BE6;">{t1}</span>'
            saved = st.session_state.saved_agency_data
            if saved:
                t2 = int(saved.get("total", 0))
                t_html += f' <span style="font-size:0.5em; color:#666;">vs</span> <span style="color:#F4A261;">{t2}</span>'
            sc1.markdown(f'<div class="card"><div style="font-size:11px; color:#999;">TOTAL SCORE</div><div style="font-size:22px; font-weight:900;">{t_html}</div></div>', unsafe_allow_html=True)

            axes1 = data.get("axes", {})
            best1 = max(axes1, key=axes1.get) if axes1 else "N/A"
            best1_val = int(axes1.get(best1, 0))
            sc2.markdown(f'<div class="card"><div style="font-size:11px; color:#999;">STRONGEST</div><div style="font-size:18px; font-weight:900;"><span style="color:#2E7BE6;">{best1} ({best1_val})</span></div></div>', unsafe_allow_html=True)

            worst1 = min(axes1, key=axes1.get) if axes1 else "N/A"
            worst1_val = int(axes1.get(worst1, 0))
            sc3.markdown(f'<div class="card"><div style="font-size:11px; color:#999;">WEAKEST</div><div style="font-size:18px; font-weight:900;"><span style="color:#ef4444;">{worst1} ({worst1_val})</span></div></div>', unsafe_allow_html=True)

            # --- VII. Latest News ---
            st.markdown("<div class='section-title'>VII. Latest News</div>", unsafe_allow_html=True)
            news_items = fetch_gov_news(data["name"])
            if news_items:
                for item in news_items:
                    st.markdown(
                        f'<div style="padding:10px 0; border-bottom:1px solid #F0F0F0;">'
                        f'<a href="{item["link"]}" target="_blank" style="font-size:0.95em; font-weight:600; color:#1e3a8a; text-decoration:none;">{item["title"]}</a>'
                        f'<div style="font-size:0.8em; color:#999; margin-top:3px;">{item["publisher"]} · {item["date"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.caption("No recent news available.")
        else:
            st.error("Failed to load agency data.")

    # ===================================================================
    # STATE SCORES TAB
    # ===================================================================
    with tab_states:
        st.markdown(
            "<div class='company-header'>State Scores</div>",
            unsafe_allow_html=True
        )

        state_options = {f"{v['abbr']} — {v['name']}": k for k, v in STATES.items()}
        selected_state_label = st.selectbox("Select State", list(state_options.keys()))
        selected_fips = state_options[selected_state_label]

        with st.spinner("Loading state data..."):
            finances = fetch_state_finances()
            state_data = score_state(selected_fips, finances) if finances else None

        if state_data:
            total_st = state_data["total"]

            # --- Buttons ---
            st_btn1, st_btn2, st_btn_rest = st.columns([1, 1, 8])
            with st_btn1:
                st_save = st.button("Save", key="st_save")
            with st_btn2:
                st_clear = st.button("Clear", key="st_clear")

            if st_save:
                st.session_state.saved_state_data = state_data
                st.rerun()
            if st_clear:
                st.session_state.saved_state_data = None
                st.rerun()

            # --- Total Score ---
            st.markdown(f"""
            <div style="text-align:center; margin-top:4px; margin-bottom:10px;">
                <div style="font-size:14px; letter-spacing:2px; color:#666;">TOTAL SCORE</div>
                <div style="font-size:90px; font-weight:800; color:#2E7BE6; line-height:1;">
                    {total_st}
                    <span style="font-size:35px; color:#BBB;">/ 1000</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # --- Radar + Metrics ---
            st_col_left, st_col_right = st.columns([1.5, 1])

            with st_col_left:
                st.markdown("<div style='font-size: 1.1em; font-weight: bold; color: #333; margin-top: -10px; margin-bottom: 5px;'>I. Intelligence Radar</div>", unsafe_allow_html=True)
                fig_st = render_radar_chart(state_data, st.session_state.saved_state_data, STATE_AXES_LABELS)
                st.plotly_chart(fig_st, use_container_width=True, config={"displayModeBar": False})

            with st_col_right:
                st.markdown(
                    "<div style='font-size: 0.9em; font-weight: bold; color: #333; margin-top: -10px; margin-bottom: 15px; border-left: 3px solid #2E7BE6; padding-left: 8px;'>II. ANALYSIS SCORE METRICS</div>",
                    unsafe_allow_html=True
                )

                saved_st = st.session_state.saved_state_data
                for axis in STATE_AXES_LABELS:
                    v1 = state_data["axes"][axis]
                    v2 = saved_st["axes"].get(axis, 0) if saved_st else None
                    desc_text = STATE_LOGIC_DESC.get(axis, "")

                    score_html = f'<span style="color: #2E7BE6;">{int(v1)}</span>'
                    if v2 is not None:
                        score_html += f' <span style="color: #ccc; font-size: 0.9em; font-weight:bold; margin: 0 6px;">vs</span> <span style="color: #F4A261;">{int(v2)}</span>'

                    st.markdown(
                        f"""
                        <div style="
                            background-color: #FFFFFF;
                            padding: 20px;
                            border-radius: 12px;
                            margin-bottom: 12px;
                            border: 1px solid #E0E0E0;
                            border-left: 8px solid #2E7BE6;
                            box-shadow: 2px 2px 5px rgba(0,0,0,0.07);
                        ">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <span style="font-size: 1.4em; font-weight: 800; color: #333333;">{axis}</span>
                                <span style="font-size: 1.9em; font-weight: 900; line-height: 1;">{score_html}</span>
                            </div>
                            <p style="font-size: 1.05em; color: #777777; margin: 0; line-height: 1.3; font-weight: 500;">{desc_text}</p>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            # --- Fiscal Snapshot ---
            st.markdown("<div class='section-title'>III. Fiscal Snapshot</div>", unsafe_allow_html=True)
            fs1, fs2, fs3, fs4 = st.columns(4)
            fiscal_items = [
                (fs1, "REVENUE", _fmt_budget(state_data["revenue"])),
                (fs2, "EXPENDITURE", _fmt_budget(state_data["expenditure"])),
                (fs3, "DEBT", _fmt_budget(state_data["debt"])),
                (fs4, "RESERVES", _fmt_budget(state_data["cash_holdings"])),
            ]
            for col, label, value in fiscal_items:
                col.markdown(f"""
                <div style="background:#fff; padding:20px; border-radius:12px; text-align:center; border:1px solid #e2e8f0; box-shadow:2px 2px 5px rgba(0,0,0,0.04);">
                    <div style="font-size:0.7em; font-weight:700; color:#94a3b8; letter-spacing:1px;">{label}</div>
                    <div style="font-size:1.8em; font-weight:900; color:#2E7BE6; line-height:1.3;">{value}</div>
                </div>
                """, unsafe_allow_html=True)

            # --- IV. Score History (2017-2023) ---
            st.markdown("<div class='section-title'>IV. GOV-1000 Score History (2017–2023)</div>",
                        unsafe_allow_html=True)
            with st.spinner("Computing historical scores..."):
                history = fetch_state_score_history(selected_fips)

            if history and len(history) > 1:
                h_years = [h["year"] for h in history]
                h_totals = [h["total"] for h in history]

                fig_sh = go.Figure()
                fig_sh.add_trace(go.Scatter(
                    x=h_years, y=h_totals,
                    mode="lines+markers", name="GOV-1000 Score",
                    line=dict(color="#2E7BE6", width=3),
                    marker=dict(size=10),
                    fill="tozeroy",
                    fillcolor="rgba(46,123,230,0.1)",
                ))
                fig_sh.update_layout(
                    height=350,
                    margin=dict(l=0, r=0, t=10, b=0),
                    plot_bgcolor="white",
                    yaxis=dict(gridcolor="#f0f0f0", range=[0, 1000], dtick=200),
                    xaxis=dict(gridcolor="#f0f0f0", dtick=1),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    dragmode=False,
                )
                st.plotly_chart(fig_sh, use_container_width=True, config={"displayModeBar": False})

                # Revenue vs Expenditure history
                st.markdown("<div class='section-title'>V. Revenue vs Expenditure (2017–2023)</div>",
                            unsafe_allow_html=True)
                h_rev = [h["revenue"] for h in history]
                h_exp = [h["expenditure"] for h in history]

                fig_re = go.Figure()
                fig_re.add_trace(go.Scatter(
                    x=h_years, y=h_rev,
                    mode="lines+markers", name="Revenue",
                    line=dict(color="#2E7BE6", width=3),
                    marker=dict(size=8),
                ))
                fig_re.add_trace(go.Scatter(
                    x=h_years, y=h_exp,
                    mode="lines+markers", name="Expenditure",
                    line=dict(color="#ef4444", width=3),
                    marker=dict(size=8),
                ))
                fig_re.update_layout(
                    height=350,
                    margin=dict(l=0, r=0, t=10, b=0),
                    plot_bgcolor="white",
                    yaxis=dict(gridcolor="#f0f0f0", tickformat="$.2s"),
                    xaxis=dict(gridcolor="#f0f0f0", dtick=1),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    dragmode=False,
                )
                st.plotly_chart(fig_re, use_container_width=True, config={"displayModeBar": False})
            else:
                st.info("Historical data not available.")

            # --- VI. Latest News ---
            st.markdown("<div class='section-title'>VI. Latest News</div>", unsafe_allow_html=True)
            state_news = fetch_gov_news(f"{state_data['name']} state budget")
            if state_news:
                for item in state_news:
                    st.markdown(
                        f'<div style="padding:10px 0; border-bottom:1px solid #F0F0F0;">'
                        f'<a href="{item["link"]}" target="_blank" style="font-size:0.95em; font-weight:600; color:#1e3a8a; text-decoration:none;">{item["title"]}</a>'
                        f'<div style="font-size:0.8em; color:#999; margin-top:3px;">{item["publisher"]} · {item["date"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.caption("No recent news available.")

        else:
            st.error("Failed to load state data. Please check your internet connection.")

    # ===================================================================
    # RANKINGS TAB
    # ===================================================================
    with tab_rankings:
        st.markdown(
            "<div style='font-size:1.5em; font-weight:900; color:#1e3a8a; margin-bottom:20px;'>"
            "All Agencies Ranking</div>",
            unsafe_allow_html=True,
        )

        # Reuse scores from dashboard if available, otherwise re-fetch
        if "all_scores" not in dir() or not all_scores:
            ranking_scores = []
            with st.spinner("Loading rankings..."):
                with ThreadPoolExecutor(max_workers=15) as executor:
                    futures = {executor.submit(score_agency, code): code for code in AGENCIES}
                    for future in as_completed(futures):
                        try:
                            d = future.result()
                            if d:
                                ranking_scores.append(d)
                        except Exception:
                            pass
        else:
            ranking_scores = all_scores

        if ranking_scores:
            # Sort option
            sort_by = st.selectbox("Sort by", ["Total Score"] + AXES_LABELS, key="rank_sort")

            if sort_by == "Total Score":
                ranking_scores.sort(key=lambda x: x["total"], reverse=True)
            else:
                ranking_scores.sort(key=lambda x: x["axes"].get(sort_by, 0), reverse=True)

            # Delta from daily history
            history = _load_scores_history()
            dates = sorted(history.keys(), reverse=True) if history else []

            # Render as styled cards (matching FRS-1000)
            for idx, s in enumerate(ranking_scores, 1):
                score = s["total"]
                if score >= 700:
                    bar_color = "#10b981"
                elif score >= 500:
                    bar_color = "#2E7BE6"
                elif score >= 300:
                    bar_color = "#f59e0b"
                else:
                    bar_color = "#ef4444"

                # Delta
                prev_score = None
                for dt in dates:
                    prev_score = history[dt].get(s["name"])
                    if prev_score is not None:
                        break
                if prev_score is not None:
                    d_val = score - prev_score
                    if d_val > 0:
                        change_html = f'<span style="color:#10b981; font-weight:700;">&#9650; +{d_val}</span>'
                    elif d_val < 0:
                        change_html = f'<span style="color:#ef4444; font-weight:700;">&#9660; {d_val}</span>'
                    else:
                        change_html = f'<span style="color:#94a3b8; font-weight:700;">&#9644; 0</span>'
                else:
                    change_html = '<span style="color:#94a3b8;">-</span>'

                st.markdown(f"""
                <div style="display:flex; align-items:center; padding:14px 20px; background:#fff; border-radius:12px; margin-bottom:8px; border:1px solid #e2e8f0; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                    <div style="font-size:1.4em; font-weight:900; color:#94a3b8; width:40px;">#{idx}</div>
                    <div style="flex:1;">
                        <div style="font-size:1.05em; font-weight:700; color:#1e293b;">{s['name']}</div>
                        <span style="font-size:0.75em; background:#2E7BE6; color:#fff; padding:2px 8px; border-radius:20px;">{s['abbr']}</span>
                    </div>
                    <div style="text-align:right; margin-right:15px; font-size:0.85em; color:#94a3b8;">
                        {_fmt_budget(s['budget_authority'])}
                    </div>
                    <div style="text-align:right; margin-right:20px;">
                        {change_html}
                    </div>
                    <div style="text-align:right; min-width:80px;">
                        <div style="font-size:1.5em; font-weight:900; color:{bar_color};">{score}</div>
                        <div style="background:#f1f5f9; border-radius:4px; height:6px; width:80px; margin-top:4px;">
                            <div style="background:{bar_color}; height:6px; border-radius:4px; width:{score/10}%;"></div>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # Bar chart
            st.markdown("<div class='section-title'>Score Distribution</div>",
                        unsafe_allow_html=True)

            fig_bar = go.Figure()
            colors = []
            for s in ranking_scores:
                if s["total"] >= 700:
                    colors.append("#10b981")
                elif s["total"] >= 500:
                    colors.append("#2E7BE6")
                elif s["total"] >= 300:
                    colors.append("#f59e0b")
                else:
                    colors.append("#ef4444")

            fig_bar.add_trace(go.Bar(
                x=[s["abbr"] for s in ranking_scores],
                y=[s["total"] for s in ranking_scores],
                marker_color=colors,
                text=[s["total"] for s in ranking_scores],
                textposition='outside',
            ))
            fig_bar.update_layout(
                yaxis=dict(range=[0, 1000], title="Score"),
                height=400, margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor='white', clickmode='none', dragmode=False,
            )
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

            # Stacked axis breakdown
            st.markdown("<div class='section-title'>Axis Breakdown</div>",
                        unsafe_allow_html=True)

            axis_colors = ["#2E7BE6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444"]
            fig_stack = go.Figure()
            for j, axis in enumerate(AXES_LABELS):
                fig_stack.add_trace(go.Bar(
                    x=[s["abbr"] for s in ranking_scores],
                    y=[s["axes"].get(axis, 0) for s in ranking_scores],
                    name=axis, marker_color=axis_colors[j],
                ))
            fig_stack.update_layout(
                barmode='stack', yaxis=dict(title="Score"),
                height=450, margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor='white', clickmode='none', dragmode=False,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_stack, use_container_width=True, config={"displayModeBar": False})

        else:
            st.error("Failed to load ranking data.")

    # Disclaimer
    st.markdown("""
    <div style="margin-top:40px; padding:16px; background:#f8fafc; border-radius:10px; font-size:0.7em;
    color:#64748b; line-height:1.6; text-align:left; border-left:4px solid #2E7BE6; max-width:600px;
    margin-left:auto; margin-right:auto;">
    <strong>DISCLAIMER:</strong> This tool is for informational and research purposes only.
    Scores are derived from publicly available data on USASpending.gov and the US Census Bureau
    and do not represent official government assessments. All data is provided as-is without warranty.
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
