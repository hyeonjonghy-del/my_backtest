from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "strategies" / "korea_sector_momentum_v2"
sys.path.insert(0, str(ROOT / "strategies"))

from korea_sector_momentum_v2.run import load_hybrid_monthly_prices, load_kospi200_monthly_prices  # noqa: E402
from korea_sector_momentum_v2.strategy import backtest, metrics  # noqa: E402

st.set_page_config(page_title="Korea Sector Momentum V2", layout="wide")
config = json.loads((STRATEGY_DIR / "config.json").read_text(encoding="utf-8"))

title, action = st.columns([6, 1])
with title:
    st.title("Korea Sector Momentum V2")
with action:
    st.write("")
    submitted = st.button("Run hybrid backtest", type="primary", use_container_width=True)

st.caption("2017~2022년은 대표종목 프록시로 검증하고, 2023년부터는 ETF가 준비된 섹터부터 ETF 수익률로 전환하는 10개 섹터 모멘텀 전략입니다.")
st.info("최종 룰: 연 1회 상위 5개 섹터 선정 후, 매월 선정 섹터 5개와 현금(0%)을 순위화하여 45/30/15/5/5로 배분합니다.")

with st.sidebar:
    st.subheader("V2 backtest settings")
    start = st.date_input("Start", value=dt.date(2017, 1, 1), key="v2_start")
    end = st.date_input("End", value=dt.date(2026, 8, 31), key="v2_end")

if submitted:
    if start >= end:
        st.error("Start 날짜는 End 날짜보다 빨라야 합니다.")
        st.stop()
    with st.spinner("대표종목과 ETF 가격을 연결해 V2 백테스트 중입니다..."):
        prices = load_hybrid_monthly_prices(config["sector_specs"], start.isoformat(), end.isoformat())
        result = backtest(
            prices, config["sector_specs"], start.isoformat(), end.isoformat(),
            weights=config["weights"],
            selection_lookback=config["selection_lookback_months"],
            ranking_lookback=config["ranking_lookback_months"],
            etf_transition_start=config["etf_transition_start"],
            transaction_cost=config["transaction_cost"],
        )
        try:
            benchmark = load_kospi200_monthly_prices(start.isoformat(), end.isoformat())
        except Exception:
            benchmark = pd.Series(dtype=float)
    st.session_state["v2_result"] = result
    st.session_state["v2_metrics"] = metrics(result.monthly)
    st.session_state["v2_benchmark"] = benchmark

if "v2_result" not in st.session_state:
    st.warning("기간을 확인한 뒤 Run hybrid backtest를 눌러주세요.")
    st.stop()

result = st.session_state["v2_result"]
monthly = result.monthly.copy()
values = st.session_state["v2_metrics"]
monthly["drawdown"] = monthly["wealth"] / monthly["wealth"].cummax() - 1.0

cols = st.columns(5)
cols[0].metric("Cumulative return", f"{values['cumulative_return']:.1%}")
cols[1].metric("CAGR", f"{values['cagr']:.1%}")
cols[2].metric("Max drawdown", f"{values['max_drawdown']:.1%}")
cols[3].metric("Annualized volatility", f"{values['annualized_volatility']:.1%}")
cols[4].metric("Win rate", f"{values['win_rate']:.1%}")

st.subheader("Cumulative performance")
st.line_chart(monthly[["wealth"]].rename(columns={"wealth": "Portfolio value"}), use_container_width=True)
st.subheader("Drawdown / MDD")
st.area_chart(monthly[["drawdown"]], use_container_width=True)

st.subheader("Monthly target allocation")
allocation_history = monthly.filter(regex=r"^weight_").copy()
allocation_history.columns = [
    column.removeprefix("weight_") for column in allocation_history.columns
]
allocation_long = (
    allocation_history.rename_axis("date")
    .reset_index()
    .melt(id_vars="date", var_name="자산", value_name="비중")
)
allocation_chart = (
    alt.Chart(allocation_long)
    .mark_area()
    .encode(
        x=alt.X("date:T", title="리밸런싱 월"),
        y=alt.Y(
            "비중:Q",
            stack="zero",
            title="목표 비중",
            axis=alt.Axis(format=".0%"),
            scale=alt.Scale(domain=[0, 1]),
        ),
        color=alt.Color("자산:N", title="자산"),
        tooltip=[
            alt.Tooltip("date:T", title="월", format="%Y-%m"),
            alt.Tooltip("자산:N", title="자산"),
            alt.Tooltip("비중:Q", title="목표 비중", format=".1%"),
        ],
    )
    .properties(height=360)
)
st.altair_chart(allocation_chart, use_container_width=True)
st.caption("매월 리밸런싱 시점의 목표 비중입니다. 범례의 ‘현금’도 12개월 모멘텀 0%의 하나의 자산으로 순위에 포함됩니다.")

st.subheader("Latest target allocation")
allocation = allocation_history.iloc[-1].rename("weight").reset_index()
allocation.columns = ["asset", "weight"]
allocation = allocation[(allocation["weight"] > 0) | (allocation["asset"] == "현금")]
st.bar_chart(allocation.set_index("asset")[["weight"]], use_container_width=True)

st.subheader("Sector return source at latest month")
latest_sources = result.sector_sources.iloc[-1].rename("source").reset_index()
latest_sources.columns = ["sector", "return source"]
st.dataframe(latest_sources, use_container_width=True, hide_index=True)

st.subheader("Recent monthly details")
st.dataframe(monthly.tail(24), use_container_width=True)
st.download_button(
    "Download V2 monthly results CSV",
    monthly.to_csv().encode("utf-8-sig"),
    file_name="korea_sector_momentum_v2_monthly.csv",
    mime="text/csv",
)

with st.expander("ETF transition universe", expanded=False):
    st.json(config["sector_specs"])
