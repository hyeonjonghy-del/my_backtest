"""SOXX / SOXL trend and volatility-target backtest v5."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from kiwoom_account import (
    KIWOOM_SOURCE,
    render_account_controls,
    render_account_summary,
)
from chart_utils import static_area_chart as mpl_static_area_chart
from chart_utils import static_line_chart as mpl_static_line_chart
from chart_utils import position_action_label
from chart_utils import static_yearly_returns_chart

TRADING_DAYS = 252
SOXX = "SOXX"
SOXL = "SOXL"
STATIC_CHART_CONFIG = {
    "staticPlot": True,
    "displayModeBar": False,
    "responsive": True,
}
COLORS = {
    "strategy": "#0F766E",
    "soxx": "#2563EB",
    "soxl": "#DC2626",
    "benchmark": "#7C3AED",
    "cash": "#64748B",
    "ma_fast": "#F59E0B",
    "ma_slow": "#111827",
    "dd": "#B91C1C",
}

st.set_page_config(page_title="SOXX/SOXL Vol Target Backtest V5", page_icon="US", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("SOXX / SOXL Volatility Target Backtest V5")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True, key="run_backtest_top")
st.caption(
    "Default: Strong Bull uses SOXL tactically, Weak Bull shifts toward SOXX, "
    "and deep-drawdown turnarounds stay active until short-term momentum breaks"
)


def static_line_chart(
    data: pd.DataFrame,
    title: str,
    yaxis_title: str = "",
    percent_axis: bool = False,
    height: int = 340,
    mdd_info: dict[str, object] | None = None,
) -> go.Figure:
    fig = go.Figure()
    palette = [
        COLORS["strategy"],
        COLORS["soxx"],
        COLORS["soxl"],
        COLORS["benchmark"],
        COLORS["ma_fast"],
        COLORS["ma_slow"],
        COLORS["cash"],
    ]
    for i, column in enumerate(data.columns):
        fig.add_trace(
            go.Scatter(
                x=data.index,
                y=data[column],
                mode="lines",
                name=str(column),
                line=dict(color=palette[i % len(palette)], width=2.4 if i == 0 else 1.8),
            )
        )
    fig.update_layout(
        title=dict(text=title, x=0.01, xanchor="left"),
        height=height,
        margin=dict(l=10, r=10, t=46, b=20),
        hovermode=False,
        legend=dict(orientation="h", y=1.05, x=1, xanchor="right", font=dict(size=12)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(showgrid=False, rangeslider=dict(visible=False), fixedrange=True),
        yaxis=dict(title=yaxis_title, showgrid=True, gridcolor="#E5E7EB", fixedrange=True),
    )
    if percent_axis:
        fig.update_yaxes(ticksuffix="%")
    if mdd_info is not None:
        mdd_date = mdd_info["date"]
        peak_date = mdd_info["peak_date"]
        mdd_value = mdd_info["value"]
        peak_value = mdd_info["peak_value"]
        trough_value = mdd_info["trough_value"]
        fig.add_trace(
            go.Scatter(
                x=[peak_date, mdd_date],
                y=[peak_value, trough_value],
                mode="markers+lines",
                name=f"MDD {mdd_value:.1%}",
                line=dict(color=COLORS["dd"], width=2, dash="dot"),
                marker=dict(color=COLORS["dd"], size=8),
                showlegend=True,
            )
        )
        fig.add_annotation(
            x=mdd_date,
            y=trough_value,
            text=f"MDD {mdd_value:.1%}",
            showarrow=True,
            arrowhead=2,
            arrowcolor=COLORS["dd"],
            ax=32,
            ay=42,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor=COLORS["dd"],
            font=dict(color=COLORS["dd"], size=12),
        )
    return fig


def static_area_chart(data: pd.DataFrame, title: str, height: int = 300) -> go.Figure:
    fig = go.Figure()
    area_colors = {
        "SOXX": "rgba(37, 99, 235, 0.72)",
        "SOXL": "rgba(220, 38, 38, 0.72)",
        "Cash": "rgba(100, 116, 139, 0.55)",
    }
    for column in data.columns:
        fig.add_trace(
            go.Scatter(
                x=data.index,
                y=data[column],
                mode="lines",
                name=str(column),
                stackgroup="one",
                line=dict(width=0.8, color=area_colors.get(column, "rgba(15, 118, 110, 0.65)")),
                fillcolor=area_colors.get(column, "rgba(15, 118, 110, 0.65)"),
            )
        )
    fig.update_layout(
        title=dict(text=title, x=0.01, xanchor="left"),
        height=height,
        margin=dict(l=10, r=10, t=46, b=20),
        hovermode=False,
        legend=dict(orientation="h", y=1.05, x=1, xanchor="right", font=dict(size=12)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(showgrid=False, rangeslider=dict(visible=False), fixedrange=True),
        yaxis=dict(showgrid=True, gridcolor="#E5E7EB", tickformat=".0%", fixedrange=True, range=[0, 1]),
    )
    return fig


static_line_chart = mpl_static_line_chart
static_area_chart = mpl_static_area_chart


def normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


@st.cache_data(show_spinner=False, ttl=3600)
def load_yahoo_chart(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    period1 = int(datetime.combine(start_dt.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine((end_dt + timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)

    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    df = pd.DataFrame(
        {
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "adjclose": adjclose,
            "volume": quote["volume"],
        },
        index=index,
    )
    return normalize_index(df).dropna(subset=["adjclose"])


def calc_metrics(daily_ret: pd.Series) -> dict[str, object]:
    daily_ret = daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nav = (1 + daily_ret).cumprod()
    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    mdd_date = dd.idxmin()
    peak_nav = nav.cummax()
    peak_value = peak_nav.loc[mdd_date]
    peak_date = nav.loc[:mdd_date].idxmax()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS) if daily_ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return {
        "nav": nav,
        "dd": dd,
        "mdd_date": mdd_date,
        "mdd_peak_date": peak_date,
        "mdd_peak_value": peak_value,
        "mdd_trough_value": nav.loc[mdd_date],
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "win_m": win_m,
    }


def metric_row(name: str, daily_ret: pd.Series, soxx_w: pd.Series | None = None, soxl_w: pd.Series | None = None) -> dict[str, object]:
    metrics = calc_metrics(daily_ret)
    return {
        "Strategy": name,
        "Total": metrics["total"],
        "CAGR": metrics["cagr"],
        "MDD": metrics["mdd"],
        "Sharpe": metrics["sharpe"],
        "Calmar": metrics["calmar"],
        "Monthly Win": metrics["win_m"],
        "Avg SOXX": np.nan if soxx_w is None else soxx_w.mean(),
        "Avg SOXL": np.nan if soxl_w is None else soxl_w.mean(),
        "Max SOXL": np.nan if soxl_w is None else soxl_w.max(),
    }


def rebalance_weights(weights: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "Daily":
        return weights

    out = weights.copy() * 0.0
    current = pd.Series({"SOXX": 0.0, "SOXL": 0.0})
    last_key = None
    for date, row in weights.iterrows():
        key = date.isocalendar()[:2] if frequency == "Weekly" else (date.year, date.month)
        if key != last_key:
            current = row
            last_key = key
        out.loc[date] = current
    return out


def build_trend_signal(price: pd.Series, fast_ma: pd.Series, slow_ma: pd.Series, rule: str) -> pd.Series:
    if rule == "MA Fast > MA Slow":
        return fast_ma > slow_ma
    if rule == "Close > MA Slow":
        return price > slow_ma
    return (price > slow_ma) & (fast_ma > slow_ma)


def build_regime_signal(
    trend_signal: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    vol: pd.Series,
    strong_spread: float,
    weak_vol_cutoff: float,
    lag_for_execution: bool = True,
) -> pd.Series:
    if lag_for_execution:
        bull = trend_signal.shift(1).fillna(False)
        ma_spread = (fast_ma / slow_ma - 1).shift(1).replace([np.inf, -np.inf], np.nan)
        signal_vol = vol.shift(1)
    else:
        bull = trend_signal.fillna(False)
        ma_spread = (fast_ma / slow_ma - 1).replace([np.inf, -np.inf], np.nan)
        signal_vol = vol
    strong = bull & (ma_spread >= strong_spread) & (signal_vol <= weak_vol_cutoff)
    weak = bull & ~strong
    regime = pd.Series("Bear", index=trend_signal.index, dtype="object")
    regime.loc[weak] = "Weak Bull"
    regime.loc[strong] = "Strong Bull"
    return regime


def build_turnaround_signal(
    price: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    drawdown_trigger: float,
    exit_fast_window: int,
    exit_slow_window: int,
    exit_confirm_days: int,
    lag_for_execution: bool = True,
) -> pd.Series:
    price_dd = price / price.cummax() - 1
    golden_cross = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
    exit_fast_ma = price.rolling(exit_fast_window).mean()
    exit_slow_ma = price.rolling(exit_slow_window).mean()
    exit_signal = exit_fast_ma < exit_slow_ma
    active = pd.Series(False, index=price.index)
    armed = False
    in_turnaround = False
    exit_count = 0

    for date in price.index:
        if pd.notna(price_dd.loc[date]) and price_dd.loc[date] <= -drawdown_trigger:
            armed = True

        if bool(golden_cross.loc[date]) and armed:
            in_turnaround = True
            armed = False
            exit_count = 0

        if in_turnaround:
            active.loc[date] = True
            if bool(exit_signal.loc[date]):
                exit_count += 1
            else:
                exit_count = 0

            if exit_count >= exit_confirm_days:
                active.loc[date] = False
                in_turnaround = False
                exit_count = 0

    if lag_for_execution:
        return active.shift(1).fillna(False)
    return active.fillna(False)


def build_strategy_weights(
    price: pd.Series,
    trend_signal: pd.Series,
    regime: pd.Series,
    turnaround_signal: pd.Series,
    vol: pd.Series,
    target_vol: float,
    soxl_cap: float,
    max_risk_exposure: float,
    strong_soxx_risk_share: float,
    weak_risk_multiplier: float,
    weak_soxx_risk_share: float,
    weak_soxl_cap: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
    rebalance: str,
) -> pd.DataFrame:
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (target_vol / vol_lag).clip(0, max_risk_exposure).fillna(0.0)

    weights = pd.DataFrame(0.0, index=price.index, columns=["SOXX", "SOXL"])

    strong_risk = desired_risk
    strong_soxx = (strong_risk * strong_soxx_risk_share).clip(0, 1)
    strong_soxl = ((strong_risk - strong_soxx) / 3).clip(0, soxl_cap)
    strong_risk_used = strong_soxx + strong_soxl * 3
    strong_soxx = (strong_soxx + (strong_risk - strong_risk_used).clip(lower=0)).clip(0, 1 - strong_soxl)

    weak_risk = (desired_risk * weak_risk_multiplier).clip(0, max_risk_exposure)
    weak_soxx = (weak_risk * weak_soxx_risk_share).clip(0, 1)
    weak_soxl = ((weak_risk - weak_soxx) / 3).clip(0, weak_soxl_cap)
    weak_risk_used = weak_soxx + weak_soxl * 3
    weak_soxx = (weak_soxx + (weak_risk - weak_risk_used).clip(lower=0)).clip(0, 1 - weak_soxl)

    weights["SOXL"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_soxl, weak_soxl],
        default=0.0,
    )
    weights["SOXX"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_soxx, weak_soxx],
        default=bear_soxx,
    )
    weights.loc[turnaround_signal, "SOXX"] = 1 - turnaround_soxl_weight
    weights.loc[turnaround_signal, "SOXL"] = turnaround_soxl_weight
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)


def calc_target_weight(
    regime: str,
    is_turnaround: bool,
    current_vol: float,
    target_vol: float,
    soxl_cap: float,
    max_risk_exposure: float,
    strong_soxx_risk_share: float,
    weak_risk_multiplier: float,
    weak_soxx_risk_share: float,
    weak_soxl_cap: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
) -> pd.Series:
    if is_turnaround:
        return pd.Series({"SOXX": 1 - turnaround_soxl_weight, "SOXL": turnaround_soxl_weight})

    if regime == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({"SOXX": bear_soxx, "SOXL": 0.0})

    desired_risk = min(target_vol / current_vol, max_risk_exposure)
    if regime == "Weak Bull":
        desired_risk = min(desired_risk * weak_risk_multiplier, max_risk_exposure)
        soxx_risk_share = weak_soxx_risk_share
        effective_soxl_cap = weak_soxl_cap
    else:
        soxx_risk_share = strong_soxx_risk_share
        effective_soxl_cap = soxl_cap

    soxx_w = min(desired_risk * soxx_risk_share, 1.0)
    soxl_w = min(max((desired_risk - soxx_w) / 3, 0.0), effective_soxl_cap)
    risk_used = soxx_w + soxl_w * 3
    soxx_w = min(soxx_w + max(desired_risk - risk_used, 0.0), 1 - soxl_w)

    target = pd.Series({"SOXX": soxx_w, "SOXL": soxl_w}).clip(0, 1)
    if target.sum() > 1:
        target = target / target.sum()
    return target


def backtest_open_execution_close_valuation(
    target_weights: pd.DataFrame,
    open_prices: pd.DataFrame,
    close_prices: pd.DataFrame,
    cost_rate: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Trade at each open, carry holdings/cash, and mark portfolio NAV at each close."""
    units = pd.Series(0.0, index=target_weights.columns)
    cash = 1.0
    previous_close_nav = 1.0
    daily_ret = pd.Series(0.0, index=target_weights.index, name="Strategy")
    turnover = pd.Series(0.0, index=target_weights.index, name="Turnover")
    close_nav = pd.Series(0.0, index=target_weights.index, name="Close NAV")

    for number, date in enumerate(target_weights.index):
        open_px = open_prices.loc[date]
        nav_at_open = float(cash + (units * open_px).sum())
        current_weights = units * open_px / nav_at_open if nav_at_open > 0 else units * 0
        desired = target_weights.loc[date].clip(0, 1)
        traded_fraction = float((desired - current_weights).abs().sum())
        trading_cost = nav_at_open * traded_fraction * cost_rate
        investable = max(nav_at_open - trading_cost, 0.0)
        target_values = investable * desired
        units = (target_values / open_px).replace([np.inf, -np.inf], 0.0).fillna(0.0)
        cash = max(investable - float(target_values.sum()), 0.0)
        nav_at_close = float(cash + (units * close_prices.loc[date]).sum())
        close_nav.loc[date] = nav_at_close
        daily_ret.loc[date] = nav_at_close / previous_close_nav - 1 if number > 0 else nav_at_close - 1
        turnover.loc[date] = traded_fraction
        previous_close_nav = nav_at_close
    return daily_ret, turnover, close_nav


def build_execution_plan(
    target_weights: pd.Series,
    prices: pd.Series,
    account_value: float,
    current_shares: pd.Series,
    current_cash: float,
) -> tuple[pd.DataFrame, float]:
    current_values = current_shares * prices
    effective_value = account_value if account_value > 0 else current_values.sum() + current_cash
    rows = []
    for symbol in ["SOXX", "SOXL"]:
        target_value = effective_value * target_weights[symbol]
        target_shares = np.floor(target_value / prices[symbol]) if prices[symbol] > 0 else 0
        order_shares = target_shares - current_shares[symbol]
        rows.append(
            {
                "Symbol": symbol,
                "Latest Price": prices[symbol],
                "Target Weight": target_weights[symbol],
                "Target Value": target_value,
                "Target Shares": target_shares,
                "Current Shares": current_shares[symbol],
                "Order": "Buy" if order_shares > 0 else "Sell" if order_shares < 0 else "Hold",
                "Order Shares": order_shares,
                "Estimated Order Value": abs(order_shares) * prices[symbol],
            }
        )
    target_cash = effective_value * max(0.0, 1 - target_weights.sum())
    return pd.DataFrame(rows), target_cash


with st.sidebar:
    st.header("Settings")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start", datetime(2016, 5, 12))
    with col2:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Trend Filter")
    trend_rule = st.selectbox("Rule", ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow + MA Fast > MA Slow"], index=0)
    fast_window = st.slider("Fast MA", 20, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Target")
    vol_window = st.slider("Volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 80, 45, 5) / 100
    soxl_cap = st.slider("SOXL max weight (%)", 0, 80, 50, 5) / 100
    max_risk_exposure = st.slider("Max risk exposure", 0.5, 2.0, 1.5, 0.1)

    st.subheader("Regime Blend")
    strong_spread = st.slider("Strong Bull MA spread (%)", 0, 20, 5, 1) / 100
    weak_vol_cutoff = st.slider("Weak Bull if volatility above (%)", 20, 100, 55, 5) / 100
    strong_soxx_risk_share = st.slider("Strong Bull SOXX risk share (%)", 0, 60, 20, 5) / 100
    weak_risk_multiplier = st.slider("Weak Bull risk multiplier (%)", 30, 100, 75, 5) / 100
    weak_soxx_risk_share = st.slider("Weak Bull SOXX risk share (%)", 40, 100, 80, 5) / 100
    weak_soxl_cap = st.slider("Weak Bull SOXL max weight (%)", 0, 40, 15, 5) / 100

    st.subheader("Turnaround Full-Bet")
    turnaround_dd_trigger = st.slider("Turnaround drawdown trigger (%)", 10, 50, 20, 5) / 100
    turnaround_soxl_weight = st.slider("Turnaround SOXL weight (%)", 0, 80, 50, 5) / 100
    turnaround_exit_fast = st.slider("Turnaround exit fast MA", 3, 20, 10, 1)
    turnaround_exit_slow = st.slider("Turnaround exit slow MA", 10, 60, 60, 5)
    turnaround_exit_confirm = st.slider("Exit confirmation days", 1, 5, 2, 1)

    st.subheader("Bear / Trading")
    bear_soxx = st.slider("Bear-regime SOXX weight (%)", 0, 100, 20, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=0, horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.25, step=0.01) / 100

    st.subheader("Execution")
    account_state = render_account_controls(
        (SOXX, SOXL),
        "soxx_soxl",
        preferred_profile="soxx",
    )
    execution_source = account_state["source"]
    kiwoom_snapshot = account_state["snapshot"]
    current_soxx_shares = float(account_state["shares"][SOXX])
    current_soxl_shares = float(account_state["shares"][SOXL])
    current_cash = float(account_state["cash"])
    account_value = float(account_state["account_value"])

with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| Item | Value |
|---|---|
| Bull regime | SOXX MA{fast_window} > MA{slow_window} |
| Target volatility | {target_vol:.0%} |
| SOXL cap | {soxl_cap:.0%} |
| Max risk exposure | {max_risk_exposure:.1f}x SOXX-equivalent risk |
| Strong Bull | Bull trend + MA spread >= {strong_spread:.0%} + volatility <= {weak_vol_cutoff:.0%} |
| Strong Bull allocation | SOXX gets {strong_soxx_risk_share:.0%} of risk budget, SOXL gets the rest |
| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, SOXX gets {weak_soxx_risk_share:.0%}, SOXL cap {weak_soxl_cap:.0%} |
| Turnaround Bull | SOXX drawdown <= -{turnaround_dd_trigger:.0%}, then golden cross occurs |
| Turnaround allocation | SOXX {1 - turnaround_soxl_weight:.0%} + SOXL {turnaround_soxl_weight:.0%} |
| Turnaround exit | MA{turnaround_exit_fast} < MA{turnaround_exit_slow} for {turnaround_exit_confirm} day(s), then return to v3 logic |
| Bear regime | Cash {1 - bear_soxx:.0%} + SOXX {bear_soxx:.0%} |
"""
    )

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

if execution_source == KIWOOM_SOURCE and not kiwoom_snapshot:
    st.error("Load the selected Kiwoom account before running the backtest.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

progress = st.progress(0, text="Loading SOXX/SOXL data...")
try:
    warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=max(slow_window, vol_window) * 3)
    end_dt = datetime.combine(end_date, datetime.min.time())
    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)
    soxl = load_yahoo_chart(SOXL, warmup_start, end_dt)
except Exception as exc:
    st.error(f"Could not load Yahoo Finance data: {exc}")
    st.stop()

common_idx = soxx.index.intersection(soxl.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 200:
    st.error("Not enough data for the selected backtest period.")
    st.stop()

full_idx = common_idx.union(soxx.index[soxx.index < common_idx[0]])
soxx = soxx.reindex(full_idx).sort_index()
soxl = soxl.reindex(full_idx).sort_index()
price = soxx["adjclose"].ffill()
soxx_adj_factor = (soxx["adjclose"] / soxx["close"]).replace([np.inf, -np.inf], np.nan).ffill()
soxl_adj_factor = (soxl["adjclose"] / soxl["close"]).replace([np.inf, -np.inf], np.nan).ffill()
soxx_adjopen = (soxx["open"] * soxx_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()
soxl_adjopen = (soxl["open"] * soxl_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()
close_ret_soxx_full = soxx["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_soxx_full = soxx["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_soxl_full = soxl["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)

fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
vol = close_ret_soxx_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
trend_signal = build_trend_signal(price, fast_ma, slow_ma, trend_rule)
turnaround_signal = build_turnaround_signal(
    price,
    fast_ma,
    slow_ma,
    turnaround_dd_trigger,
    turnaround_exit_fast,
    turnaround_exit_slow,
    turnaround_exit_confirm,
)
close_turnaround_signal = build_turnaround_signal(
    price,
    fast_ma,
    slow_ma,
    turnaround_dd_trigger,
    turnaround_exit_fast,
    turnaround_exit_slow,
    turnaround_exit_confirm,
    lag_for_execution=False,
)
regime_signal = build_regime_signal(
    trend_signal,
    fast_ma,
    slow_ma,
    vol,
    strong_spread,
    weak_vol_cutoff,
)
close_regime_signal = build_regime_signal(
    trend_signal,
    fast_ma,
    slow_ma,
    vol,
    strong_spread,
    weak_vol_cutoff,
    lag_for_execution=False,
)

weights_full = build_strategy_weights(
    price,
    trend_signal,
    regime_signal,
    turnaround_signal,
    vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    strong_soxx_risk_share,
    weak_risk_multiplier,
    weak_soxx_risk_share,
    weak_soxl_cap,
    turnaround_soxl_weight,
    bear_soxx,
    rebalance,
)

weights = weights_full.reindex(common_idx).fillna(0.0)
turnaround = turnaround_signal.reindex(common_idx).fillna(False)
display_regime_signal = regime_signal.reindex(common_idx).where(~turnaround, "Turnaround Bull")
close_turnaround = close_turnaround_signal.reindex(common_idx).fillna(False)
close_display_regime_signal = close_regime_signal.reindex(common_idx).where(~close_turnaround, "Turnaround Bull")
close_target_weights = pd.DataFrame(
    [
        calc_target_weight(
            str(close_regime_signal.ffill().loc[date]),
            bool(close_turnaround_signal.fillna(False).loc[date]),
            vol.ffill().loc[date],
            target_vol,
            soxl_cap,
            max_risk_exposure,
            strong_soxx_risk_share,
            weak_risk_multiplier,
            weak_soxx_risk_share,
            weak_soxl_cap,
            turnaround_soxl_weight,
            bear_soxx,
        )
        for date in common_idx
    ],
    index=common_idx,
)
ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)
ret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)
open_prices = pd.DataFrame({"SOXX": soxx_adjopen, "SOXL": soxl_adjopen}).reindex(common_idx).ffill()
close_prices = pd.DataFrame(
    {"SOXX": soxx["adjclose"], "SOXL": soxl["adjclose"]}
).reindex(common_idx).ffill()
strategy_ret, executed_turnover, close_nav = backtest_open_execution_close_valuation(
    weights, open_prices, close_prices, cost_rate
)

bench_soxx = ret_soxx
bench_soxl = ret_soxl
fixed_20 = 0.8 * ret_soxx + 0.2 * ret_soxl
fixed_30 = 0.7 * ret_soxx + 0.3 * ret_soxl

strategy_metrics = calc_metrics(strategy_ret)
summary = pd.DataFrame(
    [
        metric_row("Strategy", strategy_ret, weights["SOXX"], weights["SOXL"]),
        metric_row("SOXX 100%", bench_soxx),
        metric_row("SOXL 100%", bench_soxl),
        metric_row("SOXX 80% + SOXL 20%", fixed_20),
        metric_row("SOXX 70% + SOXL 30%", fixed_30),
    ]
)

progress.progress(100, text="Done")
progress.empty()

latest = weights.iloc[-1]
latest_date = weights.index[-1].date()
latest_turnaround = bool(close_turnaround_signal.reindex(weights.index).fillna(False).iloc[-1])
latest_regime = str(close_display_regime_signal.ffill().iloc[-1])
latest_vol = vol.reindex(weights.index).ffill().iloc[-1]
next_target = calc_target_weight(
    str(close_regime_signal.reindex(weights.index).ffill().iloc[-1]),
    latest_turnaround,
    latest_vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    strong_soxx_risk_share,
    weak_risk_multiplier,
    weak_soxx_risk_share,
    weak_soxl_cap,
    turnaround_soxl_weight,
    bear_soxx,
)
latest_prices = pd.Series(
    {
        "SOXX": soxx["adjclose"].reindex(weights.index).ffill().iloc[-1],
        "SOXL": soxl["adjclose"].reindex(weights.index).ffill().iloc[-1],
    }
)
current_shares = pd.Series({"SOXX": current_soxx_shares, "SOXL": current_soxl_shares})
if execution_source == KIWOOM_SOURCE:
    account_value = float(current_cash + (current_shares * latest_prices).sum())
execution_plan, target_cash = build_execution_plan(
    next_target,
    latest_prices,
    account_value,
    current_shares,
    current_cash,
)
action_label = position_action_label(execution_plan["Order Shares"].abs().sum(), tolerance=0.5)

st.success(
    f"{action_label} | Today's target for next open from close signal ({latest_date}): {latest_regime} | "
    f"SOXX {next_target['SOXX']:.1%}, SOXL {next_target['SOXL']:.1%}, Cash {1 - next_target.sum():.1%} | "
    f"SOXX {vol_window}D volatility {latest_vol:.1%}"
)

cols = st.columns(6)
cols[0].metric("Total", f"{strategy_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

tab_perf, tab_execute, tab_signal, tab_table, tab_monthly = st.tabs(
    ["Performance", "Execution", "Signal / Weights", "Comparison", "Monthly"]
)

with tab_perf:
    nav_df = pd.DataFrame(
        {
            "Strategy": strategy_metrics["nav"],
            "SOXX": calc_metrics(bench_soxx)["nav"],
            "SOXL": calc_metrics(bench_soxl)["nav"],
            "80/20": calc_metrics(fixed_20)["nav"],
        }
    )
    st.pyplot(
        static_line_chart(
            nav_df,
            "Cumulative NAV with Strategy MDD",
            yaxis_title="NAV",
            height=380,
            mdd_info={
                "date": strategy_metrics["mdd_date"],
                "peak_date": strategy_metrics["mdd_peak_date"],
                "value": strategy_metrics["mdd"],
                "peak_value": strategy_metrics["mdd_peak_value"],
                "trough_value": strategy_metrics["mdd_trough_value"],
            },
        ),
        clear_figure=True,
    )

    dd_df = pd.DataFrame(
        {
            "Strategy DD": strategy_metrics["dd"],
            "SOXX DD": calc_metrics(bench_soxx)["dd"],
            "SOXL DD": calc_metrics(bench_soxl)["dd"],
        }
    ) * 100
    st.pyplot(
        static_line_chart(
            dd_df,
            f"Drawdown | Strategy MDD {strategy_metrics['mdd']:.1%}",
            yaxis_title="Drawdown",
            percent_axis=True,
            height=280,
        ),
        clear_figure=True,
    )
    st.pyplot(
        static_yearly_returns_chart(
            {
                "Strategy": strategy_metrics["nav"],
                "SOXX": calc_metrics(bench_soxx)["nav"],
                "SOXL": calc_metrics(bench_soxl)["nav"],
            },
            "Yearly Returns",
            height=330,
        ),
        clear_figure=True,
    )
    performance_weight_df = weights.copy()
    performance_weight_df["Cash"] = (1 - performance_weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(static_area_chart(performance_weight_df, "Portfolio Weights", height=300), clear_figure=True)

with tab_execute:
    render_account_summary(account_state, account_value)

    st.subheader("Next Trade Plan")
    st.caption(
        "Signal uses the latest close. Backtest returns assume rebalancing at the next regular-session open. "
        "The table uses the latest adjusted close only as a sizing estimate because the next open is not known yet."
    )
    exec_shown = execution_plan.copy()
    for col in ["Latest Price", "Target Value", "Estimated Order Value"]:
        exec_shown[col] = exec_shown[col].map(lambda x: f"${x:,.2f}")
    exec_shown["Target Weight"] = exec_shown["Target Weight"].map(lambda x: f"{x:.1%}")
    for col in ["Target Shares", "Current Shares", "Order Shares"]:
        exec_shown[col] = exec_shown[col].map(lambda x: f"{x:,.0f}")
    st.dataframe(exec_shown, use_container_width=True, hide_index=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Target Cash", f"${target_cash:,.2f}")
    c2.metric("Signal Date", str(latest_date))
    c3.metric("Target Invested", f"{next_target.sum():.1%}")
    st.info(
        "Practical rule: after the signal date closes, prepare these orders for the next regular-session open. "
        "Buy positive Order Shares, sell negative Order Shares, and re-run after fills if the opening price differs a lot."
    )

with tab_signal:
    signal_df = pd.DataFrame(
        {
            "SOXX": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
        }
    )
    st.pyplot(
        static_line_chart(signal_df, "SOXX Trend", yaxis_title="Price", height=320),
        clear_figure=True,
    )

    weight_df = weights.copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(
        static_area_chart(weight_df, "Portfolio Weights", height=300),
        clear_figure=True,
    )

    st.subheader("Recent Signals")
    recent = pd.DataFrame(
        {
            "SOXX": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
            f"Exit MA{turnaround_exit_fast}": price.rolling(turnaround_exit_fast).mean().reindex(common_idx),
            f"Exit MA{turnaround_exit_slow}": price.rolling(turnaround_exit_slow).mean().reindex(common_idx),
            "SOXX DD": price.reindex(common_idx) / price.reindex(common_idx).cummax() - 1,
            f"Vol{vol_window}": vol.reindex(common_idx),
            "Applied Regime": display_regime_signal,
            "Target Regime": close_display_regime_signal,
            "Applied Turnaround": turnaround,
            "Target Turnaround": close_turnaround,
            "Applied SOXX": weights["SOXX"],
            "Applied SOXL": weights["SOXL"],
            "Target SOXX": close_target_weights["SOXX"],
            "Target SOXL": close_target_weights["SOXL"],
            "Target Cash": (1 - close_target_weights.sum(axis=1)).clip(0, 1),
        }
    ).tail(30)
    st.dataframe(recent, use_container_width=True)

with tab_table:
    shown = summary.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg SOXL", "Max SOXL"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown[col] = shown[col].map(lambda x: f"{x:.2f}")
    st.dataframe(shown, use_container_width=True, hide_index=True)

with tab_monthly:
    monthly = strategy_metrics["nav"].resample("ME").last().pct_change().dropna()
    pivot_source = monthly.to_frame("Return")
    pivot_source["Year"] = pivot_source.index.year
    pivot_source["Month"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly).groupby(monthly.index.year).prod() - 1
    st.dataframe(pivot.applymap(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)

