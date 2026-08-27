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


def annual_monthly_table(monthly: pd.DataFrame) -> pd.DataFrame:
    returns = monthly["return"].copy()
    returns.index = pd.to_datetime(returns.index)
    table = returns.groupby([returns.index.year, returns.index.month]).first().unstack()
    table = table.reindex(columns=range(1, 13))
    table.columns = [f"{month}월" for month in table.columns]
    annual = returns.groupby(returns.index.year).apply(lambda values: (1.0 + values).prod() - 1.0)
    table["연간"] = annual
    table.index.name = "연도"
    return table


def annual_comparison(monthly: pd.DataFrame, kospi200: pd.Series) -> pd.DataFrame:
    strategy_returns = monthly["return"].copy()
    strategy_returns.index = pd.to_datetime(strategy_returns.index)
    benchmark_returns = kospi200.pct_change().reindex(strategy_returns.index)
    comparison = pd.DataFrame({
        "전략": strategy_returns,
        "KOSPI200": benchmark_returns,
    }).dropna(how="any")
    return comparison.groupby(comparison.index.year).agg(
        전략=("전략", lambda values: (1.0 + values).prod() - 1.0),
        KOSPI200=("KOSPI200", lambda values: (1.0 + values).prod() - 1.0),
    )


st.set_page_config(page_title="Korea Sector Momentum V2", layout="wide")
config = json.loads((STRATEGY_DIR / "config.json").read_text(encoding="utf-8"))

title, action = st.columns([6, 1])
with title:
    st.title("Korea Sector Momentum V2")
with action:
    st.write("")
    submitted = st.button("Run hybrid backtest", type="primary", use_container_width=True)

st.caption("배포 버전: v2-2026-08-27.3")
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
        benchmark_error = None
        try:
            benchmark = load_kospi200_monthly_prices(start.isoformat(), end.isoformat())
        except Exception as exc:
            benchmark = pd.Series(dtype=float)
            benchmark_error = str(exc)
    st.session_state["v2_result"] = result
    st.session_state["v2_metrics"] = metrics(result.monthly)
    st.session_state["v2_benchmark"] = benchmark
    st.session_state["v2_benchmark_error"] = benchmark_error

if "v2_result" not in st.session_state:
    st.warning("기간을 확인한 뒤 Run hybrid backtest를 눌러주세요.")
    st.stop()

result = st.session_state["v2_result"]
monthly = result.monthly.copy()
values = st.session_state["v2_metrics"]
monthly["cumulative_return"] = monthly["wealth"] - 1.0
monthly["drawdown"] = monthly["wealth"] / monthly["wealth"].cummax() - 1.0

allocation = monthly.filter(regex=r"^weight_").copy()
allocation.columns = [column.removeprefix("weight_") for column in allocation.columns]
if "현금" not in allocation.columns:
    allocation["현금"] = 1.0 - allocation.sum(axis=1)
allocation["현금"] = allocation["현금"].clip(lower=0.0)
sector_columns = sorted(column for column in allocation.columns if column != "현금")
allocation = allocation[["현금", *sector_columns]]

summary_tab, monthly_tab = st.tabs(["성과 요약", "월별·연간 실적"])

with summary_tab:
    st.subheader("Backtest result")
    cols = st.columns(5)
    cols[0].metric("Cumulative return", f"{values['cumulative_return']:.1%}")
    cols[1].metric("CAGR", f"{values['cagr']:.1%}")
    cols[2].metric("Max drawdown", f"{values['max_drawdown']:.1%}")
    cols[3].metric("Annualized volatility", f"{values['annualized_volatility']:.1%}")
    cols[4].metric("Win rate", f"{values['win_rate']:.1%}")

    st.subheader("Cumulative performance")
    st.line_chart(monthly[["wealth"]].rename(columns={"wealth": "Portfolio value"}), use_container_width=True)

    st.subheader("Drawdown / MDD")
    st.area_chart(monthly[["drawdown"]].rename(columns={"drawdown": "Drawdown"}), use_container_width=True)
    st.caption(f"최대 낙폭(MDD): {values['max_drawdown']:.1%}. 누적자산은 초기 투자금 1.0 대비 계산됩니다.")

    st.subheader("Monthly target allocation")
    allocation_long = (
        allocation.rename_axis("date")
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
    latest_allocation = allocation.iloc[-1].rename("비중").reset_index()
    latest_allocation.columns = ["자산", "비중"]
    latest_allocation = latest_allocation[
        (latest_allocation["비중"] > 0.0) | (latest_allocation["자산"] == "현금")
    ].sort_values("비중", ascending=False)
    latest_chart = (
        alt.Chart(latest_allocation)
        .mark_bar()
        .encode(
            x=alt.X("비중:Q", title="목표 비중", axis=alt.Axis(format=".0%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("자산:N", sort="-x", title=None),
            color=alt.value("#2563eb"),
            tooltip=[
                alt.Tooltip("자산:N", title="자산"),
                alt.Tooltip("비중:Q", title="목표 비중", format=".1%"),
            ],
        )
        .properties(height=max(180, 42 * len(latest_allocation)))
    )
    st.altair_chart(latest_chart, use_container_width=True)

    st.subheader("Sector return source at latest month")
    latest_sources = result.sector_sources.iloc[-1].rename("source").reset_index()
    latest_sources.columns = ["sector", "return source"]
    st.dataframe(latest_sources, use_container_width=True, hide_index=True)

with monthly_tab:
    st.subheader("Monthly returns")
    monthly_table = annual_monthly_table(monthly)
    st.dataframe(monthly_table.style.format("{:.1%}", na_rep="-"), use_container_width=True)
    st.caption("마지막 열 ‘연간’은 해당 연도 월별 수익률을 복리로 합산한 값입니다.")

    st.subheader("Annual performance: strategy vs KOSPI200")
    benchmark = st.session_state["v2_benchmark"]
    if benchmark.empty:
        st.warning("KOSPI200 기준지수를 조회하지 못해 전략 결과만 표시합니다.")
        if st.session_state.get("v2_benchmark_error"):
            st.caption(f"기준지수 조회 오류: {st.session_state['v2_benchmark_error']}")
    else:
        comparison = annual_comparison(monthly, benchmark)
        comparison.index.name = "연도"
        chart_data = comparison.reset_index()
        chart_data["연도"] = chart_data["연도"].astype(str)
        chart_data = chart_data.melt(id_vars="연도", var_name="구분", value_name="수익률")
        chart = (
            alt.Chart(chart_data)
            .mark_bar()
            .encode(
                x=alt.X("연도:N", title="연도"),
                xOffset=alt.XOffset("구분:N", title=None),
                y=alt.Y("수익률:Q", title="연간 수익률", axis=alt.Axis(format=".0%")),
                color=alt.Color("구분:N", title=None),
                tooltip=[
                    alt.Tooltip("연도:N", title="연도"),
                    alt.Tooltip("구분:N", title="구분"),
                    alt.Tooltip("수익률:Q", title="수익률", format=".1%"),
                ],
            )
            .properties(height=420)
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption("전략과 KOSPI200의 연간 수익률을 좌우 막대로 비교합니다. KOSPI200 조회가 불가능하면 KODEX 200(069500)을 대용 기준으로 사용하며 배당은 반영하지 않습니다.")

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
    st.write({"etf_transition_start": config["etf_transition_start"]})
