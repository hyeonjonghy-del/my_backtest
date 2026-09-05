from __future__ import annotations

from pathlib import Path

import streamlit as st


ROOT = Path(__file__).parent
PAGES_DIR = ROOT / "pages"

STRATEGIES = [
    {
        "page": "1_KOSPI_Momentum_v3.py",
        "name": "KOSPI 200 Momentum v3",
        "role": "Core candidate",
        "decision": "Keep",
        "note": "Use v3 as the practical KOSPI momentum version.",
    },
    {
        "page": "2_SP500_Momentum_v3.py",
        "name": "S&P 500 Momentum v3",
        "role": "Core candidate",
        "decision": "Keep",
        "note": "Use v3 as the practical S&P 500 momentum version.",
    },
    {
        "page": "3_korea_bull_bear_app_v5.py",
        "name": "KODEX 200 Bull/Bear v5",
        "role": "Defensive / regime candidate",
        "decision": "Keep",
        "note": "Use v5 as the practical Korea bull/bear strategy.",
    },
    {
        "page": "4_us_bull_bear_app_v3.py",
        "name": "SPY / UPRO Bull/Bear v3",
        "role": "Defensive / regime candidate",
        "decision": "Keep",
        "note": "Use v3 as the practical US bull/bear strategy.",
    },
    {
        "page": "3_kodex_semiconductor_vol_target_app_v1.py",
        "name": "KODEX Semiconductor Vol Target v1",
        "role": "Defensive semiconductor core",
        "decision": "Keep",
        "note": "Use KODEX semiconductor and short-term bond ETFs to reduce drawdown without leverage.",
    },
    {
        "page": "3_kodex_sector_rotation_app_v1.py",
        "name": "KODEX Sector Rotation Research v1",
        "role": "Research / rotation candidate",
        "decision": "Review",
        "note": "Compare semiconductor exit, replacement-sector, and top-2 sector rotation rules.",
    },
    {
        "page": "5_soxx_vol_target_app_v1.py",
        "name": "SOXX Vol Target v1",
        "role": "Defensive semiconductor core",
        "decision": "Keep",
        "note": "Use SOXX and BIL only to reduce drawdown without leveraged ETFs.",
    },
    {
        "page": "5_soxx_soxl_vol_target_app_v5.py",
        "name": "SOXX / SOXL Vol Target v5",
        "role": "Aggressive satellite",
        "decision": "Keep",
        "note": "Keep as a small semiconductor satellite sleeve.",
    },
    {
        "page": "6_qqq_tqqq_vol_target_app_v2.py",
        "name": "QQQ / TQQQ Vol Target v2",
        "role": "Growth satellite",
        "decision": "Keep",
        "note": "Keep as the Nasdaq growth satellite sleeve.",
    },
    {
        "page": "7_dividend_screener.py",
        "name": "Dividend Screener",
        "role": "Research / screening",
        "decision": "Keep",
        "note": "Use as a supporting stock-screening tool, not a core allocation strategy.",
    },
    {
        "page": "8_chartdoctor_bluechip.py",
        "name": "Chart Doctor Bluechip",
        "role": "Research",
        "decision": "Keep",
        "note": "Keep as a research strategy unless later performance review says otherwise.",
    },
    {
        "page": "9_QQQ_Gold_Momentum_v2.py",
        "name": "QQQ / GLD / SGOV Momentum v2",
        "role": "Growth / defensive allocation",
        "decision": "Keep",
        "note": "Scale SGOV to 0%, 20%, or 40% by its 12-month momentum rank.",
    },
]


st.set_page_config(
    page_title="my_backtest Strategy Dashboard",
    page_icon="?뱢",
    layout="wide",
)

st.title("my_backtest Strategy Dashboard")
st.caption("A simplified list of strategies to keep, replace, or review.")

page_count = len(list(PAGES_DIR.glob("*.py"))) if PAGES_DIR.exists() else 0
keep_count = sum(1 for item in STRATEGIES if item["decision"] == "Keep")
satellite_count = sum(1 for item in STRATEGIES if "satellite" in item["role"].lower())

c1, c2, c3 = st.columns(3)
c1.metric("Current pages", page_count)
c2.metric("Keep", keep_count)
c3.metric("Satellite sleeves", satellite_count)

st.markdown("### Strategy List")
st.dataframe(
    [
        {
            "Decision": item["decision"],
            "Strategy": item["name"],
            "Role": item["role"],
            "File": f"pages/{item['page']}",
            "Note": item["note"],
        }
        for item in STRATEGIES
    ],
    use_container_width=True,
    hide_index=True,
)

st.markdown("### Next Questions")
st.markdown(
    """
1. Confirm the simplified strategy set.
2. Decide target allocation by strategy role: core, defensive, growth, and satellite.
3. Add a simple allocation dashboard.
4. Improve management only after the strategy set and weights are final.
    """
)

st.markdown("---")
st.caption("Use the Pages menu in the sidebar to run each remaining strategy.")
# Deployment sync: 2026-07-31 account helpers

