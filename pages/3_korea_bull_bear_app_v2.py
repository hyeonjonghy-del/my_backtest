"""
KODEX 200 / KODEX Leverage Bull-Bear strategy.

Signals:
- Signal 1: ^TNX versus its moving average
- Signal 2: KODEX 200 versus its moving average

Regimes:
- Bear: cash + KODEX 200
- Bull Mix: KODEX 200 + KODEX Leverage
- Bull Full: KODEX 200 + KODEX Leverage
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

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252

STATE_ORDER = ["Bear", "Bull Mix", "Bull Full"]
STATE_COLOR = {
    "Bear": "#d94841",
    "Bull Mix": "#d99a23",
    "Bull Full": "#157347",
}


st.set_page_config(
    page_title="KODEX Bull/Bear Backtest",
    page_icon="KR",
    layout="wide",
)

st.title("KODEX 200 Bull / Bull Mix / Bear Backtest")
st.caption("TNX 금리 필터 + KODEX 200 추세 필터 + 종가/익일 시가 리밸런싱 선택")


def normalize_index(s: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    obj = s.copy()
    obj.index = pd.to_datetime(obj.index).tz_localize(None).normalize()
    return obj.sort_index()


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
        progress=False,
        auto_adjust=True,
    )
    if df.empty or "Close" not in df:
        return pd.Series(dtype=float, name="TNX")
    close = df["Close"].squeeze().rename("TNX")
    return normalize_index(close).dropna()


def calc_metrics(nav: pd.Series) -> dict:
    nav = nav.dropna()
    ret = nav.pct_change().dropna()
    if len(nav) < 2:
        return {
            "total": 0.0,
            "cagr": 0.0,
            "mdd": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "win_m": 0.0,
            "dd": pd.Series(dtype=float),
        }

    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
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
    }


def state_from_signals(kodex_close: float, kodex_ma: float, tnx: float, tnx_ma: float) -> str:
    if np.isnan(kodex_close) or np.isnan(kodex_ma) or kodex_close < kodex_ma:
        return "Bear"
    if not np.isnan(tnx) and not np.isnan(tnx_ma) and tnx > tnx_ma:
        return "Bull Mix"
    return "Bull Full"


def weight_dict(cash: float, kodex: float, lev: float) -> dict[str, float]:
    return {
        "cash": cash / 100,
        "kodex200": kodex / 100,
        "leverage": lev / 100,
    }


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {"cash": 1.0, "kodex200": 0.0, "leverage": 0.0}
    return {key: value / total for key, value in weights.items()}


def traded_notional(old: dict[str, float], new: dict[str, float]) -> float:
    return abs(new["kodex200"] - old["kodex200"]) + abs(new["leverage"] - old["leverage"])


def portfolio_return(weights: dict[str, float], r200: float, rlev: float) -> float:
    return weights["kodex200"] * r200 + weights["leverage"] * rlev


def apply_fee(nav: float, fee_rate: float, turnover: float) -> float:
    cost = min(max(fee_rate * turnover, 0.0), 0.99)
    return nav * (1 - cost)


def backtest_same_day_close(
    dates: pd.DatetimeIndex,
    signals: pd.Series,
    state_weights: dict[str, dict[str, float]],
    ret_200_cc: pd.Series,
    ret_lev_cc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    nav_rows = []
    held_states = []
    trades = []

    first_date = dates[0]
    prev_signal = signals.shift(1).reindex(dates).ffill().loc[first_date]
    current_state = prev_signal if isinstance(prev_signal, str) else "Bear"
    current_weights = state_weights[current_state]

    for date in dates:
        nav *= 1 + portfolio_return(current_weights, ret_200_cc.loc[date], ret_lev_cc.loc[date])

        new_state = signals.loc[date]
        new_weights = state_weights[new_state]
        turnover = traded_notional(current_weights, new_weights)

        if turnover > 0:
            nav_before_fee = nav
            nav = apply_fee(nav, fee_rate, turnover)
            trades.append(
                {
                    "날짜": date.date(),
                    "체결": "당일 종가",
                    "이전 상태": current_state,
                    "신규 상태": new_state,
                    "거래회전율": turnover,
                    "비용차감": nav_before_fee - nav,
                    "NAV": nav,
                }
            )

        current_state = new_state
        current_weights = new_weights
        nav_rows.append(nav)
        held_states.append(current_state)

    return (
        pd.Series(nav_rows, index=dates, name="전략"),
        pd.Series(held_states, index=dates, name="상태"),
        pd.DataFrame(trades),
    )


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
    nav_rows = []
    held_states = []
    trades = []

    shifted_signals = signals.shift(1).reindex(dates).ffill().fillna("Bear")
    current_state = shifted_signals.iloc[0]
    current_weights = state_weights[current_state]

    for i, date in enumerate(dates):
        nav *= 1 + portfolio_return(current_weights, ret_200_co.loc[date], ret_lev_co.loc[date])

        new_state = shifted_signals.loc[date]
        new_weights = state_weights[new_state]
        turnover = traded_notional(current_weights, new_weights)

        if i > 0 and turnover > 0:
            nav_before_fee = nav
            nav = apply_fee(nav, fee_rate, turnover)
            trades.append(
                {
                    "날짜": date.date(),
                    "체결": "다음날 시가",
                    "이전 상태": current_state,
                    "신규 상태": new_state,
                    "거래회전율": turnover,
                    "비용차감": nav_before_fee - nav,
                    "NAV": nav,
                }
            )

        current_state = new_state
        current_weights = new_weights
        nav *= 1 + portfolio_return(current_weights, ret_200_oc.loc[date], ret_lev_oc.loc[date])

        nav_rows.append(nav)
        held_states.append(current_state)

    return (
        pd.Series(nav_rows, index=dates, name="전략"),
        pd.Series(held_states, index=dates, name="상태"),
        pd.DataFrame(trades),
    )


def make_nav_chart(nav: pd.Series, benchmark: pd.Series) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=nav.index,
            y=(nav / nav.iloc[0] - 1) * 100,
            name="전략",
            line=dict(color="#185FA5", width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=benchmark.index,
            y=(benchmark / benchmark.iloc[0] - 1) * 100,
            name="KODEX 200 B&H",
            line=dict(color="#9DB7D5", width=1.5, dash="dash"),
        )
    )
    fig.update_layout(
        height=360,
        margin=dict(l=0, r=0, t=10, b=0),
        yaxis=dict(ticksuffix="%"),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.08),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def make_dd_chart(strategy_dd: pd.Series, benchmark_dd: pd.Series) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=strategy_dd.index,
            y=strategy_dd * 100,
            name="전략 DD",
            fill="tozeroy",
            line=dict(color="#185FA5", width=1.5),
            fillcolor="rgba(24,95,165,0.16)",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=benchmark_dd.index,
            y=benchmark_dd * 100,
            name="KODEX 200 DD",
            fill="tozeroy",
            line=dict(color="#d94841", width=1.5, dash="dash"),
            fillcolor="rgba(217,72,65,0.10)",
        )
    )
    fig.update_layout(
        height=280,
        margin=dict(l=0, r=0, t=10, b=0),
        yaxis=dict(ticksuffix="%"),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.12),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def make_state_chart(kodex: pd.Series, kodex_ma: pd.Series, states: pd.Series) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=kodex.index, y=kodex, name="KODEX 200", line=dict(color="#185FA5")))
    fig.add_trace(go.Scatter(x=kodex_ma.index, y=kodex_ma, name="MA", line=dict(color="#d94841", dash="dash")))

    start = None
    current = None
    for date, state in states.items():
        if state != current:
            if current is not None and start is not None:
                fig.add_vrect(x0=start, x1=date, fillcolor=STATE_COLOR[current], opacity=0.08, line_width=0)
            start = date
            current = state
    if current is not None and start is not None:
        fig.add_vrect(x0=start, x1=states.index[-1], fillcolor=STATE_COLOR[current], opacity=0.08, line_width=0)

    fig.update_layout(
        height=320,
        margin=dict(l=0, r=0, t=10, b=0),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.08),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


with st.sidebar:
    st.header("설정")

    st.subheader("기간")
    col_start, col_end = st.columns(2)
    with col_start:
        start_date = st.date_input("시작", datetime(2016, 1, 4))
    with col_end:
        end_date = st.date_input("종료", datetime.today().date())

    st.subheader("신호")
    kodex_ma_window = st.slider("KODEX 200 MA", 20, 250, 120, 5)
    tnx_ma_window = st.slider("TNX MA", 20, 250, 120, 5)

    st.subheader("체결 방식")
    execution_mode = st.radio(
        "신호 확인 및 체결",
        ["당일 종가 신호확인 및 종가 매수/매도", "당일 종가 신호확인 후 다음날 시가 매수/매도"],
        index=0,
    )

    st.subheader("비중 프리셋")
    preset = st.selectbox(
        "프리셋",
        ["요청 기본값", "MDD 완화 추천", "공격형"],
        index=0,
    )

    defaults = {
        "요청 기본값": {
            "bear_cash": 100,
            "mix_lev": 50,
            "full_lev": 0,
        },
        "MDD 완화 추천": {
            "bear_cash": 90,
            "mix_lev": 20,
            "full_lev": 25,
        },
        "공격형": {
            "bear_cash": 80,
            "mix_lev": 50,
            "full_lev": 40,
        },
    }[preset]

    st.subheader("Bear 비중")
    bear_cash = st.slider("현금 (%)", 0, 100, defaults["bear_cash"], 5)
    bear_kodex = 100 - bear_cash
    st.caption(f"현금 {bear_cash}% + KODEX 200 {bear_kodex}%")

    st.subheader("Bull Mix 비중")
    mix_lev = st.slider("Bull Mix 레버리지 (%)", 0, 100, defaults["mix_lev"], 5)
    mix_kodex = 100 - mix_lev
    st.caption(f"KODEX 200 {mix_kodex}% + 레버리지 {mix_lev}%")

    st.subheader("Bull Full 비중")
    full_lev = st.slider("Bull Full 레버리지 (%)", 0, 100, defaults["full_lev"], 5)
    full_kodex = 100 - full_lev
    st.caption(f"KODEX 200 {full_kodex}% + 레버리지 {full_lev}%")

    st.subheader("거래비용")
    fee = st.number_input("거래대금당 비용 (%)", value=0.15, step=0.05, min_value=0.0) / 100

    run_btn = st.button("백테스트 실행", type="primary", use_container_width=True)


with st.expander("전략 조건", expanded=False):
    st.markdown(
        f"""
| 상태 | 조건 | 보유 비중 |
|---|---|---|
| Bear | KODEX 200 < MA{kodex_ma_window} | 현금 {bear_cash}% + KODEX 200 {bear_kodex}% |
| Bull Mix | KODEX 200 > MA{kodex_ma_window} and TNX > MA{tnx_ma_window} | KODEX 200 {mix_kodex}% + 레버리지 {mix_lev}% |
| Bull Full | KODEX 200 > MA{kodex_ma_window} and TNX <= MA{tnx_ma_window} | KODEX 200 {full_kodex}% + 레버리지 {full_lev}% |
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

progress.progress(15, text="KODEX 200 데이터를 불러오는 중...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)

progress.progress(35, text="KODEX 레버리지 데이터를 불러오는 중...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)

progress.progress(55, text="TNX 데이터를 불러오는 중...")
tnx = load_tnx(start_str, end_str, warmup_days)

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
kodex_ma_full = kodex_close_full.rolling(kodex_ma_window).mean()

tnx_daily_idx = pd.date_range(tnx.index.min(), full_idx[-1], freq="D") if not tnx.empty else pd.DatetimeIndex([])
if len(tnx_daily_idx) > 0:
    tnx_aligned_full = tnx.reindex(tnx_daily_idx).ffill().reindex(full_idx).ffill()
    tnx_ma_full = tnx.rolling(tnx_ma_window).mean().reindex(tnx_daily_idx).ffill().reindex(full_idx).ffill()
else:
    tnx_aligned_full = pd.Series(np.nan, index=full_idx, name="TNX")
    tnx_ma_full = pd.Series(np.nan, index=full_idx, name="TNX_MA")
    st.warning("TNX 데이터를 불러오지 못해 금리 위험 신호를 Bull Full로 처리합니다.")

signals = pd.Series(
    [
        state_from_signals(
            kodex_close_full.loc[date],
            kodex_ma_full.loc[date],
            tnx_aligned_full.loc[date],
            tnx_ma_full.loc[date],
        )
        for date in full_idx
    ],
    index=full_idx,
    name="신호",
)

state_weights = {
    "Bear": normalize_weights(weight_dict(bear_cash, bear_kodex, 0)),
    "Bull Mix": normalize_weights(weight_dict(0, mix_kodex, mix_lev)),
    "Bull Full": normalize_weights(weight_dict(0, full_kodex, full_lev)),
}

ret_200_cc = kodex_200["close"].pct_change().reindex(common_idx).fillna(0)
ret_lev_cc = kodex_lev["close"].pct_change().reindex(common_idx).fillna(0)

ret_200_co = ((kodex_200["open"] - kodex_200["close"].shift(1)) / kodex_200["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_co = ((kodex_lev["open"] - kodex_lev["close"].shift(1)) / kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_200_oc = ((kodex_200["close"] - kodex_200["open"]) / kodex_200["open"]).reindex(common_idx).fillna(0)
ret_lev_oc = ((kodex_lev["close"] - kodex_lev["open"]) / kodex_lev["open"]).reindex(common_idx).fillna(0)

progress.progress(80, text="백테스트 계산 중...")

if execution_mode == "당일 종가 신호확인 및 종가 매수/매도":
    nav_s, state_s, trade_log = backtest_same_day_close(
        common_idx,
        signals,
        state_weights,
        ret_200_cc,
        ret_lev_cc,
        fee,
    )
else:
    nav_s, state_s, trade_log = backtest_next_open(
        common_idx,
        signals,
        state_weights,
        ret_200_co,
        ret_lev_co,
        ret_200_oc,
        ret_lev_oc,
        fee,
    )

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
    f"현금 {current_weights['cash']:.0%}, KODEX 200 {current_weights['kodex200']:.0%}, "
    f"레버리지 {current_weights['leverage']:.0%}"
)

metric_cols = st.columns(6)
metric_cols[0].metric("총 수익률", f"{strategy_metrics['total']:.1%}", f"BM {benchmark_metrics['total']:.1%}")
metric_cols[1].metric("연 수익률", f"{strategy_metrics['cagr']:.1%}", f"BM {benchmark_metrics['cagr']:.1%}")
metric_cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"BM {benchmark_metrics['mdd']:.1%}")
metric_cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"BM {benchmark_metrics['sharpe']:.2f}")
metric_cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}", f"BM {benchmark_metrics['calmar']:.2f}")
metric_cols[5].metric("월 승률", f"{strategy_metrics['win_m']:.1%}")

state_counts = state_s.value_counts().reindex(STATE_ORDER).fillna(0).astype(int)
state_cols = st.columns(3)
for col, state in zip(state_cols, STATE_ORDER):
    count = state_counts.loc[state]
    col.metric(state, f"{count}일", f"{count / len(state_s):.1%}")

tab_chart, tab_signals, tab_trades, tab_monthly = st.tabs(
    ["성과", "신호", "거래", "월별 수익률"]
)

with tab_chart:
    st.subheader("누적 수익률")
    st.plotly_chart(make_nav_chart(nav_s, benchmark), use_container_width=True)

    st.subheader("낙폭")
    st.plotly_chart(
        make_dd_chart(strategy_metrics["dd"], benchmark_metrics["dd"]),
        use_container_width=True,
    )

with tab_signals:
    st.subheader("KODEX 200 추세와 상태")
    st.plotly_chart(
        make_state_chart(
            kodex_200["close"].reindex(common_idx).ffill(),
            kodex_ma_full.reindex(common_idx).ffill(),
            state_s,
        ),
        use_container_width=True,
    )

    st.subheader("TNX 금리 신호")
    tnx_chart = pd.DataFrame(
        {
            "TNX": tnx_aligned_full.reindex(common_idx).ffill(),
            f"TNX MA{tnx_ma_window}": tnx_ma_full.reindex(common_idx).ffill(),
        }
    ).dropna()
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
        st.download_button(
            "Trade Log CSV",
            trade_log.to_csv(index=False).encode("utf-8-sig"),
            "kodex_bull_bear_trades.csv",
            "text/csv",
        )

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_benchmark = benchmark.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame(
        {
            "전략": monthly_strategy,
            "KODEX 200": monthly_benchmark,
        }
    ).dropna()

    pivot_source = monthly_strategy.to_frame("수익률")
    pivot_source["연도"] = pivot_source.index.year
    pivot_source["월"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="연도", columns="월", values="수익률")
    pivot.columns = [f"{month}월" for month in pivot.columns]
    pivot["연간"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)

    st.subheader("월별 전략 vs KODEX 200")
    st.line_chart(monthly, height=260)

    st.download_button(
        "Monthly Returns CSV",
        monthly.reset_index().rename(columns={"index": "날짜"}).to_csv(index=False).encode("utf-8-sig"),
        "kodex_bull_bear_monthly.csv",
        "text/csv",
    )
