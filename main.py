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
        "page": "3_korea_vol_target_app_v1.py",
        "name": "KODEX 200 / Leverage Vol Target Experiment",
        "role": "Cross-apply experiment",
        "decision": "Review",
        "note": "Applies the SOXX/SOXL volatility-target logic to KODEX 200 and KODEX Leverage.",
    },
    {
        "page": "4_us_bull_bear_app_v3.py",
        "name": "SPY / UPRO Bull/Bear v3",
        "role": "Defensive / regime candidate",
        "decision": "Keep",
        "note": "Use v3 as the practical US bull/bear strategy.",
    },
    {
        "page": "5_soxx_soxl_vol_target_app.py",
        "name": "SOXX / SOXL Vol Target",
        "role": "Aggressive satellite",
        "decision": "Keep",
        "note": "Keep as a small semiconductor satellite sleeve.",
    },
    {
        "page": "5_soxx_soxl_onoff_app_v1.py",
        "name": "SOXX / SOXL ON-OFF Experiment",
        "role": "Cross-apply experiment",
        "decision": "Review",
        "note": "Applies the KODEX ON/OFF and high-volatility fallback logic to SOXX and SOXL.",
    },
    {
        "page": "6_qqq_tqqq_vol_target_app.py",
        "name": "QQQ / TQQQ Vol Target",
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
]


st.set_page_config(
    page_title="my_backtest Strategy Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("my_backtest Strategy Dashboard")
st.caption("A simplified list of strategies to keep, replace, or review.")

page_count = len(list(PAGES_DIR.glob("*.py"))) if PAGES_DIR.exists() else 0
keep_count = sum(1 for item in STRATEGIES if item["decision"] == "Keep")
satellite_count = sum(1 for item in STRATEGIES if "satellite" in item["role"].lower())
experiment_count = sum(1 for item in STRATEGIES if "experiment" in item["role"].lower())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Current pages", page_count)
c2.metric("Keep", keep_count)
c3.metric("Satellite sleeves", satellite_count)
c4.metric("Experiments", experiment_count)

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
1. Compare the original strategy and the cross-applied experiment for each asset pair.
2. Decide whether the experiment is only research, or whether it deserves a permanent allocation candidate slot.
3. Confirm the simplified strategy set.
4. Decide target allocation by strategy role: core, defensive, growth, and satellite.
    """
)

st.markdown("---")
st.caption("Use the Pages menu in the sidebar to run each remaining strategy.")
