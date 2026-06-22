"""SOXX / SOXL volatility-defensive re-entry backtest v8."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st
from chart_utils import position_action_label, static_area_chart, static_line_chart, static_yearly_returns_chart

TRADING_DAYS = 252
SOXX = "SOXX"
SOXL = "SOXL"

st.set_page_config(page_title="SOXX/SOXL Defensive Re-entry V8", page_icon="US", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("SOXX / SOXL Defensive Re-entry Backtest V8")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)
st.caption(
    "V8 keeps the v5 high-volatility cash defense. SOXL is reopened only after volatility contracts "
    "and trend/momentum confirm re-entry."
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
    idx = pd.to_datetime(result["timestamp"], unit="s").normalize()
    df = pd.DataFrame(
        {
            "open": quote["open"],
            "close": quote["close"],
            "adjclose": result["indicators"]["adjclose"][0]["adjclose"],
            "volume": quote["volume"],
        },
        index=idx,
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
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS) if daily_ret.std() > 0 else 0.0
    win_m = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return {
        "nav": nav,
        "dd": dd,
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": cagr / abs(mdd) if mdd < 0 else 0.0,
        "win_m": win_m,
        "mdd_date": mdd_date,
        "mdd_peak_date": nav.loc[:mdd_date].idxmax(),
        "mdd_peak_value": peak_nav.loc[mdd_date],
        "mdd_trough_value": nav.loc[mdd_date],
    }


def metric_row(name: str, daily_ret: pd.Series, weights: pd.DataFrame | None = None) -> dict[str, object]:
    metrics = calc_metrics(daily_ret)
    return {
        "Strategy": name,
        "Total": metrics["total"],
        "CAGR": metrics["cagr"],
        "MDD": metrics["mdd"],
        "Sharpe": metrics["sharpe"],
        "Calmar": metrics["calmar"],
        "Monthly Win": metrics["win_m"],
        "Avg SOXX": np.nan if weights is None else weights["SOXX"].mean(),
        "Avg SOXL": np.nan if weights is None else weights["SOXL"].mean(),
        "Max SOXL": np.nan if weights is None else weights["SOXL"].max(),
    }


def build_trend(price: pd.Series, fast_ma: pd.Series, slow_ma: pd.Series, rule: str) -> pd.Series:
    if rule == "MA Fast > MA Slow":
        return fast_ma > slow_ma
    if rule == "Close > MA Slow":
        return price > slow_ma
    return (price > slow_ma) & (fast_ma > slow_ma)


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


def build_turnaround(
    price: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    dd_trigger: float,
    exit_fast: int,
    exit_slow: int,
    exit_confirm: int,
    lag: bool = True,
) -> pd.Series:
    price_dd = price / price.cummax() - 1
    golden_cross = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
    exit_signal = price.rolling(exit_fast).mean() < price.rolling(exit_slow).mean()
    active = pd.Series(False, index=price.index)
    armed = False
    in_turnaround = False
    exit_count = 0
    for date in price.index:
        if pd.notna(price_dd.loc[date]) and price_dd.loc[date] <= -dd_trigger:
            armed = True
        if bool(golden_cross.loc[date]) and armed:
            in_turnaround = True
            armed = False
            exit_count = 0
        if in_turnaround:
            active.loc[date] = True
            exit_count = exit_count + 1 if bool(exit_signal.loc[date]) else 0
            if exit_count >= exit_confirm:
                active.loc[date] = False
                in_turnaround = False
                exit_count = 0
    return active.shift(1).fillna(False) if lag else active.fillna(False)


def allocate(desired_risk: pd.Series, soxx_share: float, soxl_cap: pd.Series | float) -> tuple[pd.Series, pd.Series]:
    soxx = (desired_risk * soxx_share).clip(0, 1)
    soxl = ((desired_risk - soxx) / 3).clip(lower=0)
    soxl = pd.concat([soxl, pd.Series(soxl_cap, index=desired_risk.index)], axis=1).min(axis=1)
    risk_used = soxx + soxl * 3
    soxx = (soxx + (desired_risk - risk_used).clip(lower=0)).clip(0, 1 - soxl)
    return soxx, soxl


def backtest(weights: pd.DataFrame, ret_soxx: pd.Series, ret_soxl: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    return (weights["SOXX"] * ret_soxx + weights["SOXL"] * ret_soxl - turnover * cost_rate).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_weights(
    price: pd.Series,
    trend: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    vol: pd.Series,
    close_ret: pd.Series,
    turnaround: pd.Series,
    p: dict[str, float | str],
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired = (float(p["target_vol"]) / vol_lag).clip(0, float(p["max_risk_exposure"])).fillna(0.0)
    ma_spread = (fast_ma / slow_ma - 1).shift(1).replace([np.inf, -np.inf], np.nan)
    bull = trend.shift(1).fillna(False)

    vol_ma = vol.rolling(int(p["vol_contract_window"])).mean()
    vol_contracting = (vol < vol_ma * float(p["vol_contract_ratio"])) & (vol.diff(int(p["vol_fall_days"])) < 0)
    positive_momentum = price.pct_change(int(p["momentum_window"])) > float(p["min_momentum"])
    reentry = (trend & vol_contracting & positive_momentum).shift(1).fillna(False)
    high_vol_lock = (vol.shift(1) >= float(p["high_vol_cutoff"])).fillna(False)

    strong = bull & ma_spread.ge(float(p["strong_spread"])) & vol_lag.le(float(p["weak_vol_cutoff"]))
    weak = bull & ~strong
    soxl_cap = pd.Series(float(p["weak_soxl_cap"]), index=price.index)
    soxl_cap.loc[strong] = float(p["strong_soxl_cap"])
    soxl_cap.loc[~reentry] = 0.0
    soxl_cap.loc[high_vol_lock] = 0.0

    strong_soxx, strong_soxl = allocate(desired, float(p["strong_soxx_share"]), soxl_cap)
    weak_risk = (desired * float(p["weak_risk_mult"])).clip(0, float(p["max_risk_exposure"]))
    weak_soxx, weak_soxl = allocate(weak_risk, float(p["weak_soxx_share"]), soxl_cap.clip(upper=float(p["weak_soxl_cap"])))

    weights = pd.DataFrame(0.0, index=price.index, columns=["SOXX", "SOXL"])
    weights["SOXX"] = np.select([strong, weak], [strong_soxx, weak_soxx], default=float(p["bear_soxx"]))
    weights["SOXL"] = np.select([strong, weak], [strong_soxl, weak_soxl], default=0.0)

    crash = (price.pct_change(int(p["crash_loss_window"])) <= -float(p["crash_loss_trigger"]))
    crash = (crash | ((vol >= float(p["crash_vol_trigger"])) & (price < fast_ma))).shift(1).fillna(False)
    weights.loc[crash, "SOXX"] = weights.loc[crash, "SOXX"].clip(upper=float(p["crash_soxx_cap"]))
    weights.loc[crash, "SOXL"] = 0.0

    turnaround_ok = turnaround & reentry & ~high_vol_lock & ~crash
    weights.loc[turnaround_ok, "SOXX"] = 1 - float(p["turnaround_soxl"])
    weights.loc[turnaround_ok, "SOXL"] = float(p["turnaround_soxl"])

    total = weights.sum(axis=1)
    weights = weights.mul(pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index), axis=0).clip(0, 1)
    return rebalance_weights(weights, str(p["rebalance"])), reentry, high_vol_lock, crash


with st.sidebar:
    st.header("Settings")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 5, 12))
    with c2:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Trend Filter")
    trend_rule = st.selectbox("Rule", ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow + MA Fast > MA Slow"], index=0)
    fast_window = st.slider("Fast MA", 20, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Defense")
    vol_window = st.slider("Volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 80, 45, 5) / 100
    high_vol_cutoff = st.slider("High-vol SOXL lockout (%)", 35, 100, 60, 5) / 100
    vol_contract_window = st.slider("Vol contraction average window", 20, 120, 60, 5)
    vol_contract_ratio = st.slider("Vol contraction ratio (%)", 60, 110, 90, 5) / 100
    vol_fall_days = st.slider("Vol falling lookback days", 3, 30, 10, 1)
    momentum_window = st.slider("Re-entry momentum window", 10, 80, 20, 5)
    min_momentum = st.slider("Minimum re-entry momentum (%)", -10, 20, 0, 1) / 100

    st.subheader("SOXL Re-entry Caps")
    strong_soxl_cap = st.slider("Strong Bull SOXL cap (%)", 0, 60, 30, 5) / 100
    weak_soxl_cap = st.slider("Weak Bull SOXL cap (%)", 0, 30, 5, 5) / 100
    max_risk_exposure = st.slider("Max risk exposure", 0.5, 2.0, 1.5, 0.1)
    strong_spread = st.slider("Strong Bull MA spread (%)", 0, 20, 5, 1) / 100
    weak_vol_cutoff = st.slider("Weak Bull if volatility above (%)", 20, 100, 55, 5) / 100
    strong_soxx_share = st.slider("Strong Bull SOXX risk share (%)", 0, 60, 20, 5) / 100
    weak_risk_mult = st.slider("Weak Bull risk multiplier (%)", 30, 100, 75, 5) / 100
    weak_soxx_share = st.slider("Weak Bull SOXX risk share (%)", 40, 100, 80, 5) / 100

    st.subheader("Crash / Turnaround")
    crash_loss_window = st.slider("Crash loss window", 3, 20, 5, 1)
    crash_loss_trigger = st.slider("Crash loss trigger (%)", 3, 25, 8, 1) / 100
    crash_vol_trigger = st.slider("Crash vol trigger (%)", 40, 120, 75, 5) / 100
    crash_soxx_cap = st.slider("Crash SOXX max weight (%)", 0, 100, 30, 5) / 100
    turnaround_dd = st.slider("Turnaround drawdown trigger (%)", 10, 50, 20, 5) / 100
    turnaround_soxl = st.slider("Turnaround SOXL weight (%)", 0, 60, 25, 5) / 100
    turn_exit_fast = st.slider("Turnaround exit fast MA", 3, 20, 10, 1)
    turn_exit_slow = st.slider("Turnaround exit slow MA", 10, 60, 60, 5)
    turn_exit_confirm = st.slider("Exit confirmation days", 1, 5, 2, 1)

    st.subheader("Bear / Trading")
    bear_soxx = st.slider("Bear-regime SOXX weight (%)", 0, 100, 20, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=0, horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.25, step=0.01) / 100

    st.subheader("Execution")
    account_value = st.number_input("Account value ($)", min_value=0.0, value=10000.0, step=1000.0)
    current_soxx_shares = st.number_input("Current SOXX shares", min_value=0.0, value=0.0, step=1.0)
    current_soxl_shares = st.number_input("Current SOXL shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash ($)", min_value=0.0, value=10000.0, step=1000.0)

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

p = {
    "target_vol": target_vol,
    "high_vol_cutoff": high_vol_cutoff,
    "vol_contract_window": vol_contract_window,
    "vol_contract_ratio": vol_contract_ratio,
    "vol_fall_days": vol_fall_days,
    "momentum_window": momentum_window,
    "min_momentum": min_momentum,
    "strong_soxl_cap": strong_soxl_cap,
    "weak_soxl_cap": weak_soxl_cap,
    "max_risk_exposure": max_risk_exposure,
    "strong_spread": strong_spread,
    "weak_vol_cutoff": weak_vol_cutoff,
    "strong_soxx_share": strong_soxx_share,
    "weak_risk_mult": weak_risk_mult,
    "weak_soxx_share": weak_soxx_share,
    "crash_loss_window": crash_loss_window,
    "crash_loss_trigger": crash_loss_trigger,
    "crash_vol_trigger": crash_vol_trigger,
    "crash_soxx_cap": crash_soxx_cap,
    "turnaround_soxl": turnaround_soxl,
    "bear_soxx": bear_soxx,
    "rebalance": rebalance,
}

progress = st.progress(0, text="Loading SOXX/SOXL data...")
try:
    warmup_days = max(slow_window, vol_window, vol_contract_window, momentum_window, turn_exit_slow) * 3
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
ret_soxx_full = (soxx_adjopen.shift(-1) / soxx_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_soxl_full = (soxl_adjopen.shift(-1) / soxl_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
close_ret = soxx["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)
ret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)

fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
trend = build_trend(price, fast_ma, slow_ma, trend_rule)
vol = close_ret.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
turnaround = build_turnaround(price, fast_ma, slow_ma, turnaround_dd, turn_exit_fast, turn_exit_slow, turn_exit_confirm)
weights_full, reentry, high_vol_lock, crash = build_weights(price, trend, fast_ma, slow_ma, vol, close_ret, turnaround, p)
weights = weights_full.reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_soxx, ret_soxl, cost_rate)
metrics = calc_metrics(strategy_ret)
progress.empty()

latest = weights.iloc[-1]
latest_date = weights.index[-1].date()
latest_vol = vol.reindex(common_idx).ffill().iloc[-1]
latest_reentry = bool(reentry.reindex(common_idx).fillna(False).iloc[-1])
latest_lock = bool(high_vol_lock.reindex(common_idx).fillna(False).iloc[-1])
latest_crash = bool(crash.reindex(common_idx).fillna(False).iloc[-1])
latest_prices = pd.Series(
    {
        "SOXX": soxx["adjclose"].reindex(common_idx).ffill().iloc[-1],
        "SOXL": soxl["adjclose"].reindex(common_idx).ffill().iloc[-1],
    }
)
current_shares = pd.Series({"SOXX": current_soxx_shares, "SOXL": current_soxl_shares})
current_values = current_shares * latest_prices
effective_value = account_value if account_value > 0 else current_values.sum() + current_cash
orders = []
for symbol in [SOXX, SOXL]:
    target_value = effective_value * latest[symbol]
    target_shares = np.floor(target_value / latest_prices[symbol]) if latest_prices[symbol] > 0 else 0
    order_shares = target_shares - current_shares[symbol]
    orders.append(
        {
            "Symbol": symbol,
            "Latest Price": latest_prices[symbol],
            "Target Weight": latest[symbol],
            "Target Value": target_value,
            "Target Shares": target_shares,
            "Current Shares": current_shares[symbol],
            "Order": "Buy" if order_shares > 0 else "Sell" if order_shares < 0 else "Hold",
            "Order Shares": order_shares,
            "Estimated Order Value": abs(order_shares) * latest_prices[symbol],
        }
    )
execution_plan = pd.DataFrame(orders)
action_label = position_action_label(execution_plan["Order Shares"].abs().sum(), tolerance=0.5)

st.success(
    f"{action_label} | Signal date {latest_date} | SOXX {latest['SOXX']:.1%}, SOXL {latest['SOXL']:.1%}, "
    f"Cash {1 - latest.sum():.1%} | Vol {latest_vol:.1%} | "
    f"Re-entry {'ON' if latest_reentry else 'OFF'} | High-vol lock {'ON' if latest_lock else 'OFF'} | Crash {'ON' if latest_crash else 'OFF'}"
)

cols = st.columns(6)
cols[0].metric("Total", f"{metrics['total']:.1%}")
cols[1].metric("CAGR", f"{metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{metrics['win_m']:.1%}")

tab_perf, tab_exec, tab_signal, tab_table, tab_monthly = st.tabs(
    ["Performance", "Execution", "Signal / Weights", "Comparison", "Monthly"]
)

with tab_perf:
    nav_df = pd.DataFrame(
        {
            "Strategy V8": metrics["nav"],
            "SOXX": calc_metrics(ret_soxx)["nav"],
            "SOXL": calc_metrics(ret_soxl)["nav"],
            "80/20": calc_metrics(0.8 * ret_soxx + 0.2 * ret_soxl)["nav"],
        }
    )
    st.pyplot(
        static_line_chart(
            nav_df,
            "Cumulative NAV with Strategy MDD",
            yaxis_title="NAV",
            height=380,
            mdd_info={
                "date": metrics["mdd_date"],
                "peak_date": metrics["mdd_peak_date"],
                "value": metrics["mdd"],
                "peak_value": metrics["mdd_peak_value"],
                "trough_value": metrics["mdd_trough_value"],
            },
        ),
        clear_figure=True,
    )
    dd_df = pd.DataFrame(
        {
            "Strategy DD": metrics["dd"],
            "SOXX DD": calc_metrics(ret_soxx)["dd"],
            "SOXL DD": calc_metrics(ret_soxl)["dd"],
        }
    ) * 100
    st.pyplot(static_line_chart(dd_df, "Drawdown", yaxis_title="Drawdown", percent_axis=True, height=280), clear_figure=True)
    st.pyplot(
        static_yearly_returns_chart(
            {"Strategy V8": metrics["nav"], "SOXX": calc_metrics(ret_soxx)["nav"], "SOXL": calc_metrics(ret_soxl)["nav"]},
            "Yearly Returns",
            height=330,
        ),
        clear_figure=True,
    )

with tab_exec:
    shown_exec = execution_plan.copy()
    for col in ["Latest Price", "Target Value", "Estimated Order Value"]:
        shown_exec[col] = shown_exec[col].map(lambda x: f"${x:,.2f}")
    shown_exec["Target Weight"] = shown_exec["Target Weight"].map(lambda x: f"{x:.1%}")
    for col in ["Target Shares", "Current Shares", "Order Shares"]:
        shown_exec[col] = shown_exec[col].map(lambda x: f"{x:,.0f}")
    st.dataframe(shown_exec, use_container_width=True, hide_index=True)

with tab_signal:
    st.pyplot(
        static_line_chart(
            pd.DataFrame({"SOXX": price.reindex(common_idx), f"MA{fast_window}": fast_ma.reindex(common_idx), f"MA{slow_window}": slow_ma.reindex(common_idx)}),
            "SOXX Trend",
            "Price",
            height=320,
        ),
        clear_figure=True,
    )
    st.pyplot(static_line_chart((vol.reindex(common_idx).to_frame("SOXX Vol") * 100), "Volatility", "Volatility", percent_axis=True, height=280), clear_figure=True)
    weight_df = weights.copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(static_area_chart(weight_df, "Portfolio Weights", height=300), clear_figure=True)
    recent = pd.DataFrame(
        {
            "SOXX": price.reindex(common_idx),
            "Vol": vol.reindex(common_idx),
            "Re-entry": reentry.reindex(common_idx).fillna(False),
            "High-vol Lock": high_vol_lock.reindex(common_idx).fillna(False),
            "Crash": crash.reindex(common_idx).fillna(False),
            "SOXX Weight": weights["SOXX"],
            "SOXL Weight": weights["SOXL"],
            "Cash": (1 - weights.sum(axis=1)).clip(0, 1),
        }
    ).tail(30)
    st.dataframe(recent, use_container_width=True)

with tab_table:
    summary = pd.DataFrame(
        [
            metric_row("Strategy V8", strategy_ret, weights),
            metric_row("SOXX 100%", ret_soxx),
            metric_row("SOXL 100%", ret_soxl),
            metric_row("SOXX 80% + SOXL 20%", 0.8 * ret_soxx + 0.2 * ret_soxl),
            metric_row("SOXX 70% + SOXL 30%", 0.7 * ret_soxx + 0.3 * ret_soxl),
        ]
    )
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg SOXL", "Max SOXL"]:
        summary[col] = summary[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        summary[col] = summary[col].map(lambda x: f"{x:.2f}")
    st.dataframe(summary, use_container_width=True, hide_index=True)

with tab_monthly:
    monthly = metrics["nav"].resample("ME").last().pct_change().dropna()
    pivot_source = monthly.to_frame("Return")
    pivot_source["Year"] = pivot_source.index.year
    pivot_source["Month"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly).groupby(monthly.index.year).prod() - 1
    st.dataframe(pivot.applymap(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
