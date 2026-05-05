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


def align_tnx_previous_day(
    tnx: pd.Series,
    target_index: pd.DatetimeIndex,
    tnx_ma_window: int,
) -> tuple[pd.Series, pd.Series]:
    if tnx.empty:
        return (
            pd.Series(np.nan, index=target_index, name="TNX_PREV"),
            pd.Series(np.nan, index=target_index, name="TNX_MA_PREV"),
        )

    tnx_daily_idx = pd.date_range(tnx.index.min(), target_index[-1], freq="D")
    tnx_daily = tnx.reindex(tnx_daily_idx).ffill()
    tnx_ma_daily = tnx.rolling(tnx_ma_window).mean().reindex(tnx_daily_idx).ffill()

    previous_calendar_day = target_index - pd.Timedelta(days=1)
    previous_tnx = tnx_daily.reindex(previous_calendar_day, method="ffill")
    previous_tnx_ma = tnx_ma_daily.reindex(previous_calendar_day, method="ffill")
    previous_tnx.index = target_index
    previous_tnx_ma.index = target_index
    return previous_tnx.rename("TNX_PREV"), previous_tnx_ma.rename("TNX_MA_PREV")


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
        paper_bgcolor="rgba(0,0,0,0)
    )
    return fig
