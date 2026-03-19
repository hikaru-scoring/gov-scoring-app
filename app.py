# app.py
"""GOV-1000 — US Government Agency Scoring Platform."""
import json
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_logic import AGENCIES, AXES_LABELS, score_agency
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
    "Transparency": "Budget justification x Reporting x Data quality",
    "Performance": "Transaction volume x New award count",
    "Fiscal Discipline": "YoY budget growth x Unobligated balance ratio",
    "Accountability": "GAO audit findings (fewer = higher score)",
}


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
    tab_dash, tab_agency, tab_rankings = st.tabs(["Dashboard", "Agency Detail", "Rankings"])

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

            # --- IV. Daily Score Tracker ---
            st.markdown("<div class='section-title'>IV. Daily Score Tracker</div>",
                        unsafe_allow_html=True)
            render_daily_score_tracker(data["name"])

            # --- V. Score Comparison ---
            st.markdown("<div class='section-title'>V. Score Comparison</div>",
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
        else:
            st.error("Failed to load agency data.")

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
    Scores are derived from publicly available data on USASpending.gov and do not represent
    official government assessments. All data is provided as-is without warranty.
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
