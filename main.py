from __future__ import annotations

from pathlib import Path

import streamlit as st


ROOT = Path(__file__).parent
PAGES_DIR = ROOT / "pages"

STRATEGIES = [
    {
        "page": "3_KOSPI200_Bull_Bear.py",
        "name": "KOSPI200 Bull/Bear",
        "role": "Korea core",
        "decision": "Execute",
        "note": "Primary Korea-market strategy.",
    },
    {
        "page": "3_KOSPI200_Bull_Bear_v1(aggressive).py",
        "name": "KOSPI200 Bull/Bear v1 (Aggressive)",
        "role": "Korea reference",
        "decision": "Reference",
        "note": "Keep for comparison with the primary Korea strategy.",
    },
    {
        "page": "4_Samsung_Electronics_Trend_Vol.py",
        "name": "Samsung Electronics Trend / Leverage",
        "role": "Korea monitor",
        "decision": "Monitor",
        "note": "Monitor until enough live leveraged-ETF history is available.",
    },
    {
        "page": "2_S&P500_Momentun.py",
        "name": "S&P500 Momentum",
        "role": "US core",
        "decision": "Execute",
        "note": "Primary US stock-selection strategy.",
    },
    {
        "page": "4_S&P500_Bull_Bear.py",
        "name": "S&P500 Bull/Bear",
        "role": "US reference",
        "decision": "Reference",
        "note": "Keep as a regime-strategy comparison.",
    },
    {
        "page": "5_SOXX_SOXL_vol.py",
        "name": "SOXX / SOXL Vol Target",
        "role": "US semiconductor",
        "decision": "Execute",
        "note": "Primary semiconductor strategy.",
    },
    {
        "page": "5_SOXX.vol.py",
        "name": "SOXX Vol Target",
        "role": "US semiconductor reference",
        "decision": "Reference",
        "note": "Keep the unleveraged version for comparison.",
    },
    {
        "page": "6_QQQ_TQQQ_holdings.py",
        "name": "QQQ / TQQQ Holdings",
        "role": "US growth",
        "decision": "Execute",
        "note": "Sole active QQQ/TQQQ implementation.",
    },
    {
        "page": "9_QQQ_Gold_Momentum.py",
        "name": "QQQ / Gold / SGOV Momentum",
        "role": "US asset allocation",
        "decision": "Keep",
        "note": "Preferred QQQ/Gold allocation version.",
    },
    {
        "page": "9_US_Integrated_Strategy.py",
        "name": "US Integrated Strategy",
        "role": "US allocation review",
        "decision": "Review",
        "note": "Keep unchanged while the allocation approach is reconsidered.",
    },
]


st.set_page_config(
    page_title="my_backtest Strategy Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("my_backtest Strategy Dashboard")
st.caption("Active strategies, reference models, and monitored candidates.")

page_count = len(list(PAGES_DIR.glob("*.py"))) if PAGES_DIR.exists() else 0
execute_count = sum(1 for item in STRATEGIES if item["decision"] == "Execute")
monitor_count = sum(1 for item in STRATEGIES if item["decision"] in {"Monitor", "Review"})

c1, c2, c3 = st.columns(3)
c1.metric("Active pages", page_count)
c2.metric("Execute", execute_count)
c3.metric("Monitor / Review", monitor_count)

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

st.markdown("### Operating Principle")
st.markdown(
    """
1. Execute only strategies marked `Execute`.
2. Reference strategies are comparisons, not separate allocations.
3. Monitor and review strategies remain visible without receiving capital.
4. Removed experiments are preserved under `archived_pages/`.
    """
)

st.markdown("---")
st.caption("Use the Pages menu in the sidebar to run each remaining strategy.")
# Deployment sync: 2026-07-31 account helpers

