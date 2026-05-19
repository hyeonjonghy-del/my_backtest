"""QQQ / TQQQ trend and volatility-target backtest.

This app is based on the SOXX/SOXL volatility-target strategy, adapted for
QQQ/TQQQ. It uses QQQ as the signal and volatility asset, and combines QQQ,
TQQQ, and cash through a volatility-target allocation.
"""

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

TRADING_DAYS = 252
QQQ = "QQQ"
TQQQ = "TQQQ"
STATIC_CHART_CONFIG = {"staticPlot": True, "displayModeBar": False, "responsive": True}
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

st.set_page_config(page_title="QQQ/TQQQ Vol Target Backtest", page_icon="US", layout="wide")
st.title("QQQ / TQQQ Volatility Target Backtest")
st.caption(
    "Default: QQQ MA30 > MA200, target volatility 35%, TQQQ cap 45%, "
    "max QQQ-equivalent risk exposure 1.4x, 30% QQQ in bear regimes."
)


def static_line_chart(data: pd.DataFrame, title: str, yaxis_title: str = "", percent_axis: bool = False, height: int = 340, mdd_info: dict[str, object] | None = None) -> go.Figure:
    fig = go.Figure()
    palette = [COLORS["strategy"], COLORS["qqq"], COLORS["tqqq"], COLORS["benchmark"], COLORS["ma_fast"], COLORS["ma_slow"], COLORS["cash"]]
    for i, column in enumerate(data.columns):
        fig.add_trace(go.Scatter(x=data.index, y=data[column], mode="lines", name=str(column), line=dict(color=palette[i % len(palette)], width=2.4 if i == 0 else 1.8)))
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
        fig.add_trace(
            go.Scatter(
                x=[mdd_info["peak_date"], mdd_info["date"]],
                y=[mdd_info["peak_value"], mdd_info["trough_value"]],
                mode="markers+lines",
                name=f"MDD {mdd_info['value']:.1%}",
                line=dict(color=COLORS["dd"], width=2, dash="dot"),
                marker=dict(color=COLORS["dd"], size=8),
            )
        )
        fig.add_annotation(
            x=mdd_info["date"],
            y=mdd_info["trough_value"],
            text=f"MDD {mdd_info['value']:.1%}",
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
    area_colors = {"QQQ": "rgba(37, 99, 235, 0.72)", "TQQQ": "rgba(220, 38, 38, 0.72)", "Cash": "rgba(100, 116, 139, 0.55)"}
    for column in data.columns:
        fig.add_trace(go.Scatter(x=data.index, y=data[column], mode="lines", name=str(column), stackgroup="one", line=dict(width=0.8, color=area_colors.get(column, "rgba(15, 118, 110, 0.65)")), fillcolor=area_colors.get(column, "rgba(15, 118, 110, 0.65)")))
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
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    df = pd.DataFrame({"open": quote["open"], "high": quote["high"], "low": quote["low"], "close": quote["close"], "adjclose": adjclose, "volume": quote["volume"]}, index=index)
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
    return {"nav": nav, "dd": dd, "mdd_date": mdd_date, "mdd_peak_date": peak_date, "mdd_peak_value": peak_value, "mdd_trough_value": nav.loc[mdd_date], "total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win_m": win_m}


def metric_row(name: str, daily_ret: pd.Series, qqq_w: pd.Series | None = None, tqqq_w: pd.Series | None = None) -> dict[str, object]:
    metrics = calc_metrics(daily_ret)
    return {"Strategy": name, "Total": metrics["total"], "CAGR": metrics["cagr"], "MDD": metrics["mdd"], "Sharpe": metrics["sharpe"], "Calmar": metrics["calmar"], "Monthly Win": metrics["win_m"], "Avg QQQ": np.nan if qqq_w is None else qqq_w.mean(), "Avg TQQQ": np.nan if tqqq_w is None else tqqq_w.mean(), "Max TQQQ": np.nan if tqqq_w is None else tqqq_w.max()}


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


def build_strategy_weights(price: pd.Series, trend_signal: pd.Series, vol: pd.Series, target_vol: float, tqqq_cap: float, max_risk_exposure: float, allocation_mode: str, bear_qqq: float, rebalance: str) -> pd.DataFrame:
    signal = trend_signal.shift(1).fillna(False)
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (target_vol / vol_lag).clip(0, max_risk_exposure).fillna(0.0)
    weights = pd.DataFrame(0.0, index=price.index, columns=["QQQ", "TQQQ"])
    if allocation_mode == "Risk-adjusted":
        tqqq_w = (desired_risk / 3).clip(0, tqqq_cap)
        qqq_w = (desired_risk - tqqq_w * 3).clip(0, 1 - tqqq_w)
    elif allocation_mode == "Capital-first":
        capital = desired_risk.clip(0, 1)
        tqqq_w = capital.clip(0, tqqq_cap)
        qqq_w = (capital - tqqq_w).clip(0, 1 - tqqq_w)
    else:
        tqqq_w = pd.Series(tqqq_cap, index=price.index)
        qqq_w = pd.Series(1 - tqqq_cap, index=price.index)
    weights["TQQQ"] = np.where(signal, tqqq_w, 0.0)
    weights["QQQ"] = np.where(signal, qqq_w, bear_qqq)
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)


def calc_target_weight(is_bull: bool, current_vol: float, target_vol: float, tqqq_cap: float, max_risk_exposure: float, allocation_mode: str, bear_qqq: float) -> pd.Series:
    if not is_bull or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({"QQQ": bear_qqq, "TQQQ": 0.0})
    desired_risk = min(target_vol / current_vol, max_risk_exposure)
    if allocation_mode == "Risk-adjusted":
        tqqq_w = min(desired_risk / 3, tqqq_cap)
        qqq_w = min(max(desired_risk - tqqq_w * 3, 0.0), 1 - tqqq_w)
    elif allocation_mode == "Capital-first":
        capital = min(desired_risk, 1.0)
        tqqq_w = min(capital, tqqq_cap)
        qqq_w = min(max(capital - tqqq_w, 0.0), 1 - tqqq_w)
    else:
        tqqq_w = tqqq_cap
        qqq_w = 1 - tqqq_cap
    target = pd.Series({"QQQ": qqq_w, "TQQQ": tqqq_w}).clip(0, 1)
    if target.sum() > 1:
        target = target / target.sum()
    return target


def backtest(weights: pd.DataFrame, ret_qqq: pd.Series, ret_tqqq: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    daily_ret = weights["QQQ"] * ret_qqq + weights["TQQQ"] * ret_tqqq - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_execution_plan(target_weights: pd.Series, prices: pd.Series, account_value: float, current_shares: pd.Series, current_cash: float) -> tuple[pd.DataFrame, float]:
    current_values = current_shares * prices
    effective_value = account_value if account_value > 0 else current_values.sum() + current_cash
    rows = []
    for symbol in ["QQQ", "TQQQ"]:
        target_value = effective_value * target_weights[symbol]
        target_shares = np.floor(target_value / prices[symbol]) if prices[symbol] > 0 else 0
        order_shares = target_shares - current_shares[symbol]
        rows.append({"Symbol": symbol, "Latest Price": prices[symbol], "Target Weight": target_weights[symbol], "Target Value": target_value, "Target Shares": target_shares, "Current Shares": current_shares[symbol], "Order": "Buy" if order_shares > 0 else "Sell" if order_shares < 0 else "Hold", "Order Shares": order_shares, "Estimated Order Value": abs(order_shares) * prices[symbol]})
    target_cash = effective_value * max(0.0, 1 - target_weights.sum())
    return pd.DataFrame(rows), target_cash


with st.sidebar:
    st.header("Settings")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start", datetime(2011, 1, 1))
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
    max_risk_exposure = st.slider("Max risk exposure", 0.5, 2.0, 1.4, 0.1)
    allocation_mode = st.selectbox("Allocation mode", ["Risk-adjusted", "Capital-first", "Fixed bull weights"], index=0)
    st.subheader("Bear / Trading")
    bear_qqq = st.slider("Bear-regime QQQ weight (%)", 0, 100, 30, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=0, horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.10, step=0.01) / 100
    st.subheader("Execution")
    account_value = st.number_input("Account value ($)", min_value=0.0, value=10000.0, step=1000.0)
    current_qqq_shares = st.number_input("Current QQQ shares", min_value=0.0, value=0.0, step=1.0)
    current_tqqq_shares = st.number_input("Current TQQQ shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash ($)", min_value=0.0, value=10000.0, step=1000.0)
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

with st.expander("Default Strategy", expanded=False):
    st.markdown(f"""
| Item | Value |
|---|---|
| Bull regime | QQQ MA{fast_window} > MA{slow_window} |
| Target volatility | {target_vol:.0%} |
| TQQQ cap | {tqqq_cap:.0%} |
| Max risk exposure | {max_risk_exposure:.1f}x QQQ-equivalent risk |
| Allocation | Risk-adjusted: TQQQ uses about 3x QQQ risk budget |
| Bear regime | Cash {1 - bear_qqq:.0%} + QQQ {bear_qqq:.0%} |
""")

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
weights_full = build_strategy_weights(price, trend_signal, vol, target_vol, tqqq_cap, max_risk_exposure, allocation_mode, bear_qqq, rebalance)
weights = weights_full.reindex(common_idx).fillna(0.0)
ret_qqq = ret_qqq_full.reindex(common_idx).fillna(0.0)
ret_tqqq = ret_tqqq_full.reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_qqq, ret_tqqq, cost_rate)
bench_qqq = ret_qqq
bench_tqqq = ret_tqqq
fixed_20 = 0.8 * ret_qqq + 0.2 * ret_tqqq
fixed_30 = 0.7 * ret_qqq + 0.3 * ret_tqqq
strategy_metrics = calc_metrics(strategy_ret)
summary = pd.DataFrame([metric_row("Strategy", strategy_ret, weights["QQQ"], weights["TQQQ"]), metric_row("QQQ 100%", bench_qqq), metric_row("TQQQ 100%", bench_tqqq), metric_row("QQQ 80% + TQQQ 20%", fixed_20), metric_row("QQQ 70% + TQQQ 30%", fixed_30)])
progress.progress(100, text="Done")
progress.empty()
latest_date = weights.index[-1].date()
latest_trend = bool(trend_signal.reindex(weights.index).ffill().iloc[-1])
latest_vol = vol.reindex(weights.index).ffill().iloc[-1]
next_target = calc_target_weight(latest_trend, latest_vol, target_vol, tqqq_cap, max_risk_exposure, allocation_mode, bear_qqq)
latest_prices = pd.Series({"QQQ": qqq["adjclose"].reindex(weights.index).ffill().iloc[-1], "TQQQ": tqqq["adjclose"].reindex(weights.index).ffill().iloc[-1]})
current_shares = pd.Series({"QQQ": current_qqq_shares, "TQQQ": current_tqqq_shares})
execution_plan, target_cash = build_execution_plan(next_target, latest_prices, account_value, current_shares, current_cash)
action_label = position_action_label(execution_plan["Order Shares"].abs().sum(), tolerance=0.5)
st.success(f"{action_label} | Next-open target from close signal ({latest_date}): {'Bull' if latest_trend else 'Bear'} | QQQ {next_target['QQQ']:.1%}, TQQQ {next_target['TQQQ']:.1%}, Cash {1 - next_target.sum():.1%} | QQQ {vol_window}D volatility {latest_vol:.1%}")
cols = st.columns(6)
cols[0].metric("Total", f"{strategy_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")
tab_perf, tab_execute, tab_signal, tab_table, tab_monthly = st.tabs(["Performance", "Execution", "Signal / Weights", "Comparison", "Monthly"])
with tab_perf:
    nav_df = pd.DataFrame({"Strategy": strategy_metrics["nav"], "QQQ": calc_metrics(bench_qqq)["nav"], "TQQQ": calc_metrics(bench_tqqq)["nav"], "80/20": calc_metrics(fixed_20)["nav"]})
    st.pyplot(static_line_chart(nav_df, "Cumulative NAV with Strategy MDD", yaxis_title="NAV", height=380, mdd_info={"date": strategy_metrics["mdd_date"], "peak_date": strategy_metrics["mdd_peak_date"], "value": strategy_metrics["mdd"], "peak_value": strategy_metrics["mdd_peak_value"], "trough_value": strategy_metrics["mdd_trough_value"]}), clear_figure=True)
    dd_df = pd.DataFrame({"Strategy DD": strategy_metrics["dd"], "QQQ DD": calc_metrics(bench_qqq)["dd"], "TQQQ DD": calc_metrics(bench_tqqq)["dd"]}) * 100
    st.pyplot(static_line_chart(dd_df, f"Drawdown | Strategy MDD {strategy_metrics['mdd']:.1%}", yaxis_title="Drawdown", percent_axis=True, height=280), clear_figure=True)
with tab_execute:
    st.subheader("Next Trade Plan")
    st.caption("Signal uses the latest close. Backtest returns assume rebalancing at the next regular-session open. The table uses the latest adjusted close only as a sizing estimate because the next open is not known yet.")
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
    st.info("Practical rule: after the signal date closes, prepare these orders for the next regular-session open. Buy positive Order Shares, sell negative Order Shares, and re-run after fills if the opening price differs a lot.")
with tab_signal:
    signal_df = pd.DataFrame({"QQQ": price.reindex(common_idx), f"MA{fast_window}": fast_ma.reindex(common_idx), f"MA{slow_window}": slow_ma.reindex(common_idx)})
    st.pyplot(static_line_chart(signal_df, "QQQ Trend", yaxis_title="Price", height=320), clear_figure=True)
    weight_df = weights.copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(static_area_chart(weight_df, "Portfolio Weights", height=300), clear_figure=True)
    st.subheader("Recent Signals")
    recent = pd.DataFrame({"QQQ": price.reindex(common_idx), f"MA{fast_window}": fast_ma.reindex(common_idx), f"MA{slow_window}": slow_ma.reindex(common_idx), f"Vol{vol_window}": vol.reindex(common_idx), "QQQ Weight": weights["QQQ"], "TQQQ Weight": weights["TQQQ"]}).tail(30)
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
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
