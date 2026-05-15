"""KODEX 200 / leverage trend + realized-volatility filter backtest.

Default strategy
- Bull: KODEX 200 close > MA100 and KODEX 200 RV20 < 50%.
- Bear: cash.
- Execution: check close signal, trade at next session open.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

warnings.filterwarnings("ignore")

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252

st.set_page_config(page_title="KODEX Trend Vol Filter v3", page_icon="KR", layout="wide")
st.title("KODEX 200 / Leverage Trend + Volatility Filter Backtest v3")
st.caption("Default: KODEX 200 > MA100 and KODEX 200 RV20 < 50% -> KODEX 레버리지, otherwise cash")


def normalize_index(obj: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    out = obj.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def finite_return(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    clean_denominator = denominator.where(denominator > 0)
    return finite_return(numerator / clean_denominator)


@st.cache_data(show_spinner=False, ttl=3600)
def load_krx_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    from pykrx import stock

    df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = pd.DataFrame(
        {
            "open": pd.to_numeric(df["시가"], errors="coerce"),
            "high": pd.to_numeric(df["고가"], errors="coerce"),
            "low": pd.to_numeric(df["저가"], errors="coerce"),
            "close": pd.to_numeric(df["종가"], errors="coerce"),
            "volume": pd.to_numeric(df["거래량"], errors="coerce"),
        }
    )
    out = normalize_index(out).dropna(how="all")
    return out.where(out > 0)


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
    nav = nav.replace([np.inf, -np.inf], np.nan).dropna()
    nav = nav[nav > 0]
    ret = finite_return(nav.pct_change()).dropna()
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return {
            "total": 0.0,
            "cagr": 0.0,
            "mdd": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "win_m": 0.0,
            "dd": pd.Series(dtype=float),
            "mdd_date": None,
            "mdd_peak_date": None,
            "mdd_peak_value": 1.0,
            "mdd_trough_value": 1.0,
        }

    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    mdd_date = dd.idxmin()
    peak_date = nav.loc[:mdd_date].idxmax()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("M").last().pct_change().dropna() > 0).mean()
    return {
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "win_m": win_m,
        "dd": dd,
        "mdd_date": mdd_date,
        "mdd_peak_date": peak_date,
        "mdd_peak_value": nav.loc[peak_date],
        "mdd_trough_value": nav.loc[mdd_date],
    }


def build_signals(
    kodex_close: pd.Series,
    kodex_ma: pd.Series,
    realized_vol: pd.Series,
    vol_threshold: float,
    use_vol_filter: bool,
) -> pd.Series:
    trend_ok = kodex_close > kodex_ma
    vol_ok = realized_vol < vol_threshold if use_vol_filter else pd.Series(True, index=kodex_close.index)
    bull = trend_ok.fillna(False) & vol_ok.fillna(False)
    signals = pd.Series("Bear", index=kodex_close.index, dtype=object)
    signals.loc[bull] = "Bull"
    return signals.rename("Signal")


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
    current_state = "Bear"
    current_weights = state_weights[current_state]

    for date in dates:
        nav *= 1 + portfolio_return(current_weights, ret_200_cc.loc[date], ret_lev_cc.loc[date])
        new_state = signals.loc[date]
        new_weights = state_weights[new_state]
        turnover = traded_notional(current_weights, new_weights)
        if turnover > 0:
            before_fee = nav
            nav = apply_fee(nav, fee_rate, turnover)
            trades.append(
                {
                    "날짜": date.date(),
                    "체결": "당일 종가",
                    "이전 상태": current_state,
                    "신규 상태": new_state,
                    "거래회전율": turnover,
                    "비용차감": before_fee - nav,
                    "NAV": nav,
                }
            )
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
            trades.append(
                {
                    "날짜": date.date(),
                    "체결": "다음날 시가",
                    "이전 상태": current_state,
                    "신규 상태": new_state,
                    "거래회전율": turnover,
                    "비용차감": before_fee - nav,
                    "NAV": nav,
                }
            )
        current_state = new_state
        current_weights = new_weights
        nav *= 1 + portfolio_return(current_weights, ret_200_oc.loc[date], ret_lev_oc.loc[date])
        nav_rows.append(nav)
        state_rows.append(current_state)

    return pd.Series(nav_rows, index=dates, name="전략"), pd.Series(state_rows, index=dates, name="상태"), pd.DataFrame(trades)


def add_mdd_marker(fig: go.Figure, metrics: dict[str, object]) -> go.Figure:
    if metrics["mdd_date"] is None:
        return fig
    fig.add_trace(
        go.Scatter(
            x=[metrics["mdd_peak_date"], metrics["mdd_date"]],
            y=[metrics["mdd_peak_value"], metrics["mdd_trough_value"]],
            mode="markers+lines",
            name=f"MDD {metrics['mdd']:.1%}",
            line=dict(color="#B91C1C", width=2, dash="dot"),
            marker=dict(color="#B91C1C", size=8),
        )
    )
    return fig


with st.sidebar:
    st.header("설정")
    st.subheader("기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2016, 5, 16))
    with c2:
        end_date = st.date_input("종료", datetime.today().date())

    st.subheader("신호")
    ma_window = st.slider("KODEX 200 이동평균", 20, 250, 100, 5)
    use_vol_filter = st.checkbox("변동성 필터 사용", value=True)
    vol_source = st.selectbox("변동성 기준", ["KODEX 200", "KODEX 레버리지"], index=0)
    vol_window = st.slider("변동성 계산 기간", 5, 120, 20, 1)
    vol_threshold_pct = st.slider("연율화 변동성 상한 (%)", 10, 120, 50, 5)

    st.subheader("체결 방식")
    execution_mode = st.radio(
        "신호 확인 및 체결",
        ["당일 종가 신호확인 및 종가 매수/매도", "당일 종가 신호확인 후 다음날 시가 매수/매도"],
        index=1,
    )

    st.subheader("보유 비중")
    bull_lev = st.slider("Bull 레버리지 (%)", 0, 100, 100, 5)
    bull_kodex = st.slider("Bull KODEX 200 (%)", 0, 100, 0, 5)
    bull_cash = max(0, 100 - bull_lev - bull_kodex)
    st.caption(f"Bull: 현금 {bull_cash}% + KODEX 200 {bull_kodex}% + 레버리지 {bull_lev}%")

    bear_kodex = st.slider("Bear KODEX 200 (%)", 0, 100, 0, 5)
    bear_cash = 100 - bear_kodex
    st.caption(f"Bear: 현금 {bear_cash}% + KODEX 200 {bear_kodex}%")

    st.subheader("거래비용")
    fee = st.number_input("거래대금당 비용 (%)", value=0.03, step=0.01, min_value=0.0) / 100
    run_btn = st.button("백테스트 실행", type="primary", use_container_width=True)

vol_threshold = vol_threshold_pct / 100

with st.expander("전략 조건", expanded=False):
    vol_clause = f"and {vol_source} RV{vol_window} < {vol_threshold_pct}%" if use_vol_filter else ""
    st.markdown(
        f"""
| 상태 | 조건 | 보유 비중 |
|---|---|---|
| Bull | KODEX 200 종가 > MA{ma_window} {vol_clause} | 현금 {bull_cash}% + KODEX 200 {bull_kodex}% + 레버리지 {bull_lev}% |
| Bear | Bull 조건 미충족 | 현금 {bear_cash}% + KODEX 200 {bear_kodex}% |

RV = 최근 N거래일 일간수익률 표준편차 × √252
"""
    )

if not run_btn:
    st.info("왼쪽 설정을 확인한 뒤 백테스트를 실행하세요. 기본값은 MA100 + RV20 < 50% 레버리지 전략입니다.")
    st.stop()

start_str = start_date.strftime("%Y%m%d")
end_str = end_date.strftime("%Y%m%d")
warmup_days = max(ma_window, vol_window) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="데이터를 불러오는 중...")
progress.progress(20, text="KODEX 200 데이터를 불러오는 중...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(45, text="KODEX 레버리지 데이터를 불러오는 중...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)

if kodex_200.empty or kodex_lev.empty:
    st.error("KODEX ETF 데이터를 불러오지 못했습니다. pykrx 또는 KRX 데이터 연결을 확인하세요.")
    st.stop()

common_idx = kodex_200.index.intersection(kodex_lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 30:
    st.error("백테스트에 필요한 거래일 데이터가 부족합니다.")
    st.stop()

full_idx = kodex_200.index.intersection(kodex_lev.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

kodex_close_full = kodex_200["close"].reindex(full_idx).ffill()
lev_close_full = kodex_lev["close"].reindex(full_idx).ffill()
kodex_ma_full = kodex_close_full.rolling(ma_window).mean()
vol_price = kodex_close_full if vol_source == "KODEX 200" else lev_close_full
realized_vol_full = finite_return(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
signals = build_signals(kodex_close_full, kodex_ma_full, realized_vol_full, vol_threshold, use_vol_filter)

state_weights = {
    "Bear": weight_dict(bear_cash, bear_kodex, 0),
    "Bull": weight_dict(bull_cash, bull_kodex, bull_lev),
}

ret_200_cc = finite_return(kodex_200["close"].pct_change()).reindex(common_idx).fillna(0)
ret_lev_cc = finite_return(kodex_lev["close"].pct_change()).reindex(common_idx).fillna(0)
ret_200_co = safe_divide(kodex_200["open"] - kodex_200["close"].shift(1), kodex_200["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_co = safe_divide(kodex_lev["open"] - kodex_lev["close"].shift(1), kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_200_oc = safe_divide(kodex_200["close"] - kodex_200["open"], kodex_200["open"]).reindex(common_idx).fillna(0)
ret_lev_oc = safe_divide(kodex_lev["close"] - kodex_lev["open"], kodex_lev["open"]).reindex(common_idx).fillna(0)

progress.progress(80, text="백테스트 계산 중...")
if execution_mode == "당일 종가 신호확인 및 종가 매수/매도":
    nav_s, state_s, trade_log = backtest_same_day_close(common_idx, signals, state_weights, ret_200_cc, ret_lev_cc, fee)
else:
    nav_s, state_s, trade_log = backtest_next_open(common_idx, signals, state_weights, ret_200_co, ret_lev_co, ret_200_oc, ret_lev_oc, fee)

benchmark_200 = kodex_200["close"].reindex(common_idx).ffill()
benchmark_200 = benchmark_200 / benchmark_200.iloc[0]
benchmark_lev = kodex_lev["close"].reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]
strategy_metrics = calc_metrics(nav_s)
benchmark_200_metrics = calc_metrics(benchmark_200)
benchmark_lev_metrics = calc_metrics(benchmark_lev)

progress.progress(100, text="완료")
progress.empty()

current_state = state_s.iloc[-1]
current_date = state_s.index[-1].date()
current_weights = state_weights[current_state]
latest_close = kodex_close_full.reindex(common_idx).iloc[-1]
latest_ma = kodex_ma_full.reindex(common_idx).iloc[-1]
latest_vol = realized_vol_full.reindex(common_idx).iloc[-1]

st.success(
    f"현재 상태 ({current_date}): {current_state} | "
    f"현금 {current_weights['cash']:.0%}, KODEX 200 {current_weights['kodex200']:.0%}, "
    f"레버리지 {current_weights['leverage']:.0%}"
)
st.caption(
    f"최신 신호값: KODEX 200 {latest_close:,.0f} / MA{ma_window} {latest_ma:,.0f} / "
    f"{vol_source} RV{vol_window} {latest_vol:.1%} / 상한 {vol_threshold:.0%}"
)

if not trade_log.empty:
    latest_trade = trade_log.iloc[-1]
    if latest_trade["날짜"] == current_date:
        target_state = latest_trade["신규 상태"]
        target_weights = state_weights[target_state]
        st.warning(
            f"구성종목 변경 알림: 전략을 {latest_trade['이전 상태']} -> {target_state}로 바꾸세요.\n\n"
            f"목표 비중: 현금 {target_weights['cash']:.0%}, KODEX 200 {target_weights['kodex200']:.0%}, "
            f"KODEX 레버리지 {target_weights['leverage']:.0%}"
        )

cols = st.columns(6)
cols[0].metric("총 수익률", f"{strategy_metrics['total']:.1%}", f"KODEX200 {benchmark_200_metrics['total']:.1%}")
cols[1].metric("연 수익률", f"{strategy_metrics['cagr']:.1%}", f"KODEX200 {benchmark_200_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"LEV {benchmark_lev_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"LEV {benchmark_lev_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("월 승률", f"{strategy_metrics['win_m']:.1%}")

trade_count = len(trade_log)
backtest_years = max((nav_s.index[-1] - nav_s.index[0]).days / 365.25, 1 / 365.25)
trades_per_year = trade_count / backtest_years
latest_trade_date = str(trade_log["날짜"].iloc[-1]) if trade_count else "-"
trade_cols = st.columns(3)
trade_cols[0].metric("총 매매횟수", f"{trade_count:,}회")
trade_cols[1].metric("연평균 매매횟수", f"{trades_per_year:.1f}회/년")
trade_cols[2].metric("최근 매매일", latest_trade_date)

state_counts = state_s.value_counts().reindex(["Bear", "Bull"]).fillna(0).astype(int)
state_cols = st.columns(2)
for col, state in zip(state_cols, ["Bear", "Bull"]):
    count = state_counts.loc[state]
    col.metric(state, f"{count}일", f"{count / len(state_s):.1%}")

tab_chart, tab_signals, tab_trades, tab_monthly = st.tabs(["성과", "신호", "거래", "월별 수익률"])

with tab_chart:
    st.subheader("누적 수익률")
    nav_chart = pd.DataFrame(
        {
            "전략": nav_s / nav_s.iloc[0] - 1,
            "KODEX 200 B&H": benchmark_200 / benchmark_200.iloc[0] - 1,
            "KODEX 레버리지 B&H": benchmark_lev / benchmark_lev.iloc[0] - 1,
        }
    ) * 100
    st.line_chart(nav_chart, height=360)

    st.subheader("연도별 수익률 비교")
    yearly_strategy = (1 + nav_s.pct_change().fillna(0)).groupby(nav_s.index.year).prod() - 1
    yearly_200 = (1 + benchmark_200.pct_change().fillna(0)).groupby(benchmark_200.index.year).prod() - 1
    yearly_lev = (1 + benchmark_lev.pct_change().fillna(0)).groupby(benchmark_lev.index.year).prod() - 1
    yearly_returns = pd.DataFrame({"전략": yearly_strategy, "KODEX 200": yearly_200, "KODEX 레버리지": yearly_lev}).dropna(how="all") * 100
    yearly_returns.index = yearly_returns.index.astype(str)
    fig_yearly = go.Figure()
    for name, color in [("전략", "#0F766E"), ("KODEX 200", "#2563EB"), ("KODEX 레버리지", "#DC2626")]:
        fig_yearly.add_trace(
            go.Bar(
                x=yearly_returns.index,
                y=yearly_returns[name],
                name=name,
                marker_color=color,
                text=[f"{v:.1f}%" for v in yearly_returns[name]],
                textposition="outside",
            )
        )
    fig_yearly.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_yearly.update_layout(
        barmode="group",
        yaxis_title="수익률 (%)",
        yaxis_ticksuffix="%",
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
        height=380,
        margin=dict(t=50, b=20),
    )
    st.plotly_chart(fig_yearly, use_container_width=True)

    st.subheader("낙폭")
    dd_chart = pd.DataFrame(
        {
            "전략 DD": strategy_metrics["dd"],
            "KODEX 200 DD": benchmark_200_metrics["dd"],
            "KODEX 레버리지 DD": benchmark_lev_metrics["dd"],
        }
    ) * 100
    st.line_chart(dd_chart, height=260)

with tab_signals:
    st.subheader("KODEX 200 추세")
    trend_chart = pd.DataFrame(
        {
            "KODEX 200": kodex_200["close"].reindex(common_idx).ffill(),
            f"MA{ma_window}": kodex_ma_full.reindex(common_idx).ffill(),
        }
    )
    st.line_chart(trend_chart, height=280)

    st.subheader("연율화 실현 변동성")
    vol_chart = pd.DataFrame(
        {
            f"{vol_source} RV{vol_window}": realized_vol_full.reindex(common_idx).ffill() * 100,
            "상한": pd.Series(vol_threshold_pct, index=common_idx),
        }
    )
    st.line_chart(vol_chart, height=260)

    signal_table = pd.DataFrame(
        {
            "신호": signals.reindex(common_idx).ffill(),
            "보유 상태": state_s,
            "KODEX 200": kodex_200["close"].reindex(common_idx).ffill(),
            f"MA{ma_window}": kodex_ma_full.reindex(common_idx).ffill(),
            f"RV{vol_window}": realized_vol_full.reindex(common_idx).ffill(),
        }
    ).tail(40)
    st.dataframe(signal_table, use_container_width=True)

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
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "kodex_trend_vol_v3_trades.csv", "text/csv")

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_200 = benchmark_200.resample("M").last().pct_change().dropna()
    monthly_lev = benchmark_lev.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame({"전략": monthly_strategy, "KODEX 200": monthly_200, "KODEX 레버리지": monthly_lev}).dropna()
    pivot_source = monthly_strategy.to_frame("수익률")
    pivot_source["연도"] = pivot_source.index.year
    pivot_source["월"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="연도", columns="월", values="수익률")
    pivot.columns = [f"{month}월" for month in pivot.columns]
    pivot["연간"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    st.subheader("월별 전략 vs KODEX")
    st.line_chart(monthly, height=260)
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "날짜"}).to_csv(index=False).encode("utf-8-sig"), "kodex_trend_vol_v3_monthly.csv", "text/csv")
