"""QQQ/Gold relative-momentum strategy page for Streamlit."""

import json
from datetime import date

import numpy as np
import pandas as pd
import requests
import streamlit as st


MARKETS = {
    "미국: QQQ + GLD": {
        "tickers": ("QQQ", "GLD"),
        "names": ("QQQ", "GLD"),
    },
    "한국: TIGER 미국나스닥100 (133690) + ACE KRX금현물 (411060)": {
        "tickers": ("133690.KS", "411060.KS"),
        "names": ("TIGER 미국나스닥100 (133690)", "ACE KRX금현물 (411060)"),
    },
}
PERIODS = [1, 3, 6, 12]
MARKET_MIN_DATES = {
    "미국: QQQ + GLD": date(2004, 11, 19),
    "한국: TIGER 미국나스닥100 (133690) + ACE KRX금현물 (411060)": date(2021, 12, 15),
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_prices(tickers, start_date, end_date):
    start_ts = int(pd.Timestamp(start_date, tz="UTC").timestamp())
    end_ts = int(pd.Timestamp(end_date, tz="UTC").timestamp())
    series = []
    for ticker in tickers:
        url = (
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={start_ts}&period2={end_ts}&interval=1d&events=div%2Csplits"
        )
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        index = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(None).normalize()
        values = result["indicators"]["adjclose"][0]["adjclose"]
        series.append(pd.Series(values, index=index, name=ticker).dropna())
    return pd.concat(series, axis=1).dropna()


def backtest(prices, rebalance_months, momentum_months, cost_bps):
    month_end = prices.resample("ME").last()
    momentum = month_end.pct_change(momentum_months)
    target = pd.DataFrame(index=month_end.index, columns=prices.columns, dtype=float)
    first_stronger = (momentum.iloc[:, 0] > momentum.iloc[:, 1]) & (momentum.iloc[:, 0] > 0)
    second_stronger = (momentum.iloc[:, 1] >= momentum.iloc[:, 0]) & (momentum.iloc[:, 1] > 0)
    target.loc[first_stronger, prices.columns[0]] = 0.80
    target.loc[first_stronger, prices.columns[1]] = 0.20
    target.loc[second_stronger, prices.columns[0]] = 0.20
    target.loc[second_stronger, prices.columns[1]] = 0.80

    # If both momentum readings are non-positive, keep the less-bad asset at 80%.
    valid = momentum.notna().all(axis=1)
    neither = valid & target.isna().all(axis=1)
    first_less_bad = (momentum.iloc[:, 0] >= momentum.iloc[:, 1]) & neither
    target.loc[first_less_bad, prices.columns[0]] = 0.80
    target.loc[first_less_bad, prices.columns[1]] = 0.20
    target.loc[neither & ~first_less_bad, prices.columns[0]] = 0.20
    target.loc[neither & ~first_less_bad, prices.columns[1]] = 0.80

    # Use the previous month's completed signal for the next period.
    target = target.ffill().shift(1)
    target.index = target.index.to_period("M")
    daily_target = target.reindex(prices.index.to_period("M")).ffill()
    daily_target.index = prices.index
    daily_returns = prices.pct_change().fillna(0.0)
    rebalance_periods = {
        period for period in target.index
        if (period.month - 1) % rebalance_months == 0
    }

    wealth = 1.0
    actual = None
    previous_period = None
    rows = []
    for day, daily_return in daily_returns.iterrows():
        desired = daily_target.loc[day]
        if desired.isna().any():
            continue
        period = day.to_period("M")
        if actual is None:
            actual = desired.copy()
        is_rebalance = period != previous_period and period in rebalance_periods
        turnover = float((desired - actual).abs().sum()) if is_rebalance else 0.0
        if is_rebalance:
            wealth *= 1 - turnover * cost_bps / 10000
            actual = desired.copy()
        wealth *= float((actual * (1 + daily_return)).sum())
        actual = actual * (1 + daily_return)
        actual = actual / actual.sum()
        rows.append((
            day, wealth, desired.iloc[0], desired.iloc[1],
            actual.iloc[0], actual.iloc[1], is_rebalance, turnover
        ))
        previous_period = period

    result = pd.DataFrame(
        rows, columns=[
            "Date", "Wealth", "Target first weight", "Target second weight",
            "Actual first weight", "Actual second weight", "Rebalance", "Turnover"
        ]
    ).set_index("Date")
    returns = result.Wealth.pct_change().fillna(0.0)
    years = (result.index[-1] - result.index[0]).days / 365.2425
    drawdown = result.Wealth / result.Wealth.cummax() - 1
    volatility = returns.std(ddof=1) * np.sqrt(252)
    metrics = {
        "시작일": str(result.index[0].date()),
        "종료일": str(result.index[-1].date()),
        "최종 배수": result.Wealth.iloc[-1],
        "CAGR": result.Wealth.iloc[-1] ** (1 / years) - 1,
        "MDD": drawdown.min(),
        "연 변동성": volatility,
        "Sharpe": returns.mean() * 252 / volatility,
        "평균 첫 번째 자산 비중": result["Actual first weight"].mean(),
        "리밸런싱 횟수": int(result.Rebalance.sum()),
    }
    return result, metrics


st.set_page_config(page_title="QQQ · Gold Momentum", layout="wide")
st.title("9. QQQ · 금 상대 모멘텀 전략")
st.caption("최근 모멘텀이 더 강한 자산 80%, 다른 자산 20% — 다음 리밸런싱 구간부터 적용")

with st.sidebar:
    st.header("전략 설정")
    market_label = st.selectbox("시장", list(MARKETS.keys()))
    rebalance_months = st.selectbox("리밸런싱 주기", PERIODS, index=0, format_func=lambda x: f"{x}개월")
    momentum_months = st.selectbox("모멘텀 기간", PERIODS, index=3, format_func=lambda x: f"{x}개월")
    cost_bps = st.number_input("거래비용 (편도, bp)", min_value=0.0, value=10.0, step=1.0)
    today = date.today()
    min_date = MARKET_MIN_DATES[market_label]
    default_start = max(date(2021, 1, 1), min_date)
    start_date = st.date_input(
        "데이터 시작일", value=default_start, min_value=min_date, max_value=today
    )
    end_date = st.date_input(
        "데이터 종료일", value=today, min_value=min_date, max_value=today
    )
    run = st.button("백테스트 실행", type="primary", use_container_width=True)

if run:
    tickers = MARKETS[market_label]["tickers"]
    display_names = MARKETS[market_label]["names"]
    if start_date > end_date:
        st.error("데이터 시작일은 종료일보다 빠르거나 같아야 합니다.")
        st.stop()
    with st.spinner("시장 데이터를 내려받아 계산 중입니다..."):
        try:
            prices = fetch_prices(tickers, start_date, end_date)
            result, metrics = backtest(prices, rebalance_months, momentum_months, cost_bps)
        except Exception as exc:
            st.error(f"데이터를 불러오거나 계산하는 중 오류가 발생했습니다: {exc}")
            st.stop()

    st.success(
        f"{market_label} 백테스트 완료 | 입력 기간: {start_date} ~ {end_date} | "
        f"실제 계산 기간: {metrics['시작일']} ~ {metrics['종료일']}"
    )
    latest = result.iloc[-1]
    latest_date = result.index[-1].strftime("%Y-%m-%d")
    st.info(
        f"현재 전략 비중 (기준일 {latest_date}) | "
        f"목표: {display_names[0]} {latest['Target first weight']:.0%} / "
        f"{display_names[1]} {latest['Target second weight']:.0%} | "
        f"실제: {display_names[0]} {latest['Actual first weight']:.2%} / "
        f"{display_names[1]} {latest['Actual second weight']:.2%}"
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CAGR", f"{metrics['CAGR']:.2%}")
    c2.metric("MDD", f"{metrics['MDD']:.2%}")
    c3.metric("최종 배수", f"{metrics['최종 배수']:.2f}x")
    c4.metric("연 변동성", f"{metrics['연 변동성']:.2%}")

    st.subheader("자산 및 포트폴리오 성장곡선")
    growth = prices.loc[result.index].div(prices.loc[result.index[0]])
    growth.columns = list(display_names)
    growth["전략 포트폴리오"] = result.Wealth / result.Wealth.iloc[0]
    st.line_chart(growth)

    st.subheader("자산 가격 비교 (시작일 = 100)")
    normalized_prices = prices.loc[result.index].div(prices.loc[result.index[0]]).mul(100)
    normalized_prices.columns = list(display_names)
    st.line_chart(normalized_prices)
    st.caption("배당·분할 등이 반영된 조정주가를 시작일 기준 100으로 정규화했습니다.")

    st.subheader("MDD 추이")
    drawdown_chart = (result.Wealth / result.Wealth.cummax() - 1).rename("Drawdown")
    st.line_chart(drawdown_chart)

    st.subheader("비중 변화")
    weights = result[["Actual first weight", "Actual second weight"]].rename(
        columns={"Actual first weight": display_names[0], "Actual second weight": display_names[1]}
    )
    weights_long = weights.reset_index().melt(
        id_vars="Date", var_name="자산", value_name="비중"
    )
    st.vega_lite_chart(
        weights_long,
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "mark": {"type": "area", "opacity": 0.9},
            "encoding": {
                "x": {"field": "Date", "type": "temporal", "title": "날짜"},
                "y": {
                    "field": "비중", "type": "quantitative", "stack": "zero",
                    "scale": {"domain": [0, 1]}, "axis": {"format": ".0%", "title": "비중"}
                },
                "color": {"field": "자산", "type": "nominal", "title": "자산"},
                "tooltip": [
                    {"field": "Date", "type": "temporal", "title": "날짜"},
                    {"field": "자산", "type": "nominal", "title": "자산"},
                    {"field": "비중", "type": "quantitative", "format": ".2%", "title": "비중"}
                ]
            },
            "width": "container",
            "height": 360,
        },
        use_container_width=True,
    )

    st.subheader("리밸런싱 날짜 및 자산 비중")
    rebalance_table = result.loc[result.Rebalance, [
        "Target first weight", "Target second weight",
        "Actual first weight", "Actual second weight", "Turnover"
    ]].copy()
    rebalance_table.index = rebalance_table.index.strftime("%Y-%m-%d")
    rebalance_table.columns = [
        f"목표 {display_names[0]}", f"목표 {display_names[1]}",
        f"실제 {display_names[0]}", f"실제 {display_names[1]}", "회전율"
    ]
    for column in rebalance_table.columns:
        rebalance_table[column] = rebalance_table[column].map(lambda value: f"{value:.2%}")
    st.dataframe(rebalance_table, use_container_width=True)
    st.caption("전월 마지막 거래일에 신호를 계산하고, 해당 리밸런싱 월의 첫 거래일에 목표비중으로 조정합니다.")

    st.subheader("상세 지표")
    st.dataframe(pd.DataFrame([metrics]).T.rename(columns={0: "값"}), use_container_width=True)
else:
    st.info("왼쪽에서 시장·리밸런싱 주기·모멘텀 기간을 선택한 뒤 ‘백테스트 실행’을 누르세요.")
