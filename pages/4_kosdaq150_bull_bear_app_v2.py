"""KODEX KOSDAQ150 / leverage bull-bear backtest.

Signals
- KODEX KOSDAQ150 close versus its moving average.
- Previous-day TNX close versus previous-day TNX moving average.

Regimes
- Bear: cash + KODEX KOSDAQ150.
- Bull Mix: KODEX KOSDAQ150 + KODEX KOSDAQ150 leverage.
- Bull Full: KODEX KOSDAQ150 + KODEX KOSDAQ150 leverage.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

warnings.filterwarnings("ignore")

KODEX_200 = "229200"
KODEX_LEVERAGE = "233740"
TRADING_DAYS = 252

st.set_page_config(page_title="KODEX KOSDAQ150 Bull/Bear Backtest", page_icon="KR", layout="wide")
st.title("KODEX 코스닥150 Bull / Bull Mix / Bear Backtest")
st.caption("KODEX 코스닥150 추세 + 전일 TNX 금리 필터 + 종가/익일 시가 체결 선택")


def normalize_index(obj: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    out = obj.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


@st.cache_data(show_spinner=False, ttl=3600)
def load_krx_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    from pykrx import stock

    df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if df.empty:
        return pd.DataFrame(columns=["open", "close"])
    out = pd.DataFrame(
        {
            "open": pd.to_numeric(df["시가"], errors="coerce"),
            "close": pd.to_numeric(df["종가"], errors="coerce"),
        }
    )
    return normalize_index(out).dropna(how="all")


@st.cache_data(show_spinner=False, ttl=3600)
def load_tnx(start_str: str, end_str: str, warmup_days: int) -> pd.Series:
    start = datetime.strptime(start_str, "%Y%m%d") - timedelta(days=warmup_days)
    end = datetime.strptime(end_str, "%Y%m%d") + timedelta(days=2)
    df = yf.download(
        "^TNX",
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
    )
    if df.empty or "Close" not in df:
        return pd.Series(dtype=float, name="TNX")
    return normalize_index(df["Close"].squeeze().rename("TNX")).dropna()


def align_tnx_previous_day(
    tnx: pd.Series,
    target_index: pd.DatetimeIndex,
    tnx_ma_window: int,
) -> tuple[pd.Series, pd.Series]:
    """For each Korean trading day, use only the last TNX value known by the previous calendar day."""
    if tnx.empty:
        return (
            pd.Series(np.nan, index=target_index, name="TNX_PREV"),
            pd.Series(np.nan, index=target_index, name="TNX_MA_PREV"),
        )

    calendar_index = pd.date_range(tnx.index.min(), target_index[-1], freq="D")
    tnx_daily = tnx.reindex(calendar_index).ffill()
    tnx_ma_daily = tnx.rolling(tnx_ma_window).mean().reindex(calendar_index).ffill()

    previous_day = target_index - pd.Timedelta(days=1)
    previous_tnx = tnx_daily.reindex(previous_day, method="ffill")
    previous_tnx_ma = tnx_ma_daily.reindex(previous_day, method="ffill")
    previous_tnx.index = target_index
    previous_tnx_ma.index = target_index
    return previous_tnx.rename("TNX_PREV"), previous_tnx_ma.rename("TNX_MA_PREV")


def state_from_signals(kodex_close: float, kodex_ma: float, tnx_prev: float, tnx_ma_prev: float) -> str:
    if np.isnan(kodex_close) or np.isnan(kodex_ma) or kodex_close < kodex_ma:
        return "Bear"
    if not np.isnan(tnx_prev) and not np.isnan(tnx_ma_prev) and tnx_prev > tnx_ma_prev:
        return "Bull Mix"
    return "Bull Full"


def weight_dict(cash_pct: float, kodex_pct: float, leverage_pct: float) -> dict[str, float]:
    weights = {
        "cash": cash_pct / 100,
        "kodex200": kodex_pct / 100,
        "leverage": leverage_pct / 100,
    }
    total = sum(weights.values())
    if total <= 0:
        return {"cash": 1.0, "kodex200": 0.0, "leverage": 0.0}
    return {key: value / total for key, value in weights.items()}


def portfolio_return(weights: dict[str, float], r200: float, rlev: float) -> float:
    return weights["kodex200"] * r200 + weights["leverage"] * rlev


def traded_notional(old: dict[str, float], new: dict[str, float]) -> float:
    return abs(new["kodex200"] - old["kodex200"]) + abs(new["leverage"] - old["leverage"])


def apply_fee(nav: float, fee_rate: float, turnover: float) -> float:
    return nav * (1 - min(max(fee_rate * turnover, 0.0), 0.99))


def calc_metrics(nav: pd.Series) -> dict[str, object]:
    nav = nav.dropna()
    ret = nav.pct_change().dropna()
    if len(nav) < 2:
        return {"total": 0.0, "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "calmar": 0.0, "win_m": 0.0, "dd": pd.Series(dtype=float)}

    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("M").last().pct_change().dropna() > 0).mean()
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win_m": win_m, "dd": dd}


def backtest_same_day_close(
    dates: pd.DatetimeIndex,
    signals: pd.Series,
    state_weights: dict[str, dict[str, float]],
    ret_200_cc: pd.Series,
    ret_lev_cc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    nav_rows: list[float] = []
    state_rows: list[str] = []
    trades: list[dict[str, object]] = []

    first_signal = signals.shift(1).reindex(dates).ffill().loc[dates[0]]
    current_state = first_signal if isinstance(first_signal, str) else "Bear"
    current_weights = state_weights[current_state]

    for date in dates:
        nav *= 1 + portfolio_return(current_weights, ret_200_cc.loc[date], ret_lev_cc.loc[date])
        new_state = signals.loc[date]
        new_weights = state_weights[new_state]
        turnover = traded_notional(current_weights, new_weights)
        if turnover > 0:
            before_fee = nav
            nav = apply_fee(nav, fee_rate, turnover)
            trades.append({"날짜": date.date(), "체결": "당일 종가", "이전 상태": current_state, "신규 상태": new_state, "거래회전율": turnover, "비용차감": before_fee - nav, "NAV": nav})
        current_state = new_state
        current_weights = new_weights
        nav_rows.append(nav)
        state_rows.append(current_state)

    return pd.Series(nav_rows, index=dates, name="전략"), pd.Series(state_rows, index=dates, name="상태"), pd.DataFrame(trades)


def backtest_next_open(
    dates: pd.DatetimeIndex,
    signals: pd.Series,
    state_weights: dict[str, dict[str, float]],
    ret_200_co: pd.Series,
    ret_lev_co: pd.Series,
    ret_200_oc: pd.Series,
    ret_lev_oc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    nav_rows: list[float] = []
    state_rows: list[str] = []
    trades: list[dict[str, object]] = []

    executable_signals = signals.shift(1).reindex(dates).ffill().fillna("Bear")
    current_state = executable_signals.iloc[0]
    current_weights = state_weights[current_state]

    for i, date in enumerate(dates):
        nav *= 1 + portfolio_return(current_weights, ret_200_co.loc[date], ret_lev_co.loc[date])

        new_state = executable_signals.loc[date]
        new_weights = state_weights[new_state]
        turnover = traded_notional(current_weights, new_weights)
        if i > 0 and turnover > 0:
            before_fee = nav
            nav = apply_fee(nav, fee_rate, turnover)
            trades.append({"날짜": date.date(), "체결": "다음날 시가", "이전 상태": current_state, "신규 상태": new_state, "거래회전율": turnover, "비용차감": before_fee - nav, "NAV": nav})

        current_state = new_state
        current_weights = new_weights
        nav *= 1 + portfolio_return(current_weights, ret_200_oc.loc[date], ret_lev_oc.loc[date])
        nav_rows.append(nav)
        state_rows.append(current_state)

    return pd.Series(nav_rows, index=dates, name="전략"), pd.Series(state_rows, index=dates, name="상태"), pd.DataFrame(trades)


with st.sidebar:
    st.header("설정")
    st.subheader("기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2016, 1, 4))
    with c2:
        end_date = st.date_input("종료", datetime.today().date())

    st.subheader("신호")
    kodex_ma_window = st.slider("KODEX 코스닥150 MA", 20, 250, 120, 5)
    tnx_ma_window = st.slider("TNX MA", 20, 250, 120, 5)

    st.subheader("체결 방식")
    execution_mode = st.radio(
        "신호 확인 및 체결",
        ["당일 종가 신호확인 및 종가 매수/매도", "당일 종가 신호확인 후 다음날 시가 매수/매도"],
        index=1,
    )

    st.subheader("비중 프리셋")
    preset = st.selectbox("프리셋", ["요청 기본값", "MDD 완화 추천", "공격형"], index=0)
    defaults = {
        "요청 기본값": {"bear_cash": 100, "mix_lev": 50, "full_lev": 20},
        "MDD 완화 추천": {"bear_cash": 90, "mix_lev": 20, "full_lev": 25},
        "공격형": {"bear_cash": 80, "mix_lev": 50, "full_lev": 40},
    }[preset]

    st.subheader("Bear 비중")
    bear_cash = st.slider("현금 (%)", 0, 100, defaults["bear_cash"], 5)
    bear_kodex = 100 - bear_cash
    st.caption(f"현금 {bear_cash}% + KODEX 코스닥150 {bear_kodex}%")

    st.subheader("Bull Mix 비중")
    mix_lev = st.slider("Bull Mix 레버리지 (%)", 0, 100, defaults["mix_lev"], 5)
    mix_kodex = 100 - mix_lev
    st.caption(f"KODEX 코스닥150 {mix_kodex}% + 레버리지 {mix_lev}%")

    st.subheader("Bull Full 비중")
    full_lev = st.slider("Bull Full 레버리지 (%)", 0, 100, defaults["full_lev"], 5)
    full_kodex = 100 - full_lev
    st.caption(f"KODEX 코스닥150 {full_kodex}% + 레버리지 {full_lev}%")

    st.subheader("거래비용")
    fee = st.number_input("거래대금당 비용 (%)", value=0.15, step=0.05, min_value=0.0) / 100
    run_btn = st.button("백테스트 실행", type="primary", use_container_width=True)


with st.expander("전략 조건", expanded=False):
    st.markdown(
        f"""
| 상태 | 조건 | 보유 비중 |
|---|---|---|
| Bear | KODEX 코스닥150 < MA{kodex_ma_window} | 현금 {bear_cash}% + KODEX 코스닥150 {bear_kodex}% |
| Bull Mix | KODEX 코스닥150 > MA{kodex_ma_window} and 전일 TNX > 전일 TNX MA{tnx_ma_window} | KODEX 코스닥150 {mix_kodex}% + 레버리지 {mix_lev}% |
| Bull Full | KODEX 코스닥150 > MA{kodex_ma_window} and 전일 TNX <= 전일 TNX MA{tnx_ma_window} | KODEX 코스닥150 {full_kodex}% + 레버리지 {full_lev}% |
"""
    )

if not run_btn:
    st.info("왼쪽 설정을 확인한 뒤 백테스트를 실행하세요.")
    st.stop()

start_str = start_date.strftime("%Y%m%d")
end_str = end_date.strftime("%Y%m%d")
warmup_days = max(kodex_ma_window, tnx_ma_window) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="데이터를 불러오는 중...")
progress.progress(15, text="KODEX 코스닥150 데이터를 불러오는 중...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(35, text="KODEX 코스닥150레버리지 데이터를 불러오는 중...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)
progress.progress(55, text="TNX 데이터를 불러오는 중...")
tnx = load_tnx(start_str, end_str, warmup_days)

if kodex_200.empty or kodex_lev.empty:
    st.error("KODEX 코스닥150 ETF 데이터를 불러오지 못했습니다. pykrx 또는 KRX 데이터 연결을 확인하세요.")
    st.stop()

common_idx = kodex_200.index.intersection(kodex_lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 30:
    st.error("백테스트에 필요한 거래일 데이터가 부족합니다.")
    st.stop()

full_idx = kodex_200.index.intersection(kodex_lev.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

kodex_close_full = kodex_200["close"].reindex(full_idx).ffill()
kodex_ma_full = kodex_close_full.rolling(kodex_ma_window).mean()
tnx_prev_full, tnx_ma_prev_full = align_tnx_previous_day(tnx, full_idx, tnx_ma_window)
if tnx.empty:
    st.warning("TNX 데이터를 불러오지 못해 금리 위험 신호를 Bull Full로 처리합니다.")

signals = pd.Series(
    [state_from_signals(kodex_close_full.loc[d], kodex_ma_full.loc[d], tnx_prev_full.loc[d], tnx_ma_prev_full.loc[d]) for d in full_idx],
    index=full_idx,
    name="신호",
)

state_weights = {
    "Bear": weight_dict(bear_cash, bear_kodex, 0),
    "Bull Mix": weight_dict(0, mix_kodex, mix_lev),
    "Bull Full": weight_dict(0, full_kodex, full_lev),
}

ret_200_cc = kodex_200["close"].pct_change().reindex(common_idx).fillna(0)
ret_lev_cc = kodex_lev["close"].pct_change().reindex(common_idx).fillna(0)
ret_200_co = ((kodex_200["open"] - kodex_200["close"].shift(1)) / kodex_200["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_co = ((kodex_lev["open"] - kodex_lev["close"].shift(1)) / kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_200_oc = ((kodex_200["close"] - kodex_200["open"]) / kodex_200["open"]).reindex(common_idx).fillna(0)
ret_lev_oc = ((kodex_lev["close"] - kodex_lev["open"]) / kodex_lev["open"]).reindex(common_idx).fillna(0)

progress.progress(80, text="백테스트 계산 중...")
if execution_mode == "당일 종가 신호확인 및 종가 매수/매도":
    nav_s, state_s, trade_log = backtest_same_day_close(common_idx, signals, state_weights, ret_200_cc, ret_lev_cc, fee)
else:
    nav_s, state_s, trade_log = backtest_next_open(common_idx, signals, state_weights, ret_200_co, ret_lev_co, ret_200_oc, ret_lev_oc, fee)

benchmark = kodex_200["close"].reindex(common_idx).ffill()
benchmark = benchmark / benchmark.iloc[0]
strategy_metrics = calc_metrics(nav_s)
benchmark_metrics = calc_metrics(benchmark)

progress.progress(100, text="완료")
progress.empty()

current_state = state_s.iloc[-1]
current_date = state_s.index[-1].date()
current_weights = state_weights[current_state]
st.success(
    f"현재 상태 ({current_date}): {current_state} | "
    f"현금 {current_weights['cash']:.0%}, KODEX 코스닥150 {current_weights['kodex200']:.0%}, "
    f"레버리지 {current_weights['leverage']:.0%}"
)

if not trade_log.empty:
    latest_trade = trade_log.iloc[-1]
    if latest_trade["날짜"] == current_date:
        target_state = latest_trade["신규 상태"]
        target_weights = state_weights[target_state]
        st.warning(
            f"구성종목 변경 알림: 전략을 "
            f"{latest_trade['이전 상태']} -> {target_state}로 바꾸세요.\n\n"
            f"목표 비중: 현금 {target_weights['cash']:.0%}, "
            f"KODEX 코스닥150 {target_weights['kodex200']:.0%}, "
            f"KODEX 코스닥150레버리지 {target_weights['leverage']:.0%}"
        )

cols = st.columns(6)
cols[0].metric("총 수익률", f"{strategy_metrics['total']:.1%}", f"BM {benchmark_metrics['total']:.1%}")
cols[1].metric("연 수익률", f"{strategy_metrics['cagr']:.1%}", f"BM {benchmark_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"BM {benchmark_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"BM {benchmark_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}", f"BM {benchmark_metrics['calmar']:.2f}")
cols[5].metric("월 승률", f"{strategy_metrics['win_m']:.1%}")

trade_count = len(trade_log)
backtest_years = max((nav_s.index[-1] - nav_s.index[0]).days / 365.25, 1 / 365.25)
trades_per_year = trade_count / backtest_years
latest_trade_date = str(trade_log["날짜"].iloc[-1]) if trade_count else "-"
trade_cols = st.columns(3)
trade_cols[0].metric("총 매매횟수", f"{trade_count:,}회")
trade_cols[1].metric("연평균 매매횟수", f"{trades_per_year:.1f}회/년")
trade_cols[2].metric("최근 매매일", latest_trade_date)

state_counts = state_s.value_counts().reindex(["Bear", "Bull Mix", "Bull Full"]).fillna(0).astype(int)
state_cols = st.columns(3)
for col, state in zip(state_cols, ["Bear", "Bull Mix", "Bull Full"]):
    count = state_counts.loc[state]
    col.metric(state, f"{count}일", f"{count / len(state_s):.1%}")

tab_chart, tab_signals, tab_trades, tab_monthly = st.tabs(["성과", "신호", "거래", "월별 수익률"])

with tab_chart:
    st.subheader("누적 수익률")
    nav_chart = pd.DataFrame({"전략": nav_s / nav_s.iloc[0] - 1, "KODEX 코스닥150 B&H": benchmark / benchmark.iloc[0] - 1}) * 100
    st.line_chart(nav_chart, height=360)

    st.subheader("연도별 수익률 비교")
    yearly_strategy = (1 + nav_s.pct_change().fillna(0)).groupby(nav_s.index.year).prod() - 1
    yearly_benchmark = (1 + benchmark.pct_change().fillna(0)).groupby(benchmark.index.year).prod() - 1
    yearly_returns = pd.DataFrame(
        {
            "전략": yearly_strategy,
            "KODEX 코스닥150 B&H": yearly_benchmark,
        }
    ).dropna(how="all") * 100
    yearly_returns.index = yearly_returns.index.astype(str)
    fig_yearly = go.Figure()
    fig_yearly.add_trace(
        go.Bar(
            x=yearly_returns.index,
            y=yearly_returns["전략"],
            name="전략",
            marker_color="#1f77b4",
            text=[f"{v:.1f}%" for v in yearly_returns["전략"]],
            textposition="outside",
        )
    )
    fig_yearly.add_trace(
        go.Bar(
            x=yearly_returns.index,
            y=yearly_returns["KODEX 코스닥150 B&H"],
            name="KODEX 코스닥150 B&H",
            marker_color="#ff7f0e",
            text=[f"{v:.1f}%" for v in yearly_returns["KODEX 코스닥150 B&H"]],
            textposition="outside",
        )
    )
    fig_yearly.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_yearly.update_layout(
        barmode="group",
        yaxis_title="수익률 (%)",
        yaxis_ticksuffix="%",
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
        height=360,
        margin=dict(t=50, b=20),
    )
    st.plotly_chart(fig_yearly, use_container_width=True)
    st.subheader("낙폭")
    dd_chart = pd.DataFrame({"전략 DD": strategy_metrics["dd"], "KODEX 코스닥150 DD": benchmark_metrics["dd"]}) * 100
    st.line_chart(dd_chart, height=260)

with tab_signals:
    st.subheader("KODEX 코스닥150 추세와 상태")
    st.line_chart(pd.DataFrame({"KODEX 코스닥150": kodex_200["close"].reindex(common_idx).ffill(), f"MA{kodex_ma_window}": kodex_ma_full.reindex(common_idx).ffill()}), height=280)
    st.dataframe(pd.DataFrame({"신호": signals.reindex(common_idx).ffill(), "보유 상태": state_s}).tail(30), use_container_width=True)
    st.subheader("전일 TNX 금리 신호")
    tnx_chart = pd.DataFrame({"전일 TNX": tnx_prev_full.reindex(common_idx).ffill(), f"전일 TNX MA{tnx_ma_window}": tnx_ma_prev_full.reindex(common_idx).ffill()}).dropna()
    if tnx_chart.empty:
        st.info("표시할 TNX 데이터가 없습니다.")
    else:
        st.line_chart(tnx_chart, height=260)

with tab_trades:
    st.subheader("상태 전환 내역")
    if trade_log.empty:
        st.info("상태 전환이 없었습니다.")
    else:
        shown = trade_log.copy()
        shown["거래회전율"] = shown["거래회전율"].map(lambda x: f"{x:.1%}")
        shown["비용차감"] = shown["비용차감"].map(lambda x: f"{x:.4f}")
        shown["NAV"] = shown["NAV"].map(lambda x: f"{x:.4f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "kodex_bull_bear_trades.csv", "text/csv")

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_benchmark = benchmark.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame({"전략": monthly_strategy, "KODEX 코스닥150": monthly_benchmark}).dropna()
    pivot_source = monthly_strategy.to_frame("수익률")
    pivot_source["연도"] = pivot_source.index.year
    pivot_source["월"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="연도", columns="월", values="수익률")
    pivot.columns = [f"{month}월" for month in pivot.columns]
    pivot["연간"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    st.subheader("월별 전략 vs KODEX 코스닥150")
    st.line_chart(monthly, height=260)
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "날짜"}).to_csv(index=False).encode("utf-8-sig"), "kodex_bull_bear_monthly.csv", "text/csv")
