from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "integrated_runner"
ALLOCATION_PATH = DATA_DIR / "allocation_plan.json"


@dataclass(frozen=True)
class StrategySpec:
    number: int
    name: str
    market: str
    role: str
    page: str
    default_weight: float
    integration_status: str


STRATEGIES = [
    StrategySpec(
        1,
        "KOSPI Momentum v3",
        "Korea",
        "Core momentum",
        "1_KOSPI_Momentum_v3.py",
        0.35,
        "Needs core runner",
    ),
    StrategySpec(
        2,
        "S&P 500 Momentum v3",
        "US",
        "Core momentum",
        "2_SP500_Momentum_v3.py",
        0.05,
        "Needs core runner",
    ),
    StrategySpec(
        3,
        "Korea Bull/Bear v5",
        "Korea",
        "Defensive regime",
        "3_korea_bull_bear_app_v5.py",
        0.45,
        "Needs core runner",
    ),
    StrategySpec(
        4,
        "US Bull/Bear v3",
        "US",
        "Defensive regime",
        "4_us_bull_bear_app_v3.py",
        0.05,
        "Needs core runner",
    ),
    StrategySpec(
        5,
        "SOXX/SOXL Vol Target",
        "US",
        "Aggressive satellite",
        "5_soxx_soxl_vol_target_app.py",
        0.03,
        "Needs core runner",
    ),
    StrategySpec(
        6,
        "QQQ/TQQQ Vol Target",
        "US",
        "Growth satellite",
        "6_qqq_tqqq_vol_target_app.py",
        0.07,
        "Needs core runner",
    ),
]


def load_allocation() -> dict[str, float]:
    if not ALLOCATION_PATH.exists():
        return {str(item.number): item.default_weight for item in STRATEGIES}
    try:
        data = json.loads(ALLOCATION_PATH.read_text(encoding="utf-8"))
        return {
            str(item.number): float(data.get(str(item.number), item.default_weight))
            for item in STRATEGIES
        }
    except Exception:
        return {str(item.number): item.default_weight for item in STRATEGIES}


def save_allocation(weights: dict[str, float]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ALLOCATION_PATH.write_text(
        json.dumps(weights, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def allocation_frame(weights: dict[str, float]) -> pd.DataFrame:
    rows = []
    for item in STRATEGIES:
        weight = float(weights[str(item.number)])
        rows.append(
            {
                "No": item.number,
                "Strategy": item.name,
                "Market": item.market,
                "Role": item.role,
                "Weight": weight,
                "Page": item.page,
                "Status": item.integration_status,
            }
        )
    return pd.DataFrame(rows)


def market_summary(df: pd.DataFrame) -> pd.DataFrame:
    out = df.groupby("Market", as_index=False)["Weight"].sum()
    out["Weight"] = out["Weight"].map(lambda x: f"{x:.1%}")
    return out


st.set_page_config(
    page_title="Integrated Strategy Runner",
    page_icon="📊",
    layout="wide",
)

st.title("Integrated Strategy Runner")
st.caption("Strategy allocation and execution structure for strategies 1-6.")

weights = load_allocation()

with st.sidebar:
    st.header("Allocation")
    edited_weights: dict[str, float] = {}
    for item in STRATEGIES:
        edited_weights[str(item.number)] = (
            st.number_input(
                f"{item.number}. {item.name}",
                min_value=0.0,
                max_value=1.0,
                value=float(weights[str(item.number)]),
                step=0.01,
                format="%.2f",
            )
        )
    normalize = st.checkbox("Normalize to 100%", value=True)
    if normalize:
        total_raw = sum(edited_weights.values())
        if total_raw > 0:
            edited_weights = {
                key: value / total_raw
                for key, value in edited_weights.items()
            }

    if st.button("Save allocation", type="primary", use_container_width=True):
        save_allocation(edited_weights)
        st.success("Saved.")

df = allocation_frame(edited_weights)
total_weight = df["Weight"].sum()
korea_weight = df.loc[df["Market"] == "Korea", "Weight"].sum()
us_weight = df.loc[df["Market"] == "US", "Weight"].sum()

c1, c2, c3 = st.columns(3)
c1.metric("Total", f"{total_weight:.1%}")
c2.metric("Korea", f"{korea_weight:.1%}")
c3.metric("US", f"{us_weight:.1%}")

tab_alloc, tab_runner, tab_schema = st.tabs(["Allocation", "Runner", "Result Schema"])

with tab_alloc:
    shown = df.copy()
    shown["Weight"] = shown["Weight"].map(lambda x: f"{x:.1%}")
    st.dataframe(shown, use_container_width=True, hide_index=True)
    st.dataframe(market_summary(df), use_container_width=True, hide_index=True)

with tab_runner:
    st.subheader("Execution Plan")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Step": 1,
                    "Work": "Extract each strategy calculation into a pure runner function.",
                    "Output": "daily_returns, nav, metrics, current_position",
                },
                {
                    "Step": 2,
                    "Work": "Run strategies 1-6 with default configs.",
                    "Output": "One result object per strategy",
                },
                {
                    "Step": 3,
                    "Work": "Combine daily returns by allocation weights.",
                    "Output": "portfolio_nav, portfolio_mdd, monthly_returns",
                },
                {
                    "Step": 4,
                    "Work": "Show current target positions and rebalance notes.",
                    "Output": "operation table",
                },
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.info(
        "This page is the integration shell. The next code step is to connect the "
        "strategy runner functions one by one without changing the existing pages."
    )

with tab_schema:
    st.code(
        """
{
  "name": "Strategy name",
  "daily_returns": "pd.Series indexed by date",
  "nav": "pd.Series indexed by date",
  "metrics": {
    "total": 0.0,
    "cagr": 0.0,
    "mdd": 0.0,
    "sharpe": 0.0,
    "calmar": 0.0
  },
  "current_position": "human-readable position",
  "current_signal": "human-readable signal"
}
        """.strip(),
        language="json",
    )
