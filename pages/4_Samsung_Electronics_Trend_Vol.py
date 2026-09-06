"""Samsung Electronics trend and volatility strategy dashboard."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from strategies.samsung_electronics_trend_vol.strategy import (
    StrategyConfig,
    performance_metrics,
    run_backtest,
)


TICKER = "005930"
NAME = "삼성전자"


st.set_page_config(page_title="삼성전자 추세·변동성 전략", page_icon="📱", layout="wide")
st.title("삼성전자 추세·변동성 전략 v2")
st.caption(
    "장기 추세가 깨질 때만 전량 현금화하고, RV20과 모멘텀은 상승 추세 안에서 비중을 줄이는 데 사용합니다. "
    "신호는 종가에 확정되고 다음 거래일 시가에 실행됩니다."
)


@st.cache_data(show_spinner=False, ttl=3600)
def load_prices(start: date, end: date) -> pd.DataFrame:
    """Load split-adjusted daily OHLC, with KRX-compatible data as fallback."""

    try:
        import yfinance as yf

        raw = yf.download(
            "005930.KS",
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
        )
        if not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            frame = raw.rename(columns={"Open": "open", "Close": "close", "Volume": "volume"})
            return frame.loc[:, ["open", "close", "volume"]].dropna(subset=["open", "close"])
    except Exception:
        pass

    from pykrx import stock

    raw = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), TICKER)
    if raw.empty:
        return pd.DataFrame(columns=["open", "close", "volume"])
    frame = pd.DataFrame(
        {
            "open": pd.to_numeric(raw["시가"], errors="coerce"),
            "close": pd.to_numeric(raw["종가"], errors="coerce"),
            "volume": pd.to_numeric(raw["거래량"], errors="coerce"),
        }
    )
    frame.index = pd.to_datetime(frame.index)
    return frame.dropna(subset=["open", "close"])


def metric_table(result: pd.DataFrame) -> pd.DataFrame:
    strategy = performance_metrics(result["strategy_nav"])
    benchmark = performance_metrics(result["buy_hold_nav"])
    return pd.DataFrame(
        {
            "전략": strategy,
            "삼성전자 보유": benchmark,
        }
    ).T


def draw_performance(result: pd.DataFrame) -> None:
    st.line_chart(
        result[["strategy_nav", "buy_hold_nav"]].rename(
            columns={"strategy_nav": "전략", "buy_hold_nav": "삼성전자 보유"}
        )
    )
    strategy_dd = result["strategy_nav"] / result["strategy_nav"].cummax() - 1
    benchmark_dd = result["buy_hold_nav"] / result["buy_hold_nav"].cummax() - 1
    st.line_chart(
        pd.DataFrame(
            {
                "전략 누적 MDD": strategy_dd.cummin(),
                "삼성전자 누적 MDD": benchmark_dd.cummin(),
            }
        )
    )


with st.sidebar:
    st.header("백테스트 설정")
    start_date = st.date_input("시작일", date(2019, 1, 1), help="2018년 액면분할 이후를 기본 구간으로 사용합니다.")
    end_date = st.date_input("종료일", date.today())

    st.subheader("신호")
    long_ma_window = st.slider("장기 이동평균 (거래일)", 60, 250, 120, 10)
    momentum_window = st.slider("모멘텀 기간 (거래일)", 20, 120, 60, 5)
    volatility_window = st.slider("실현변동성 기간 (거래일)", 10, 60, 20, 5)
    volatility_cap_pct = st.slider("RV20 축소 기준 (%)", 25, 80, 65, 5)

    st.subheader("비중·비용")
    target_volatility_pct = st.slider("목표 변동성 (%)", 10, volatility_cap_pct, min(30, volatility_cap_pct), 5)
    min_weight_pct = st.slider("정상 상승 시 최소 비중 (%)", 0, 80, 65, 5)
    max_weight_pct = st.slider("최대 비중 (%)", min_weight_pct, 100, 100, 5)
    weak_momentum_weight_pct = st.slider("모멘텀 약화 시 최대 비중 (%)", 0, max_weight_pct, min(35, max_weight_pct), 5)
    high_volatility_weight_pct = st.slider("RV20 초과 시 비중 (%)", 0, max_weight_pct, min(35, max_weight_pct), 5)
    fee_bps = st.slider("편도 거래비용 (bp)", 0, 50, 15, 1)
    run_button = st.button("백테스트 실행", type="primary", use_container_width=True)


st.markdown(
    f"""
### 기본 운용 규칙

1. 종가가 **{long_ma_window}일 이동평균 아래**이면 현금으로 대기합니다.
2. 상승 추세에서는 목표 변동성 {target_volatility_pct}%에 맞춰 삼성전자 비중을
   {min_weight_pct}~{max_weight_pct}%로 조절합니다.
3. 상승 추세에서 {momentum_window}일 모멘텀이 음수이면 최대 {weak_momentum_weight_pct}%,
   RV20이 {volatility_cap_pct}%를 넘으면 {high_volatility_weight_pct}%로 비중을 낮춥니다.
4. 오늘 종가 신호는 다음 거래일 시가에 실행하고, 매매 회전율에 거래비용을 적용합니다.
"""
)

if not run_button:
    st.info("왼쪽 설정을 확인한 뒤 **백테스트 실행**을 눌러주세요.")
    st.stop()

if start_date >= end_date:
    st.error("종료일은 시작일보다 뒤여야 합니다.")
    st.stop()

config = StrategyConfig(
    long_ma_window=long_ma_window,
    momentum_window=momentum_window,
    volatility_window=volatility_window,
    volatility_cap=volatility_cap_pct / 100,
    target_volatility=target_volatility_pct / 100,
    min_invested_weight=min_weight_pct / 100,
    max_invested_weight=max_weight_pct / 100,
    weak_momentum_weight=weak_momentum_weight_pct / 100,
    high_volatility_weight=high_volatility_weight_pct / 100,
    fee_rate=fee_bps / 10_000,
)

warmup_days = max(long_ma_window, momentum_window, volatility_window) * 2
fetch_start = start_date - timedelta(days=warmup_days)
with st.spinner("삼성전자 가격 데이터를 불러오고 있습니다..."):
    prices = load_prices(fetch_start, end_date)

if prices.empty:
    st.error("가격 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    st.stop()

result = run_backtest(prices, config).loc[pd.Timestamp(start_date) : pd.Timestamp(end_date)]
if len(result) < 2:
    st.error("선택한 기간의 거래일 데이터가 충분하지 않습니다.")
    st.stop()

latest = result.iloc[-1]
metrics = metric_table(result)
latest_signal_weight = float(latest["target_weight"])
latest_executed_weight = float(latest["executed_weight"])

st.subheader("현재 신호")
c1, c2, c3, c4 = st.columns(4)
c1.metric("기준일", result.index[-1].strftime("%Y-%m-%d"))
c2.metric("다음 시가 목표", f"삼성전자 {latest_signal_weight:.0%}")
c3.metric("현재 실행 비중", f"{latest_executed_weight:.0%}")
c4.metric("현재 상태", str(latest["regime"]))
st.caption(
    f"종가 {latest['close']:,.0f}원 / MA{long_ma_window} {latest['long_ma']:,.0f}원 / "
    f"{momentum_window}일 모멘텀 {latest['momentum']:.1%} / "
    f"실현변동성 {latest['realized_volatility']:.1%}"
)

st.subheader("성과 요약")
display_metrics = metrics.rename(
    columns={
        "total_return": "총수익률",
        "cagr": "CAGR",
        "mdd": "MDD",
        "sharpe": "샤프",
        "calmar": "칼마",
    }
)
st.dataframe(
    display_metrics.style.format(
        {"총수익률": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}", "샤프": "{:.2f}", "칼마": "{:.2f}"}
    ),
    use_container_width=True,
)
strategy_drawdown = result["strategy_nav"] / result["strategy_nav"].cummax() - 1.0
st.caption(
    f"전략 MDD: {strategy_drawdown.min():.1%} / 발생일: {strategy_drawdown.idxmin():%Y-%m-%d}. "
    "아래 차트는 각 시점까지 기록된 최악의 낙폭인 누적 MDD를 표시합니다."
)
draw_performance(result)

tab1, tab2, tab3, tab4 = st.tabs(["비중·신호", "연도별 수익률", "손익 감사", "실행 계산기"])
with tab1:
    st.area_chart(result[["executed_weight", "cash_weight"]].rename(columns={"executed_weight": "삼성전자", "cash_weight": "현금"}))
    st.line_chart(
        result[["close", "long_ma"]].rename(columns={"close": NAME, "long_ma": f"MA{long_ma_window}"})
    )

with tab2:
    annual = pd.DataFrame(
        {
            "전략": (1 + result["strategy_return"]).groupby(result.index.year).prod() - 1,
            "삼성전자 보유": (1 + result["buy_hold_return"]).groupby(result.index.year).prod() - 1,
        }
    )
    st.bar_chart(annual)
    st.dataframe(annual.style.format("{:.1%}"), use_container_width=True)

with tab3:
    st.caption(
        "실행비중이 0%가 된 날에도 전일 보유분의 시가 갭과 매도비용은 발생할 수 있습니다. "
        "전일과 당일 비중이 모두 0%인 완전 현금일의 수익률은 0%여야 합니다."
    )
    audit = result[
        [
            "regime",
            "prior_weight",
            "executed_weight",
            "overnight_contribution",
            "intraday_contribution",
            "fee_contribution",
            "strategy_return",
            "cash_all_day",
        ]
    ].tail(120).sort_index(ascending=False)
    st.dataframe(
        audit.style.format(
            {
                "prior_weight": "{:.0%}",
                "executed_weight": "{:.0%}",
                "overnight_contribution": "{:.2%}",
                "intraday_contribution": "{:.2%}",
                "fee_contribution": "{:.3%}",
                "strategy_return": "{:.2%}",
            }
        ),
        use_container_width=True,
        height=450,
    )
    invalid_cash_rows = result.loc[result["cash_all_day"] & result["strategy_return"].abs().gt(1e-12)]
    if invalid_cash_rows.empty:
        st.success("검증 통과: 완전 현금일의 전략 수익률은 모두 0%입니다.")
    else:
        st.error(f"검증 실패: 완전 현금일 중 {len(invalid_cash_rows)}일에 손익이 발생했습니다.")

with tab4:
    portfolio_value = st.number_input("평가금액 (원)", min_value=0, value=100_000_000, step=1_000_000)
    current_shares = st.number_input("현재 삼성전자 보유수량", min_value=0, value=0, step=1)
    reference_price = float(latest["close"])
    target_value = portfolio_value * latest_signal_weight
    target_shares = int(np.floor(target_value / reference_price)) if reference_price > 0 else 0
    share_delta = target_shares - int(current_shares)
    action = "매수" if share_delta > 0 else "매도" if share_delta < 0 else "유지"
    p1, p2, p3 = st.columns(3)
    p1.metric("목표 보유수량", f"{target_shares:,}주")
    p2.metric("주문 방향", action)
    p3.metric("주문 수량", f"{abs(share_delta):,}주")
    st.caption("실제 주문 전 다음 시가, 기존 현금, 세금·수수료와 체결 가능 수량을 다시 확인하세요.")

download_columns = [
    "close",
    "long_ma",
    "momentum",
    "realized_volatility",
    "regime",
    "target_weight",
    "executed_weight",
    "turnover",
    "fee_cost",
    "overnight_contribution",
    "intraday_contribution",
    "fee_contribution",
    "cash_all_day",
    "strategy_nav",
    "buy_hold_nav",
]
st.download_button(
    "결과 CSV 다운로드",
    result[download_columns].to_csv(index_label="date").encode("utf-8-sig"),
    file_name=f"samsung_strategy_{result.index[-1]:%Y%m%d}.csv",
    mime="text/csv",
)

st.warning(
    "연구용 모델이며 투자 권유가 아닙니다. 배당·세금·시장충격·현금이자는 반영하지 않았고, "
    "단일종목 집중투자는 지수 ETF보다 큰 손실을 낼 수 있습니다."
)
