"""SOXX / SOXL downside-volatility target backtest v6."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st
from chart_utils import position_action_label
from chart_utils import static_area_chart
from chart_utils import static_line_chart
from chart_utils import static_yearly_returns_chart

TRADING_DAYS = 252
SOXX = "SOXX"
SOXL = "SOXL"

st.set_page_config(page_title="SOXX/SOXL Downside Vol Target V6", page_icon="US", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("SOXX / SOXL Downside Volatility Target Backtest V6")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True, key="run_backtest_top")
st.caption(
    "V6: upside volatility is treated as opportunity in bullish momentum, while downside volatility "
    "and crash filters control cash and SOXL exposure."
)


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


def rolling_semivol(daily_ret: pd.Series, window: int, side: str) -> pd.Series:
    if side == "downside":
        selected = daily_ret.clip(upper=0.0)
    else:
        selected = daily_ret.clip(lower=0.0)
    return np.sqrt(selected.pow(2).rolling(window).mean()) * np.sqrt(TRADING_DAYS)


def build_upside_strength(
    price: pd.Series,
    daily_ret: pd.Series,
    trend_signal: pd.Series,
    momentum_window: int,
    min_up_days: float,
) -> pd.Series:
    momentum = price.pct_change(momentum_window)
    up_day_ratio = daily_ret.gt(0).rolling(momentum_window).mean()
    return (trend_signal & momentum.gt(0) & up_day_ratio.ge(min_up_days)).fillna(False)


def build_effective_vol(
    normal_vol: pd.Series,
    downside_vol: pd.Series,
    upside_vol: pd.Series,
    upside_strength: pd.Series,
    mode: str,
    upside_credit: float,
    min_risk_vol: float,
) -> pd.Series:
    conservative_vol = pd.concat([normal_vol, downside_vol], axis=1).max(axis=1)
    credited_vol = (normal_vol - upside_vol * upside_credit).clip(lower=downside_vol)

    if mode == "Normal volatility":
        effective_vol = normal_vol.copy()
    elif mode == "Downside volatility":
        effective_vol = downside_vol.copy()
    else:
        effective_vol = conservative_vol.copy()
        effective_vol.loc[upside_strength] = credited_vol.loc[upside_strength]

    effective_vol = effective_vol.replace([np.inf, -np.inf], np.nan)
    effective_vol = effective_vol.where(effective_vol > 0, normal_vol)
    return effective_vol.clip(lower=min_risk_vol)


def build_crash_signal(
    price: pd.Series,
    downside_vol: pd.Series,
    fast_ma: pd.Series,
    loss_window: int,
    loss_trigger: float,
    downside_vol_trigger: float,
) -> pd.Series:
    short_loss = price.pct_change(loss_window)
    fast_ma_break = price < fast_ma
    return ((short_loss <= -loss_trigger) | ((downside_vol >= downside_vol_trigger) & fast_ma_break)).fillna(False)


def build_regime_signal(
    trend_signal: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    risk_vol: pd.Series,
    strong_spread: float,
    weak_vol_cutoff: float,
    lag_for_execution: bool = True,
) -> pd.Series:
    if lag_for_execution:
        bull = trend_signal.shift(1).fillna(False)
        ma_spread = (fast_ma / slow_ma - 1).shift(1).replace([np.inf, -np.inf], np.nan)
        signal_vol = risk_vol.shift(1)
    else:
        bull = trend_signal.fillna(False)
        ma_spread = (fast_ma / slow_ma - 1).replace([np.inf, -np.inf], np.nan)
        signal_vol = risk_vol
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
    regime: pd.Series,
    turnaround_signal: pd.Series,
    crash_signal: pd.Series,
    risk_vol: pd.Series,
    target_vol: float,
    soxl_cap: float,
    max_risk_exposure: float,
    strong_soxx_risk_share: float,
    weak_risk_multiplier: float,
    weak_soxx_risk_share: float,
    weak_soxl_cap: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
    crash_soxx_cap: float,
    crash_soxl_cap: float,
    rebalance: str,
) -> pd.DataFrame:
    vol_lag = risk_vol.shift(1).replace(0, np.nan)
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

    crash_rows = crash_signal.fillna(False)
    weights.loc[crash_rows, "SOXX"] = weights.loc[crash_rows, "SOXX"].clip(upper=crash_soxx_cap)
    weights.loc[crash_rows, "SOXL"] = weights.loc[crash_rows, "SOXL"].clip(upper=crash_soxl_cap)

    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)


def calc_target_weight(
    regime: str,
    is_turnaround: bool,
    is_crash: bool,
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
    crash_soxx_cap: float,
    crash_soxl_cap: float,
) -> pd.Series:
    if is_turnaround:
        target = pd.Series({"SOXX": 1 - turnaround_soxl_weight, "SOXL": turnaround_soxl_weight})
    elif regime == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        target = pd.Series({"SOXX": bear_soxx, "SOXL": 0.0})
    else:
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

    if is_crash:
        target["SOXX"] = min(target["SOXX"], crash_soxx_cap)
        target["SOXL"] = min(target["SOXL"], crash_soxl_cap)

    if target.sum() > 1:
        target = target / target.sum()
    return target.clip(0, 1)


def backtest(weights: pd.DataFrame, ret_soxx: pd.Series, ret_soxl: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    daily_ret = weights["SOXX"] * ret_soxx + weights["SOXL"] * ret_soxl - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


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
    vol_mode = st.selectbox(
        "Risk volatility mode",
        ["Downside volatility", "Hybrid upside-credit", "Normal volatility"],
        index=0,
    )
    target_vol = st.slider("Target volatility (%)", 10, 80, 45, 5) / 100
    min_risk_vol = st.slider("Minimum risk volatility floor (%)", 5, 50, 20, 5) / 100
    upside_credit = st.slider("Upside volatility credit (%)", 0, 80, 35, 5) / 100
    momentum_window = st.slider("Upside momentum window", 10, 80, 20, 5)
    min_up_days = st.slider("Upside day ratio for credit (%)", 45, 75, 55, 5) / 100
    soxl_cap = st.slider("SOXL max weight (%)", 0, 80, 50, 5) / 100
    max_risk_exposure = st.slider("Max risk exposure", 0.5, 2.0, 1.5, 0.1)

    st.subheader("Downside Crash Filter")
    crash_loss_window = st.slider("Crash loss window", 3, 20, 5, 1)
    crash_loss_trigger = st.slider("Crash loss trigger (%)", 3, 25, 8, 1) / 100
    crash_downside_vol_trigger = st.slider("Crash downside vol trigger (%)", 30, 120, 65, 5) / 100
    crash_soxx_cap = st.slider("Crash SOXX max weight (%)", 0, 100, 50, 5) / 100
    crash_soxl_cap = st.slider("Crash SOXL max weight (%)", 0, 40, 0, 5) / 100

    st.subheader("Regime Blend")
    strong_spread = st.slider("Strong Bull MA spread (%)", 0, 20, 5, 1) / 100
    weak_vol_cutoff = st.slider("Weak Bull if risk volatility above (%)", 20, 100, 55, 5) / 100
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
    account_value = st.number_input("Account value ($)", min_value=0.0, value=10000.0, step=1000.0)
    current_soxx_shares = st.number_input("Current SOXX shares", min_value=0.0, value=0.0, step=1.0)
    current_soxl_shares = st.number_input("Current SOXL shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash ($)", min_value=0.0, value=10000.0, step=1000.0)

with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| Item | Value |
|---|---|
| Bull regime | SOXX MA{fast_window} > MA{slow_window} |
| Risk volatility mode | {vol_mode} |
| Target volatility | {target_vol:.0%} |
| Minimum risk vol floor | {min_risk_vol:.0%} |
| Upside credit | {upside_credit:.0%} when trend, momentum, and up-day ratio confirm upside strength |
| SOXL cap | {soxl_cap:.0%} |
| Max risk exposure | {max_risk_exposure:.1f}x SOXX-equivalent risk |
| Strong Bull | Bull trend + MA spread >= {strong_spread:.0%} + risk volatility <= {weak_vol_cutoff:.0%} |
| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, SOXX gets {weak_soxx_risk_share:.0%}, SOXL cap {weak_soxl_cap:.0%} |
| Crash filter | {crash_loss_window}D loss <= -{crash_loss_trigger:.0%} or downside vol >= {crash_downside_vol_trigger:.0%} with fast MA break |
| Crash caps | SOXX <= {crash_soxx_cap:.0%}, SOXL <= {crash_soxl_cap:.0%} |
| Turnaround Bull | SOXX drawdown <= -{turnaround_dd_trigger:.0%}, then golden cross occurs |
| Turnaround allocation | SOXX {1 - turnaround_soxl_weight:.0%} + SOXL {turnaround_soxl_weight:.0%}, unless crash filter caps exposure |
| Bear regime | Cash {1 - bear_soxx:.0%} + SOXX {bear_soxx:.0%} |
"""
    )

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

progress = st.progress(0, text="Loading SOXX/SOXL data...")
try:
    warmup_days = max(slow_window, vol_window, momentum_window, turnaround_exit_slow) * 3
    warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=warmup_days)
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
ret_soxx_full = (soxx_adjopen.shift(-1) / soxx_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_soxl_full = (soxl_adjopen.shift(-1) / soxl_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)

fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
normal_vol = close_ret_soxx_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
downside_vol = rolling_semivol(close_ret_soxx_full, vol_window, "downside")
upside_vol = rolling_semivol(close_ret_soxx_full, vol_window, "upside")
trend_signal = build_trend_signal(price, fast_ma, slow_ma, trend_rule)
upside_strength = build_upside_strength(price, close_ret_soxx_full, trend_signal, momentum_window, min_up_days)
risk_vol = build_effective_vol(
    normal_vol,
    downside_vol,
    upside_vol,
    upside_strength,
    vol_mode,
    upside_credit,
    min_risk_vol,
)
raw_crash_signal = build_crash_signal(
    price,
    downside_vol,
    fast_ma,
    crash_loss_window,
    crash_loss_trigger,
    crash_downside_vol_trigger,
)
crash_signal = raw_crash_signal.shift(1).fillna(False)
close_crash_signal = raw_crash_signal.fillna(False)
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
    risk_vol,
    strong_spread,
    weak_vol_cutoff,
)
close_regime_signal = build_regime_signal(
    trend_signal,
    fast_ma,
    slow_ma,
    risk_vol,
    strong_spread,
    weak_vol_cutoff,
    lag_for_execution=False,
)

weights_full = build_strategy_weights(
    price,
    regime_signal,
    turnaround_signal,
    crash_signal,
    risk_vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    strong_soxx_risk_share,
    weak_risk_multiplier,
    weak_soxx_risk_share,
    weak_soxl_cap,
    turnaround_soxl_weight,
    bear_soxx,
    crash_soxx_cap,
    crash_soxl_cap,
    rebalance,
)

weights = weights_full.reindex(common_idx).fillna(0.0)
turnaround = turnaround_signal.reindex(common_idx).fillna(False)
crash = crash_signal.reindex(common_idx).fillna(False)
display_regime_signal = regime_signal.reindex(common_idx).where(~turnaround, "Turnaround Bull")
display_regime_signal = display_regime_signal.where(~crash, display_regime_signal + " + Crash Filter")
close_turnaround = close_turnaround_signal.reindex(common_idx).fillna(False)
close_crash = close_crash_signal.reindex(common_idx).fillna(False)
close_display_regime_signal = close_regime_signal.reindex(common_idx).where(~close_turnaround, "Turnaround Bull")
close_display_regime_signal = close_display_regime_signal.where(~close_crash, close_display_regime_signal + " + Crash Filter")
close_target_weights = pd.DataFrame(
    [
        calc_target_weight(
            str(close_regime_signal.ffill().loc[date]),
            bool(close_turnaround_signal.fillna(False).loc[date]),
            bool(close_crash_signal.fillna(False).loc[date]),
            risk_vol.ffill().loc[date],
            target_vol,
            soxl_cap,
            max_risk_exposure,
            strong_soxx_risk_share,
            weak_risk_multiplier,
            weak_soxx_risk_share,
            weak_soxl_cap,
            turnaround_soxl_weight,
            bear_soxx,
            crash_soxx_cap,
            crash_soxl_cap,
        )
        for date in common_idx
    ],
    index=common_idx,
)
ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)
ret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_soxx, ret_soxl, cost_rate)

bench_soxx = ret_soxx
bench_soxl = ret_soxl
fixed_20 = 0.8 * ret_soxx + 0.2 * ret_soxl
fixed_30 = 0.7 * ret_soxx + 0.3 * ret_soxl

strategy_metrics = calc_metrics(strategy_ret)
summary = pd.DataFrame(
    [
        metric_row("Strategy V6", strategy_ret, weights["SOXX"], weights["SOXL"]),
        metric_row("SOXX 100%", bench_soxx),
        metric_row("SOXL 100%", bench_soxl),
        metric_row("SOXX 80% + SOXL 20%", fixed_20),
        metric_row("SOXX 70% + SOXL 30%", fixed_30),
    ]
)

progress.progress(100, text="Done")
progress.empty()

latest_date = weights.index[-1].date()
latest_turnaround = bool(close_turnaround_signal.reindex(weights.index).fillna(False).iloc[-1])
latest_crash = bool(close_crash_signal.reindex(weights.index).fillna(False).iloc[-1])
latest_regime = str(close_display_regime_signal.ffill().iloc[-1])
latest_normal_vol = normal_vol.reindex(weights.index).ffill().iloc[-1]
latest_downside_vol = downside_vol.reindex(weights.index).ffill().iloc[-1]
latest_risk_vol = risk_vol.reindex(weights.index).ffill().iloc[-1]
latest_upside_strength = bool(upside_strength.reindex(weights.index).fillna(False).iloc[-1])
next_target = calc_target_weight(
    str(close_regime_signal.reindex(weights.index).ffill().iloc[-1]),
    latest_turnaround,
    latest_crash,
    latest_risk_vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    strong_soxx_risk_share,
    weak_risk_multiplier,
    weak_soxx_risk_share,
    weak_soxl_cap,
    turnaround_soxl_weight,
    bear_soxx,
    crash_soxx_cap,
    crash_soxl_cap,
)
latest_prices = pd.Series(
    {
        "SOXX": soxx["adjclose"].reindex(weights.index).ffill().iloc[-1],
        "SOXL": soxl["adjclose"].reindex(weights.index).ffill().iloc[-1],
    }
)
current_shares = pd.Series({"SOXX": current_soxx_shares, "SOXL": current_soxl_shares})
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
    f"Risk vol {latest_risk_vol:.1%}, downside vol {latest_downside_vol:.1%}, normal vol {latest_normal_vol:.1%} | "
    f"Upside credit {'ON' if latest_upside_strength else 'OFF'}"
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
            "Strategy V6": strategy_metrics["nav"],
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
                "Strategy V6": strategy_metrics["nav"],
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

    vol_df = pd.DataFrame(
        {
            "Risk Vol": risk_vol.reindex(common_idx),
            "Downside Vol": downside_vol.reindex(common_idx),
            "Normal Vol": normal_vol.reindex(common_idx),
        }
    ) * 100
    st.pyplot(
        static_line_chart(vol_df, "Risk Volatility Inputs", yaxis_title="Volatility", percent_axis=True, height=280),
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
            "SOXX DD": price.reindex(common_idx) / price.reindex(common_idx).cummax() - 1,
            f"Normal Vol{vol_window}": normal_vol.reindex(common_idx),
            f"Downside Vol{vol_window}": downside_vol.reindex(common_idx),
            "Risk Vol": risk_vol.reindex(common_idx),
            "Upside Credit": upside_strength.reindex(common_idx).fillna(False),
            "Applied Crash": crash,
            "Target Crash": close_crash,
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
