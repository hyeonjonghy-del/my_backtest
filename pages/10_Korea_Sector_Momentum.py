from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "strategies" / "korea_sector_momentum"
sys.path.insert(0, str(STRATEGY_DIR))

from run import load_kospi200_monthly_prices, load_monthly_prices  # noqa: E402
from strategy import backtest, metrics  # noqa: E402


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
    benchmark_returns = kospi200.pct_change()
    benchmark_returns = benchmark_returns.reindex(strategy_returns.index)
    comparison = pd.DataFrame({
        "전략": strategy_returns,
        "KOSPI200": benchmark_returns,
    }).dropna(how="any")
    return comparison.groupby(comparison.index.year).agg(
        전략=("전략", lambda values: (1.0 + values).prod() - 1.0),
        KOSPI200=("KOSPI200", lambda values: (1.0 + values).prod() - 1.0),
    )


st.set_page_config(page_title="Korea Sector Momentum", layout="wide")
st.title("Korea Sector Momentum")
st.caption("10개 섹터 중 연 1회 상위 5개를 선정하고, 월별 모멘텀과 하락 필터로 비중을 조절합니다.")

config = json.loads((STRATEGY_DIR / "config.json").read_text(encoding="utf-8"))

with st.sidebar:
    st.subheader("Backtest settings")
    with st.form("backtest_form"):
        start = st.date_input("Start", value=dt.date(2017, 1, 1))
        end = st.date_input("End", value=dt.date(2026, 8, 31))
        submitted = st.form_submit_button("Run KRX backtest", type="primary")

st.info("기본 룰: 연 1회 12개월 모멘텀으로 5개 섹터 선정, 월별 45/30/15/5/5 배분, 6개월 수익률이 음수인 섹터는 현금화.")

if submitted:
    if start >= end:
        st.error("Start 날짜는 End 날짜보다 빨라야 합니다.")
        st.stop()

    start_text = start.isoformat()
    end_text = end.isoformat()
    with st.spinner("KRX 데이터와 KOSPI200 기준지수를 조회하고 백테스트 중입니다..."):
        prices = load_monthly_prices(config["sectors"], start_text, end_text)
        benchmark_error = None
        try:
            kospi200 = load_kospi200_monthly_prices(start_text, end_text)
        except Exception as exc:
            kospi200 = None
            benchmark_error = str(exc)
        result = backtest(
            prices,
            config["sectors"],
            start_text,
            end_text,
            weights=config["weights"],
            selection_lookback=config["selection_lookback_months"],
            ranking_lookback=config["ranking_lookback_months"],
            downside_lookback=config["downside_lookback_months"],
            transaction_cost=config["transaction_cost"],
        )
    st.session_state["monthly"] = result.monthly
    st.session_state["metrics"] = metrics(result.monthly)
    st.session_state["kospi200"] = kospi200 if kospi200 is not None else pd.Series(dtype=float)
    st.session_state["benchmark_error"] = benchmark_error

if "metrics" in st.session_state:
    values = st.session_state["metrics"]
    monthly = st.session_state["monthly"].copy()
    monthly["cumulative_return"] = monthly["wealth"] - 1.0
    monthly["drawdown"] = monthly["wealth"] / monthly["wealth"].cummax() - 1.0

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
        performance = monthly[["wealth"]].rename(columns={"wealth": "Portfolio value"})
        st.line_chart(performance, use_container_width=True)

        st.subheader("Drawdown / MDD")
        st.area_chart(
            monthly[["drawdown"]].rename(columns={"drawdown": "Drawdown"}),
            use_container_width=True,
        )
        st.caption(f"최대 낙폭(MDD): {values['max_drawdown']:.1%}. 누적자산은 초기 투자금 1.0 대비 계산됩니다.")

    with monthly_tab:
        st.subheader("Monthly returns")
        monthly_table = annual_monthly_table(monthly)
        st.dataframe(
            monthly_table.style.format("{:.1%}", na_rep="-"),
            use_container_width=True,
        )
        st.caption("월별 수익률 표의 마지막 열 ‘연간’은 해당 연도 1월부터 12월까지의 복리 누적수익률입니다.")

        st.subheader("Annual performance: strategy vs KOSPI200")
        if st.session_state["kospi200"].empty:
            st.warning("KOSPI200 기준지수를 조회하지 못해 전략 결과만 표시합니다.")
            if st.session_state.get("benchmark_error"):
                st.caption(f"기준지수 조회 오류: {st.session_state['benchmark_error']}")
        else:
            comparison = annual_comparison(monthly, st.session_state["kospi200"])
            comparison.index.name = "연도"
            chart_data = comparison.reset_index()
            chart_data["연도"] = chart_data["연도"].astype(str)
            chart_data = chart_data.melt(
                id_vars="연도",
                var_name="구분",
                value_name="수익률",
            )
            chart = (
                alt.Chart(chart_data)
                .mark_bar()
                .encode(
                    x=alt.X("연도:N", title="연도"),
                    xOffset=alt.XOffset("구분:N", title=None),
                    y=alt.Y(
                        "수익률:Q",
                        title="연간 수익률",
                        axis=alt.Axis(format=".0%"),
                    ),
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
            st.caption("전략과 KOSPI200을 연도별 좌우 막대로 비교합니다. KOSPI200 지수 조회가 불가능한 환경에서는 KODEX 200(069500)을 대용 기준으로 사용하며 배당은 반영하지 않습니다.")

        st.subheader("Recent monthly details")
        st.dataframe(monthly.tail(24), use_container_width=True)
        st.download_button(
            "Download monthly results CSV",
            monthly.to_csv().encode("utf-8-sig"),
            file_name="korea_sector_momentum_monthly.csv",
            mime="text/csv",
        )
else:
    st.warning("왼쪽에서 기간을 확인한 뒤 Run KRX backtest를 눌러주세요.")

with st.expander("Universe and rules", expanded=False):
    st.json(config["sectors"])
    st.write({
        "weights": config["weights"],
        "selection_lookback_months": config["selection_lookback_months"],
        "ranking_lookback_months": config["ranking_lookback_months"],
        "downside_lookback_months": config["downside_lookback_months"],
        "transaction_cost": config["transaction_cost"],
    })
