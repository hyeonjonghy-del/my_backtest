"""Samsung Electronics trend and volatility strategy dashboard."""

from __future__ import annotations

from datetime import date, timedelta
from importlib import reload

import numpy as np
import pandas as pd
import streamlit as st

from strategies.samsung_electronics_trend_vol import strategy as samsung_strategy


# Streamlit Cloud can keep an already imported strategy module alive across a
# deployment. Reload it so page controls and the dataclass always use the same
# deployed version.
samsung_strategy = reload(samsung_strategy)
StrategyConfig = samsung_strategy.StrategyConfig
performance_metrics = samsung_strategy.performance_metrics
run_backtest = samsung_strategy.run_backtest


TICKER = "005930"
LEVERAGE_TICKER = "0193W0"
NAME = "삼성전자"
LEVERAGE_NAME = "KODEX 삼성전자단일종목레버리지"


st.set_page_config(page_title="삼성전자 추세·변동성 전략", page_icon="📱", layout="wide")
title_col, run_col = st.columns([5, 1])
with title_col:
    st.title("삼성전자 실전 추세·레버리지 전략 v3")
with run_col:
    st.write("")
    run_button = st.button("백테스트 실행", type="primary", width="stretch", key="run_backtest_top")
st.caption(
    "일반 상승에는 삼성전자, 강한 상승에는 삼성전자와 2배 레버리지 ETF를 함께 보유합니다. "
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


@st.cache_data(show_spinner=False, ttl=3600)
def load_leverage_prices(start: date, end: date) -> pd.DataFrame:
    """Load the listed leveraged ETF; an empty frame triggers synthetic 2x returns."""

    try:
        import yfinance as yf

        raw = yf.download(
            "0193W0.KS",
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

    try:
        from pykrx import stock

        raw = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), LEVERAGE_TICKER)
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
    except Exception:
        return pd.DataFrame(columns=["open", "close", "volume"])


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
    st.subheader("MDD 비교")
    st.line_chart(
        pd.DataFrame(
            {
                f"전략 (MDD {strategy_dd.min():.1%})": strategy_dd,
                f"삼성전자 (MDD {benchmark_dd.min():.1%})": benchmark_dd,
            }
        )
    )


with st.sidebar:
    st.header("백테스트 설정")
    start_date = st.date_input("시작일", date(2019, 1, 1), help="2018년 액면분할 이후를 기본 구간으로 사용합니다.")
    end_date = st.date_input("종료일", date.today())

    st.subheader("신호")
    long_ma_window = st.slider("장기 이동평균 (거래일)", 120, 250, 200, 10)
    fast_ma_window = st.slider("단기 이동평균 (거래일)", 10, 60, 20, 5)
    fast_ma_slope_window = st.slider("단기 이평 상승 확인 (거래일)", 2, 20, 5, 1)
    momentum_window = st.slider("모멘텀 기간 (거래일)", 20, 120, 60, 5)
    recent_range_window = st.slider("최근 고점·저점 기간", 10, 60, 20, 5)
    strong_momentum_pct = st.slider("강한 상승 모멘텀 기준 (%)", 0, 30, 5, 1)
    strong_volatility_cap_pct = st.slider("레버리지 RV20 상한 (%)", 30, 80, 65, 5)

    st.subheader("비중·비용")
    leverage_weight_pct = st.slider("강한 상승 시 레버리지 ETF 비중 (%)", 0, 50, 25, 5)
    early_reentry_weight_pct = st.slider("조기 재진입 삼성전자 비중 (%)", 0, 100, 65, 5)
    crash_drawdown_pct = st.slider("20일 고점 대비 급락 기준 (%)", 5, 30, 15, 1)
    crash_volatility_pct = st.slider("비상 RV20 기준 (%)", 60, 150, 80, 5)
    leverage_expense_pct = st.number_input("레버리지 연 비용 가정 (%)", 0.0, 5.0, 0.29, 0.01)
    fee_bps = st.slider("편도 거래비용 (bp)", 0, 50, 15, 1)


st.markdown(
    f"""
### 기본 운용 규칙

1. 종가가 **MA{long_ma_window} 위**이면 삼성전자 100%를 기본으로 보유합니다.
2. 상승 추세에서 MA{fast_ma_window}가 상승하고 {momentum_window}일 모멘텀이
   {strong_momentum_pct}%를 넘으며 RV20이 {strong_volatility_cap_pct}% 이하면 삼성전자
   {100 - leverage_weight_pct}% + 레버리지 ETF {leverage_weight_pct}%를 보유합니다
   (실질 노출 {100 + leverage_weight_pct}%).
3. 장기 추세 아래에서도 단기 반등이 확인되면 삼성전자 {early_reentry_weight_pct}%로 조기 재진입합니다.
4. 최근 {recent_range_window}일 고점 대비 -{crash_drawdown_pct}% 또는 RV20 {crash_volatility_pct}% 초과 시 전량 현금화합니다.
5. 당일 종가 신호는 다음 거래일 시가에 실행하며 회전율에 거래비용을 적용합니다.
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
    fast_ma_window=fast_ma_window,
    fast_ma_slope_window=fast_ma_slope_window,
    momentum_window=momentum_window,
    recent_range_window=recent_range_window,
    strong_momentum_threshold=strong_momentum_pct / 100,
    strong_volatility_cap=strong_volatility_cap_pct / 100,
    leverage_weight=leverage_weight_pct / 100,
    early_reentry_weight=early_reentry_weight_pct / 100,
    crash_drawdown_threshold=crash_drawdown_pct / 100,
    crash_volatility_threshold=crash_volatility_pct / 100,
    leverage_expense_rate=leverage_expense_pct / 100,
    fee_rate=fee_bps / 10_000,
)

warmup_days = max(long_ma_window, momentum_window, recent_range_window) * 2
fetch_start = start_date - timedelta(days=warmup_days)
with st.spinner("삼성전자와 레버리지 ETF 가격 데이터를 불러오고 있습니다..."):
    prices = load_prices(fetch_start, end_date)
    leverage_prices = load_leverage_prices(fetch_start, end_date)

if prices.empty:
    st.error("가격 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    st.stop()

result = run_backtest(prices, config, leverage_prices).loc[pd.Timestamp(start_date) : pd.Timestamp(end_date)]
if len(result) < 2:
    st.error("선택한 기간의 거래일 데이터가 충분하지 않습니다.")
    st.stop()

latest = result.iloc[-1]
metrics = metric_table(result)
target_samsung_weight = float(latest["target_samsung_weight"])
target_leverage_weight = float(latest["target_leverage_weight"])
target_effective_exposure = float(latest["target_effective_exposure"])
executed_samsung_weight = float(latest["executed_samsung_weight"])
executed_leverage_weight = float(latest["executed_leverage_weight"])

st.subheader("현재 신호")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("기준일", result.index[-1].strftime("%Y-%m-%d"))
c2.metric("다음 시가 원주", f"{target_samsung_weight:.0%}")
c3.metric("다음 시가 레버리지", f"{target_leverage_weight:.0%}")
c4.metric("실질 주식 노출", f"{target_effective_exposure:.0%}")
c5.metric("현재 상태", str(latest["regime"]))
st.caption(
    f"현재 실행: 삼성전자 {executed_samsung_weight:.0%} + 레버리지 {executed_leverage_weight:.0%} / "
    f"종가 {latest['close']:,.0f}원 / MA{long_ma_window} {latest['long_ma']:,.0f}원 / "
    f"{momentum_window}일 모멘텀 {latest['momentum']:.1%} / "
    f"RV20 {latest['realized_volatility']:.1%} / 20일 고점 대비 {latest['pullback']:.1%}"
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
    width="stretch",
)
leveraged_days = result["executed_leverage_weight"] > 0
actual_leverage_days = int((leveraged_days & result["leverage_return_source"].eq("Actual ETF")).sum())
synthetic_leverage_days = int((leveraged_days & result["leverage_return_source"].eq("Synthetic 2x")).sum())
elapsed_years = max((result.index[-1] - result.index[0]).days / 365.25, 1 / 252)
annual_turnover = float(result["turnover"].sum() / elapsed_years)
st.info(
    f"레버리지 보유일 데이터: 실제 ETF {actual_leverage_days:,}일 / 합성 2배 {synthetic_leverage_days:,}일. "
    f"연평균 회전율: {annual_turnover:.0%}. 상장 전 합성 구간은 실제 운용성과가 아니므로 별도로 해석해야 합니다."
)
strategy_drawdown = result["strategy_nav"] / result["strategy_nav"].cummax() - 1.0
benchmark_drawdown = result["buy_hold_nav"] / result["buy_hold_nav"].cummax() - 1.0
st.caption(
    f"전략 MDD: {strategy_drawdown.min():.1%} / 발생일: {strategy_drawdown.idxmin():%Y-%m-%d}. "
    f"삼성전자 MDD: {benchmark_drawdown.min():.1%} / 발생일: {benchmark_drawdown.idxmin():%Y-%m-%d}. "
    "MDD 비교 차트의 각 선에서 가장 낮은 지점이 해당 자산의 MDD입니다."
)
draw_performance(result)

tab1, tab2, tab3, tab4 = st.tabs(["비중·신호", "연도별 수익률", "손익 감사", "실행 계산기"])
with tab1:
    st.area_chart(
        result[["executed_samsung_weight", "executed_leverage_weight", "executed_cash_weight"]].rename(
            columns={
                "executed_samsung_weight": "삼성전자",
                "executed_leverage_weight": "레버리지 ETF",
                "executed_cash_weight": "현금",
            }
        )
    )
    st.line_chart(result[["executed_effective_exposure"]].rename(columns={"executed_effective_exposure": "실질 주식 노출"}))
    st.line_chart(
        result[["close", "fast_ma", "long_ma"]].rename(
            columns={"close": NAME, "fast_ma": f"MA{fast_ma_window}", "long_ma": f"MA{long_ma_window}"}
        )
    )

with tab2:
    annual = pd.DataFrame(
        {
            "전략": (1 + result["strategy_return"]).groupby(result.index.year).prod() - 1,
            "삼성전자 보유": (1 + result["buy_hold_return"]).groupby(result.index.year).prod() - 1,
        }
    )
    st.bar_chart(annual)
    st.dataframe(annual.style.format("{:.1%}"), width="stretch")
    validation_rows = []
    for label, period_nav in {
        "전반기 2019~2022": result.loc["2019-01-01":"2022-12-31", "strategy_nav"],
        "후반기 2023~현재": result.loc["2023-01-01":, "strategy_nav"],
    }.items():
        if len(period_nav) >= 2:
            period_metrics = performance_metrics(period_nav)
            validation_rows.append({"구간": label, **period_metrics})
    if validation_rows:
        validation = pd.DataFrame(validation_rows).set_index("구간").rename(
            columns={"total_return": "총수익률", "cagr": "CAGR", "mdd": "MDD", "sharpe": "샤프", "calmar": "칼마"}
        )
        st.subheader("기간 분할 검증")
        st.dataframe(
            validation.style.format(
                {"총수익률": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}", "샤프": "{:.2f}", "칼마": "{:.2f}"}
            ),
            width="stretch",
        )
        st.caption("전체기간 성과가 최근 급등 구간에만 의존하는지 확인하기 위한 고정 구간 비교입니다.")

with tab3:
    st.caption(
        "실행비중이 0%가 된 날에도 전일 보유분의 시가 갭과 매도비용은 발생할 수 있습니다. "
        "전일과 당일 비중이 모두 0%인 완전 현금일의 수익률은 0%여야 합니다."
    )
    audit = result[
        [
            "regime",
            "prior_samsung_weight",
            "prior_leverage_weight",
            "executed_samsung_weight",
            "executed_leverage_weight",
            "executed_effective_exposure",
            "overnight_contribution",
            "intraday_contribution",
            "fee_contribution",
            "strategy_return",
            "cash_all_day",
            "leverage_return_source",
        ]
    ].tail(120).sort_index(ascending=False)
    st.dataframe(
        audit.style.format(
            {
                "prior_samsung_weight": "{:.0%}",
                "prior_leverage_weight": "{:.0%}",
                "executed_samsung_weight": "{:.0%}",
                "executed_leverage_weight": "{:.0%}",
                "executed_effective_exposure": "{:.0%}",
                "overnight_contribution": "{:.2%}",
                "intraday_contribution": "{:.2%}",
                "fee_contribution": "{:.3%}",
                "strategy_return": "{:.2%}",
            }
        ),
        width="stretch",
        height=450,
    )
    invalid_cash_rows = result.loc[result["cash_all_day"] & result["strategy_return"].abs().gt(1e-12)]
    if invalid_cash_rows.empty:
        st.success("검증 통과: 완전 현금일의 전략 수익률은 모두 0%입니다.")
    else:
        st.error(f"검증 실패: 완전 현금일 중 {len(invalid_cash_rows)}일에 손익이 발생했습니다.")

with tab4:
    portfolio_value = st.number_input("평가금액 (원)", min_value=0, value=100_000_000, step=1_000_000)
    current_samsung_shares = st.number_input("현재 삼성전자 보유수량", min_value=0, value=0, step=1)
    current_leverage_shares = st.number_input("현재 레버리지 ETF 보유수량", min_value=0, value=0, step=1)
    samsung_price = float(latest["close"])
    leverage_price = float(leverage_prices["close"].dropna().iloc[-1]) if not leverage_prices.empty else np.nan
    target_samsung_shares = int(np.floor(portfolio_value * target_samsung_weight / samsung_price))
    target_leverage_shares = (
        int(np.floor(portfolio_value * target_leverage_weight / leverage_price))
        if np.isfinite(leverage_price) and leverage_price > 0
        else 0
    )
    samsung_delta = target_samsung_shares - int(current_samsung_shares)
    leverage_delta = target_leverage_shares - int(current_leverage_shares)
    p1, p2, p3 = st.columns(3)
    p1.metric("삼성전자 목표", f"{target_samsung_shares:,}주", f"{samsung_delta:+,}주")
    p2.metric("레버리지 목표", f"{target_leverage_shares:,}주", f"{leverage_delta:+,}주")
    p3.metric("레버리지 기준가", f"{leverage_price:,.0f}원" if np.isfinite(leverage_price) else "조회 실패")
    if target_leverage_weight > 0 and not np.isfinite(leverage_price):
        st.error("레버리지 ETF 실시간 가격을 조회하지 못했습니다. 가격 확인 전에는 주문하지 마세요.")
    st.caption("실제 주문 전 다음 시가, 기존 현금, 세금·수수료와 체결 가능 수량을 다시 확인하세요.")

download_columns = [
    "close",
    "long_ma",
    "fast_ma",
    "momentum",
    "realized_volatility",
    "regime",
    "pullback",
    "target_samsung_weight",
    "target_leverage_weight",
    "target_effective_exposure",
    "executed_samsung_weight",
    "executed_leverage_weight",
    "executed_cash_weight",
    "executed_effective_exposure",
    "turnover",
    "fee_cost",
    "overnight_contribution",
    "intraday_contribution",
    "fee_contribution",
    "cash_all_day",
    "leverage_return_source",
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
    "아직 Review 단계입니다. 합성 레버리지 수익은 실제 괴리율·추적오차·선물 롤오버·시장충격을 완전히 반영하지 못합니다. "
    "실제 ETF 상장 후 구간을 별도로 검증하기 전에는 실전 확정 전략으로 사용하지 마세요."
)
