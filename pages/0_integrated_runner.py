from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from chart_utils import static_line_chart
from core.us_vol_runner import default_us_configs, run_us_vol_strategy


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
        "SOXX/SOXL Vol Target v5",
        "US",
        "Aggressive satellite",
        "5_soxx_soxl_vol_target_app_v5.py",
        0.03,
        "Needs core runner",
    ),
    StrategySpec(
        6,
        "QQQ/TQQQ Vol Target v2",
        "US",
        "Growth satellite",
        "6_qqq_tqqq_vol_target_app_v2.py",
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


def metrics_frame(results: list[dict[str, object]], weights: dict[str, float]) -> pd.DataFrame:
    rows = []
    name_to_number = {item.name: str(item.number) for item in STRATEGIES}
    for result in results:
        metrics = result["metrics"]
        strategy_number = name_to_number.get(str(result["name"]), "")
        rows.append(
            {
                "Strategy": result["name"],
                "Weight": weights.get(strategy_number, 0.0),
                "Signal": result["current_signal"],
                "Position": result["current_position"],
                "Total": metrics["total"],
                "CAGR": metrics["cagr"],
                "MDD": metrics["mdd"],
                "Sharpe": metrics["sharpe"],
                "Calmar": metrics["calmar"],
            }
        )
    return pd.DataFrame(rows)


def combine_returns(results: list[dict[str, object]], weights: dict[str, float]) -> pd.Series:
    name_to_number = {item.name: str(item.number) for item in STRATEGIES}
    series = []
    for result in results:
        strategy_number = name_to_number[str(result["name"])]
        weight = weights[strategy_number]
        ret = result["daily_returns"].rename(result["name"]) * weight
        series.append(ret)
    if not series:
        return pd.Series(dtype=float)
    frame = pd.concat(series, axis=1).fillna(0.0)
    return frame.sum(axis=1).rename("Portfolio")


def calc_portfolio_metrics(daily_returns: pd.Series) -> dict[str, object]:
    if daily_returns.empty:
        return {"nav": pd.Series(dtype=float), "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0}
    nav = (1 + daily_returns).cumprod()
    years = len(nav) / 252
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    dd = nav / nav.cummax() - 1
    sharpe = daily_returns.mean() / daily_returns.std() * (252 ** 0.5) if daily_returns.std() > 0 else 0.0
    return {"nav": nav, "drawdown": dd, "cagr": cagr, "mdd": dd.min(), "sharpe": sharpe}


st.set_page_config(
    page_title="Integrated Strategy Runner",
    page_icon="📊",
    layout="wide",
)

title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("Integrated Strategy Runner")
with run_col:
    st.write("")
    run_us = st.button("Run strategies 4-6", type="primary", use_container_width=True, key="run_strategies_top")
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

    st.markdown("---")
    st.header("Runner")
    start_date = st.date_input("US start", pd.Timestamp("2016-01-04"))
    end_date = st.date_input("US end", pd.Timestamp.today())

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
    st.subheader("Run Strategies 4-6")
    st.caption("This first integrated runner connects the three US ETF strategies only.")

    if not run_us:
        st.info("Set the allocation and dates in the sidebar, then click Run strategies 4-6.")
    else:
        progress = st.progress(0, text="Running US ETF strategies...")
        results: list[dict[str, object]] = []
        configs = default_us_configs(
            pd.Timestamp(start_date).to_pydatetime(),
            pd.Timestamp(end_date).to_pydatetime(),
        )
        for index, config in enumerate(configs, start=1):
            progress.progress(index / len(configs), text=f"Running {config.name}...")
            results.append(run_us_vol_strategy(config))
        progress.empty()

        portfolio_returns = combine_returns(results, edited_weights)
        portfolio_metrics = calc_portfolio_metrics(portfolio_returns)
        c1, c2, c3 = st.columns(3)
        c1.metric("US Sleeve CAGR", f"{portfolio_metrics['cagr']:.1%}")
        c2.metric("US Sleeve MDD", f"{portfolio_metrics['mdd']:.1%}")
        c3.metric("US Sleeve Sharpe", f"{portfolio_metrics['sharpe']:.2f}")

        result_df = metrics_frame(results, edited_weights)
        shown = result_df.copy()
        for col in ["Weight", "Total", "CAGR", "MDD"]:
            shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        for col in ["Sharpe", "Calmar"]:
            shown[col] = shown[col].map(lambda x: f"{x:.2f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)

        nav_frame = pd.DataFrame({"US Integrated Sleeve": portfolio_metrics["nav"]})
        for result in results:
            nav_frame[str(result["name"])] = result["nav"]
        st.pyplot(
            static_line_chart(nav_frame, "US Strategies 4-6 Integrated NAV", "NAV", height=360),
            clear_figure=True,
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
