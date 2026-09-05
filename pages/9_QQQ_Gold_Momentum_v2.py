"""QQQ/GLD/SGOV rank-based momentum strategy v2 for Streamlit."""

from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategies"))

from qqq_gld_sgov_momentum_v2.strategy import ASSETS, backtest, make_sgov_bil_proxy  # noqa: E402


DATA_TICKERS = ("QQQ", "GLD", "BIL", "SGOV")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_prices(start_date: date, end_date: date) -> pd.DataFrame:
    start_ts = int(pd.Timestamp(start_date, tz="UTC").timestamp())
    # Yahoo's period2 is exclusive, so include the selected end date.
    end_ts = int((pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    series = []
    for ticker in DATA_TICKERS:
        url = (
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={start_ts}&period2={end_ts}&interval=1d&events=div%2Csplits"
        )
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        response.raise_for_status()
        chart = response.json()["chart"]
        if not chart.get("result"):
            if ticker == "SGOV":
                series.append(pd.Series(dtype=float, name=ticker))
                continue
            raise ValueError(f"{ticker} 데이터를 받지 못했습니다: {chart.get('error')}")
        payload = chart["result"][0]
        index = pd.to_datetime(payload["timestamp"], unit="s", utc=True).tz_convert(None).normalize()
        values = payload["indicators"]["adjclose"][0]["adjclose"]
        series.append(pd.Series(values, index=index, name=ticker).dropna())
    return make_sgov_bil_proxy(pd.concat(series, axis=1))


def annual_monthly_table(result: pd.DataFrame) -> pd.DataFrame:
    month_end_wealth = result["Wealth"].resample("ME").last()
    monthly_returns = month_end_wealth.pct_change(fill_method=None)
    if len(monthly_returns):
        monthly_returns.iloc[0] = month_end_wealth.iloc[0] / result["Wealth"].iloc[0] - 1.0
    frame = monthly_returns.rename("수익률").to_frame()
    frame["연도"] = frame.index.year
    frame["월"] = frame.index.month
    table = frame.pivot(index="연도", columns="월", values="수익률").reindex(columns=range(1, 13))
    table["연간"] = monthly_returns.groupby(monthly_returns.index.year).apply(
        lambda values: (1.0 + values).prod() - 1.0
    )
    table.columns = [f"{month}월" for month in range(1, 13)] + ["연간"]
    return table


st.set_page_config(page_title="QQQ · GLD · SGOV Momentum v2", layout="wide")
st.title("9. QQQ · GLD · SGOV 모멘텀 전략 v2")
st.caption("12개월 모멘텀 순위로 현금 비중을 정하고, 나머지를 QQQ와 GLD의 8:2 상대 모멘텀 전략에 배분합니다.")

st.info(
    "고정 배분 규칙: SGOV 3위 → 현금 0% · SGOV 2위 → 현금 20% · "
    "SGOV 1위 → 현금 40%. 나머지 비중은 QQQ와 GLD 중 강한 자산 80%, 약한 자산 20%입니다."
)

rule_table = pd.DataFrame(
    [
        {"SGOV 12개월 모멘텀 순위": "3위", "SGOV": "0%", "강한 위험자산": "80%", "약한 위험자산": "20%"},
        {"SGOV 12개월 모멘텀 순위": "2위", "SGOV": "20%", "강한 위험자산": "64%", "약한 위험자산": "16%"},
        {"SGOV 12개월 모멘텀 순위": "1위", "SGOV": "40%", "강한 위험자산": "48%", "약한 위험자산": "12%"},
    ]
)
st.dataframe(rule_table, use_container_width=True, hide_index=True)
st.caption("모멘텀이 정확히 같으면 보수적으로 SGOV, GLD, QQQ 순으로 우선합니다. 신호는 전월 말 확정 후 다음 달부터 적용합니다.")
st.caption("현금 데이터는 SGOV 상장 전에는 BIL을 사용하고, SGOV 수익률이 제공되기 시작하면 SGOV로 자동 전환합니다.")

with st.sidebar:
    st.header("v2 전략 설정")
    st.text_input("모멘텀 기간", value="12개월 (고정)", disabled=True)
    rebalance_months = st.selectbox(
        "리밸런싱 주기", (1, 3, 6, 12), index=0, format_func=lambda value: f"{value}개월"
    )
    cost_bps = st.number_input("거래비용 (편도, bp)", min_value=0.0, value=10.0, step=1.0)
    today = date.today()
    start_date = st.date_input(
        "데이터 시작일", value=date(2015, 1, 1), min_value=date(2007, 5, 30), max_value=today
    )
    end_date = st.date_input(
        "데이터 종료일", value=today, min_value=date(2007, 5, 30), max_value=today
    )
    run = st.button("v2 백테스트 실행", type="primary", use_container_width=True)

if not run:
    st.info("왼쪽에서 기간과 리밸런싱 주기를 선택한 뒤 ‘v2 백테스트 실행’을 누르세요.")
    st.stop()

if start_date > end_date:
    st.error("데이터 시작일은 종료일보다 빠르거나 같아야 합니다.")
    st.stop()

with st.spinner("QQQ, GLD, BIL, SGOV 데이터를 내려받아 v2를 계산 중입니다..."):
    try:
        prices = fetch_prices(start_date, end_date)
        result, metrics = backtest(
            prices,
            rebalance_months=rebalance_months,
            momentum_months=12,
            cost_bps=cost_bps,
        )
    except Exception as exc:
        st.error(f"데이터를 불러오거나 계산하는 중 오류가 발생했습니다: {exc}")
        st.stop()

latest = result.iloc[-1]
latest_date = result.index[-1].strftime("%Y-%m-%d")
st.success(
    f"12개월 모멘텀 · {rebalance_months}개월 리밸런싱 | "
    f"실제 계산 기간: {metrics['시작일']} ~ {metrics['종료일']}"
)
st.info(
    f"현재 목표 비중 (기준일 {latest_date}, SGOV 모멘텀 {int(latest['SGOV rank'])}위) | "
    f"QQQ {latest['Target QQQ']:.0%} / GLD {latest['Target GLD']:.0%} / SGOV {latest['Target SGOV']:.0%}"
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("CAGR", f"{metrics['CAGR']:.2%}")
c2.metric("MDD", f"{metrics['MDD']:.2%}")
c3.metric("최종 배수", f"{metrics['최종 배수']:.2f}x")
c4.metric("연 변동성", f"{metrics['연 변동성']:.2%}")
c5.metric("평균 SGOV 비중", f"{metrics['평균 SGOV 비중']:.2%}")

overview_tab, history_tab, returns_tab = st.tabs(["전략 결과", "리밸런싱 이력", "월별·연도별 수익률"])

with overview_tab:
    st.subheader("자산 및 포트폴리오 성장곡선")
    growth = prices.loc[result.index].div(prices.loc[result.index[0]])
    growth["전략 포트폴리오"] = result["Wealth"] / result["Wealth"].iloc[0]
    st.line_chart(growth)

    st.subheader("MDD 추이")
    st.line_chart((result["Wealth"] / result["Wealth"].cummax() - 1.0).rename("Drawdown"))

    st.subheader("실제 비중 변화")
    weights = result[[f"Actual {asset}" for asset in ASSETS]].rename(
        columns={f"Actual {asset}": asset for asset in ASSETS}
    )
    weights_long = weights.reset_index().melt(id_vars="Date", var_name="자산", value_name="비중")
    st.vega_lite_chart(
        weights_long,
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "mark": {"type": "area", "opacity": 0.9},
            "encoding": {
                "x": {"field": "Date", "type": "temporal", "title": "날짜"},
                "y": {
                    "field": "비중", "type": "quantitative", "stack": "zero",
                    "scale": {"domain": [0, 1]}, "axis": {"format": ".0%", "title": "비중"},
                },
                "color": {"field": "자산", "type": "nominal", "title": "자산"},
                "tooltip": [
                    {"field": "Date", "type": "temporal", "title": "날짜"},
                    {"field": "자산", "type": "nominal", "title": "자산"},
                    {"field": "비중", "type": "quantitative", "format": ".2%", "title": "비중"},
                ],
            },
            "height": 360,
        },
        use_container_width=True,
    )

with history_tab:
    rebalance_table = result.loc[result["Rebalance"], [
        "SGOV rank", "Target QQQ", "Target GLD", "Target SGOV",
        "Actual QQQ", "Actual GLD", "Actual SGOV", "Turnover",
    ]].copy()
    rebalance_table.index = rebalance_table.index.strftime("%Y-%m-%d")
    rebalance_table.index.name = "리밸런싱일"
    rebalance_table["SGOV rank"] = rebalance_table["SGOV rank"].map(lambda value: f"{int(value)}위")
    for column in rebalance_table.columns[1:]:
        rebalance_table[column] = rebalance_table[column].map(lambda value: f"{value:.2%}")
    st.dataframe(rebalance_table, use_container_width=True)
    st.caption("목표 비중은 해당 리밸런싱일에 적용된 전월 말 신호입니다.")

with returns_tab:
    st.dataframe(annual_monthly_table(result).style.format("{:.2%}", na_rep="-"), use_container_width=True)
    st.caption("시작월과 종료월은 선택 기간에 따라 부분 기간 수익률일 수 있습니다.")
