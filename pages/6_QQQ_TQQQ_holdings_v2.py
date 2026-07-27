"""QQQ / TQQQ holdings-based trend and volatility-target backtest v2."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from chart_utils import static_area_chart as mpl_static_area_chart
from chart_utils import static_line_chart as mpl_static_line_chart
from chart_utils import position_action_label
from chart_utils import static_yearly_returns_chart

TRADING_DAYS = 252
QQQ = "QQQ"
TQQQ = "TQQQ"
STATIC_CHART_CONFIG = {
    "staticPlot": True,
    "displayModeBar": False,
    "responsive": True,
}
COLORS = {
    "strategy": "#0F766E",
    "qqq": "#2563EB",
    "tqqq": "#DC2626",
    "benchmark": "#7C3AED",
    "cash": "#64748B",
    "ma_fast": "#F59E0B",
    "ma_slow": "#111827",
    "dd": "#B91C1C",
}

st.set_page_config(page_title="QQQ/TQQQ Holdings Backtest V2", page_icon="US", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("QQQ / TQQQ Holdings-Based Backtest V2")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True, key="run_backtest_top")
st.caption(
    "Default: Strong Bull uses TQQQ tactically, Weak Bull shifts toward QQQ, "
    "and deep-drawdown turnarounds stay active until short-term momentum breaks. "
    "V2 keeps share quantities unchanged until the target allocation changes."
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
        COLORS["qqq"],
        COLORS["tqqq"],
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
        "QQQ": "rgba(37, 99, 235, 0.72)",
        "TQQQ": "rgba(220, 38, 38, 0.72)",
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


def metric_row(name: str, daily_ret: pd.Series, qqq_w: pd.Series | None = None, tqqq_w: pd.Series | None = None) -> dict[str, object]:
    metrics = calc_metrics(daily_ret)
    return {
        "Strategy": name,
        "Total": metrics["total"],
        "CAGR": metrics["cagr"],
        "MDD": metrics["mdd"],
        "Sharpe": metrics["sharpe"],
        "Calmar": metrics["calmar"],
        "Monthly Win": metrics["win_m"],
        "Avg QQQ": np.nan if qqq_w is None else qqq_w.mean(),
        "Avg TQQQ": np.nan if tqqq_w is None else tqqq_w.mean(),
        "Max TQQQ": np.nan if tqqq_w is None else tqqq_w.max(),
    }


def rebalance_weights(weights: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "Daily":
        return weights

    out = weights.copy() * 0.0
    current = pd.Series({"QQQ": 0.0, "TQQQ": 0.0})
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
    tqqq_cap: float,
    max_risk_exposure: float,
    strong_qqq_risk_share: float,
    strong_cash_sweep: float,
    weak_risk_multiplier: float,
    weak_qqq_risk_share: float,
    weak_tqqq_cap: float,
    weak_cash_sweep: float,
    turnaround_tqqq_weight: float,
    bear_qqq: float,
    rebalance: str,
) -> pd.DataFrame:
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (target_vol / vol_lag).clip(0, max_risk_exposure).fillna(0.0)

    weights = pd.DataFrame(0.0, index=price.index, columns=["QQQ", "TQQQ"])

    strong_risk = desired_risk
    strong_qqq = (strong_risk * strong_qqq_risk_share).clip(0, 1)
    strong_tqqq = ((strong_risk - strong_qqq) / 3).clip(0, tqqq_cap)
    strong_risk_used = strong_qqq + strong_tqqq * 3
    strong_qqq = (strong_qqq + (strong_risk - strong_risk_used).clip(lower=0)).clip(0, 1 - strong_tqqq)

    weak_risk = (desired_risk * weak_risk_multiplier).clip(0, max_risk_exposure)
    weak_qqq = (weak_risk * weak_qqq_risk_share).clip(0, 1)
    weak_tqqq = ((weak_risk - weak_qqq) / 3).clip(0, weak_tqqq_cap)
    weak_risk_used = weak_qqq + weak_tqqq * 3
    weak_qqq = (weak_qqq + (weak_risk - weak_risk_used).clip(lower=0)).clip(0, 1 - weak_tqqq)

    weights["TQQQ"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_tqqq, weak_tqqq],
        default=0.0,
    )
    weights["QQQ"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_qqq, weak_qqq],
        default=bear_qqq,
    )
    residual_cash = (1 - weights.sum(axis=1)).clip(lower=0)
    weights.loc[regime == "Strong Bull", "QQQ"] += residual_cash.loc[regime == "Strong Bull"] * strong_cash_sweep
    weights.loc[regime == "Weak Bull", "QQQ"] += residual_cash.loc[regime == "Weak Bull"] * weak_cash_sweep
    weights.loc[turnaround_signal, "QQQ"] = 1 - turnaround_tqqq_weight
    weights.loc[turnaround_signal, "TQQQ"] = turnaround_tqqq_weight
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)


def calc_target_weight(
    regime: str,
    is_turnaround: bool,
    current_vol: float,
    target_vol: float,
    tqqq_cap: float,
    max_risk_exposure: float,
    strong_qqq_risk_share: float,
    strong_cash_sweep: float,
    weak_risk_multiplier: float,
    weak_qqq_risk_share: float,
    weak_tqqq_cap: float,
    weak_cash_sweep: float,
    turnaround_tqqq_weight: float,
    bear_qqq: float,
) -> pd.Series:
    if is_turnaround:
        return pd.Series({"QQQ": 1 - turnaround_tqqq_weight, "TQQQ": turnaround_tqqq_weight})

    if regime == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({"QQQ": bear_qqq, "TQQQ": 0.0})

    desired_risk = min(target_vol / current_vol, max_risk_exposure)
    if regime == "Weak Bull":
        desired_risk = min(desired_risk * weak_risk_multiplier, max_risk_exposure)
        qqq_risk_share = weak_qqq_risk_share
        effective_tqqq_cap = weak_tqqq_cap
        cash_sweep = weak_cash_sweep
    else:
        qqq_risk_share = strong_qqq_risk_share
        effective_tqqq_cap = tqqq_cap
        cash_sweep = strong_cash_sweep

    qqq_w = min(desired_risk * qqq_risk_share, 1.0)
    tqqq_w = min(max((desired_risk - qqq_w) / 3, 0.0), effective_tqqq_cap)
    risk_used = qqq_w + tqqq_w * 3
    qqq_w = min(qqq_w + max(desired_risk - risk_used, 0.0), 1 - tqqq_w)
    qqq_w = min(qqq_w + max(1 - qqq_w - tqqq_w, 0.0) * cash_sweep, 1 - tqqq_w)

    target = pd.Series({"QQQ": qqq_w, "TQQQ": tqqq_w}).clip(0, 1)
    if target.sum() > 1:
        target = target / target.sum()
    return target


def legacy_backtest(
    weights: pd.DataFrame,
    ret_qqq: pd.Series,
    ret_tqqq: pd.Series,
    cost_rate: float,
) -> pd.Series:
    """Original calculation: target weights are treated as maintained every day."""
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    daily_ret = weights["QQQ"] * ret_qqq + weights["TQQQ"] * ret_tqqq - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def holdings_backtest(
    target_weights: pd.DataFrame,
    open_prices: pd.DataFrame,
    cost_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    """Hold shares and cash unchanged until the target allocation changes."""
    shares = pd.Series(0.0, index=["QQQ", "TQQQ"])
    cash = 1.0
    prior_target: pd.Series | None = None
    daily_ret = pd.Series(0.0, index=target_weights.index)
    actual_weights = pd.DataFrame(0.0, index=target_weights.index, columns=["QQQ", "TQQQ"])
    turnover = pd.Series(0.0, index=target_weights.index)

    for index_number, date in enumerate(target_weights.index):
        prices = open_prices.loc[date]
        asset_values = shares * prices
        nav_before = float(cash + asset_values.sum())
        target = target_weights.loc[date].clip(0, 1)
        should_rebalance = prior_target is None or not np.allclose(
            target.values,
            prior_target.values,
            atol=1e-12,
            rtol=0,
        )

        if should_rebalance and nav_before > 0:
            current_weights = asset_values / nav_before
            target_turnover = float((target - current_weights).abs().sum())
            trading_cost = nav_before * target_turnover * cost_rate
            nav_after_trade = max(nav_before - trading_cost, 0.0)
            shares = (nav_after_trade * target / prices).replace([np.inf, -np.inf], 0.0).fillna(0.0)
            cash = nav_after_trade * max(0.0, 1 - float(target.sum()))
            turnover.loc[date] = target_turnover
            prior_target = target.copy()
        else:
            nav_after_trade = nav_before

        marked_values = shares * prices
        marked_nav = float(cash + marked_values.sum())
        if marked_nav > 0:
            actual_weights.loc[date] = marked_values / marked_nav

        if index_number + 1 < len(target_weights.index):
            next_date = target_weights.index[index_number + 1]
            next_value = float(cash + (shares * open_prices.loc[next_date]).sum())
            daily_ret.loc[date] = next_value / nav_before - 1 if nav_before > 0 else 0.0
        else:
            daily_ret.loc[date] = nav_after_trade / nav_before - 1 if nav_before > 0 else 0.0

    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0), actual_weights, turnover


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
    for symbol in ["QQQ", "TQQQ"]:
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
    target_vol = st.slider("Target volatility (%)", 10, 80, 35, 5) / 100
    tqqq_cap = st.slider("TQQQ max weight (%)", 0, 80, 45, 5) / 100
    max_risk_exposure = st.slider("Max risk exposure", 0.5, 2.0, 1.8, 0.1)

    st.subheader("Regime Blend")
    strong_spread = st.slider("Strong Bull MA spread (%)", 0, 20, 5, 1) / 100
    weak_vol_cutoff = st.slider("Weak Bull if volatility above (%)", 20, 100, 55, 5) / 100
    strong_qqq_risk_share = st.slider("Strong Bull QQQ risk share (%)", 0, 60, 60, 5) / 100
    strong_cash_sweep = st.slider("Strong Bull cash sweep to QQQ (%)", 0, 100, 50, 5) / 100
    weak_risk_multiplier = st.slider("Weak Bull risk multiplier (%)", 30, 100, 75, 5) / 100
    weak_qqq_risk_share = st.slider("Weak Bull QQQ risk share (%)", 40, 100, 60, 5) / 100
    weak_tqqq_cap = st.slider("Weak Bull TQQQ max weight (%)", 0, 40, 15, 5) / 100
    weak_cash_sweep = st.slider("Weak Bull cash sweep to QQQ (%)", 0, 100, 20, 5) / 100

    st.subheader("Turnaround Full-Bet")
    turnaround_dd_trigger = st.slider("Turnaround drawdown trigger (%)", 10, 50, 10, 5) / 100
    turnaround_tqqq_weight = st.slider("Turnaround TQQQ weight (%)", 0, 80, 50, 5) / 100
    turnaround_exit_fast = st.slider("Turnaround exit fast MA", 3, 20, 10, 1)
    turnaround_exit_slow = st.slider("Turnaround exit slow MA", 10, 60, 60, 5)
    turnaround_exit_confirm = st.slider("Exit confirmation days", 1, 5, 2, 1)

    st.subheader("Bear / Trading")
    bear_qqq = st.slider("Bear-regime QQQ weight (%)", 0, 100, 30, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=0, horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.25, step=0.01) / 100

    st.subheader("Execution")
    account_value = st.number_input("Account value ($)", min_value=0.0, value=10000.0, step=1000.0)
    current_qqq_shares = st.number_input("Current QQQ shares", min_value=0.0, value=0.0, step=1.0)
    current_tqqq_shares = st.number_input("Current TQQQ shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash ($)", min_value=0.0, value=10000.0, step=1000.0)

with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| Item | Value |
|---|---|
| Bull regime | QQQ MA{fast_window} > MA{slow_window} |
| Target volatility | {target_vol:.0%} |
| TQQQ cap | {tqqq_cap:.0%} |
| Max risk exposure | {max_risk_exposure:.1f}x QQQ-equivalent risk |
| Strong Bull | Bull trend + MA spread >= {strong_spread:.0%} + volatility <= {weak_vol_cutoff:.0%} |
| Strong Bull allocation | QQQ gets {strong_qqq_risk_share:.0%} of risk budget, then {strong_cash_sweep:.0%} of leftover cash is added to QQQ |
| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, QQQ gets {weak_qqq_risk_share:.0%}, TQQQ cap {weak_tqqq_cap:.0%}, cash sweep {weak_cash_sweep:.0%} |
| Turnaround Bull | QQQ drawdown <= -{turnaround_dd_trigger:.0%}, then golden cross occurs |
| Turnaround allocation | QQQ {1 - turnaround_tqqq_weight:.0%} + TQQQ {turnaround_tqqq_weight:.0%} |
| Turnaround exit | MA{turnaround_exit_fast} < MA{turnaround_exit_slow} for {turnaround_exit_confirm} day(s), then return to regime logic |
| Bear regime | Cash {1 - bear_qqq:.0%} + QQQ {bear_qqq:.0%} |
"""
    )

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

progress = st.progress(0, text="Loading QQQ/TQQQ data...")
try:
    warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=max(slow_window, vol_window) * 3)
    end_dt = datetime.combine(end_date, datetime.min.time())
    qqq = load_yahoo_chart(QQQ, warmup_start, end_dt)
    tqqq = load_yahoo_chart(TQQQ, warmup_start, end_dt)
except Exception as exc:
    st.error(f"Could not load Yahoo Finance data: {exc}")
    st.stop()

common_idx = qqq.index.intersection(tqqq.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 200:
    st.error("Not enough data for the selected backtest period.")
    st.stop()

full_idx = common_idx.union(qqq.index[qqq.index < common_idx[0]])
qqq = qqq.reindex(full_idx).sort_index()
tqqq = tqqq.reindex(full_idx).sort_index()
price = qqq["adjclose"].ffill()
qqq_adj_factor = (qqq["adjclose"] / qqq["close"]).replace([np.inf, -np.inf], np.nan).ffill()
tqqq_adj_factor = (tqqq["adjclose"] / tqqq["close"]).replace([np.inf, -np.inf], np.nan).ffill()
qqq_adjopen = (qqq["open"] * qqq_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()
tqqq_adjopen = (tqqq["open"] * tqqq_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()
close_ret_qqq_full = qqq["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_qqq_full = (qqq_adjopen.shift(-1) / qqq_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_tqqq_full = (tqqq_adjopen.shift(-1) / tqqq_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)

fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
vol = close_ret_qqq_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
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
    tqqq_cap,
    max_risk_exposure,
    strong_qqq_risk_share,
    strong_cash_sweep,
    weak_risk_multiplier,
    weak_qqq_risk_share,
    weak_tqqq_cap,
    weak_cash_sweep,
    turnaround_tqqq_weight,
    bear_qqq,
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
            tqqq_cap,
            max_risk_exposure,
            strong_qqq_risk_share,
            strong_cash_sweep,
            weak_risk_multiplier,
            weak_qqq_risk_share,
            weak_tqqq_cap,
            weak_cash_sweep,
            turnaround_tqqq_weight,
            bear_qqq,
        )
        for date in common_idx
    ],
    index=common_idx,
)
ret_qqq = ret_qqq_full.reindex(common_idx).fillna(0.0)
ret_tqqq = ret_tqqq_full.reindex(common_idx).fillna(0.0)
legacy_ret = legacy_backtest(weights, ret_qqq, ret_tqqq, cost_rate)
open_prices = pd.DataFrame(
    {
        "QQQ": qqq_adjopen.reindex(common_idx).ffill(),
        "TQQQ": tqqq_adjopen.reindex(common_idx).ffill(),
    }
)
strategy_ret, actual_weights, executed_turnover = holdings_backtest(
    weights,
    open_prices,
    cost_rate,
)

bench_qqq = ret_qqq
bench_tqqq = ret_tqqq
fixed_20 = 0.8 * ret_qqq + 0.2 * ret_tqqq
fixed_30 = 0.7 * ret_qqq + 0.3 * ret_tqqq

strategy_metrics = calc_metrics(strategy_ret)
legacy_metrics = calc_metrics(legacy_ret)
summary = pd.DataFrame(
    [
        metric_row("Strategy V2 (Holdings)", strategy_ret, actual_weights["QQQ"], actual_weights["TQQQ"]),
        metric_row("Legacy (Daily Target)", legacy_ret, weights["QQQ"], weights["TQQQ"]),
        metric_row("QQQ 100%", bench_qqq),
        metric_row("TQQQ 100%", bench_tqqq),
        metric_row("QQQ 80% + TQQQ 20%", fixed_20),
        metric_row("QQQ 70% + TQQQ 30%", fixed_30),
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
    tqqq_cap,
    max_risk_exposure,
    strong_qqq_risk_share,
    strong_cash_sweep,
    weak_risk_multiplier,
    weak_qqq_risk_share,
    weak_tqqq_cap,
    weak_cash_sweep,
    turnaround_tqqq_weight,
    bear_qqq,
)
latest_prices = pd.Series(
    {
        "QQQ": qqq["adjclose"].reindex(weights.index).ffill().iloc[-1],
        "TQQQ": tqqq["adjclose"].reindex(weights.index).ffill().iloc[-1],
    }
)
current_shares = pd.Series({"QQQ": current_qqq_shares, "TQQQ": current_tqqq_shares})
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
    f"QQQ {next_target['QQQ']:.1%}, TQQQ {next_target['TQQQ']:.1%}, Cash {1 - next_target.sum():.1%} | "
    f"QQQ {vol_window}D volatility {latest_vol:.1%}"
)
st.info(
    f"V2 holdings vs legacy daily-target calculation | "
    f"Total return difference {strategy_metrics['total'] - legacy_metrics['total']:+.1%}p | "
    f"CAGR difference {strategy_metrics['cagr'] - legacy_metrics['cagr']:+.2%}p | "
    f"MDD difference {strategy_metrics['mdd'] - legacy_metrics['mdd']:+.2%}p"
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
            "Strategy V2": strategy_metrics["nav"],
            "Legacy": legacy_metrics["nav"],
            "QQQ": calc_metrics(bench_qqq)["nav"],
            "TQQQ": calc_metrics(bench_tqqq)["nav"],
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
            "Strategy V2 DD": strategy_metrics["dd"],
            "Legacy DD": legacy_metrics["dd"],
            "QQQ DD": calc_metrics(bench_qqq)["dd"],
            "TQQQ DD": calc_metrics(bench_tqqq)["dd"],
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
                "Strategy V2": strategy_metrics["nav"],
                "Legacy": legacy_metrics["nav"],
                "QQQ": calc_metrics(bench_qqq)["nav"],
                "TQQQ": calc_metrics(bench_tqqq)["nav"],
            },
            "Yearly Returns",
            height=330,
        ),
        clear_figure=True,
    )
    performance_weight_df = actual_weights.copy()
    performance_weight_df["Cash"] = (1 - performance_weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(static_area_chart(performance_weight_df, "Portfolio Weights", height=300), clear_figure=True)

with tab_execute:
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
            "QQQ": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
        }
    )
    st.pyplot(
        static_line_chart(signal_df, "QQQ Trend", yaxis_title="Price", height=320),
        clear_figure=True,
    )

    weight_df = actual_weights.copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(
        static_area_chart(weight_df, "Portfolio Weights", height=300),
        clear_figure=True,
    )

    st.subheader("Recent Signals")
    recent = pd.DataFrame(
        {
            "QQQ": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
            f"Exit MA{turnaround_exit_fast}": price.rolling(turnaround_exit_fast).mean().reindex(common_idx),
            f"Exit MA{turnaround_exit_slow}": price.rolling(turnaround_exit_slow).mean().reindex(common_idx),
            "QQQ DD": price.reindex(common_idx) / price.reindex(common_idx).cummax() - 1,
            f"Vol{vol_window}": vol.reindex(common_idx),
            "Applied Regime": display_regime_signal,
            "Target Regime": close_display_regime_signal,
            "Applied Turnaround": turnaround,
            "Target Turnaround": close_turnaround,
            "Applied Target QQQ": weights["QQQ"],
            "Applied Target TQQQ": weights["TQQQ"],
            "Actual QQQ": actual_weights["QQQ"],
            "Actual TQQQ": actual_weights["TQQQ"],
            "Next Target QQQ": close_target_weights["QQQ"],
            "Next Target TQQQ": close_target_weights["TQQQ"],
            "Target Cash": (1 - close_target_weights.sum(axis=1)).clip(0, 1),
        }
    ).tail(30)
    st.dataframe(recent, use_container_width=True)

with tab_table:
    shown = summary.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg QQQ", "Avg TQQQ", "Max TQQQ"]:
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
