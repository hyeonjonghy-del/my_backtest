"""SOXX-only trend and volatility-target backtest."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from kiwoom_account import (
    KIWOOM_SOURCE,
    render_account_controls,
    render_account_summary,
)


TRADING_DAYS = 252
SOXX = "SOXX"
BIL = "BIL"
COLORS = {
    "strategy": "#0F766E",
    "soxx": "#2563EB",
    "bil": "#64748B",
    "benchmark": "#7C3AED",
    "fast": "#F59E0B",
    "slow": "#111827",
    "vol": "#DC2626",
    "dd": "#B91C1C",
}


st.set_page_config(page_title="SOXX Volatility Target Strategy", page_icon="US", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("SOXX Volatility Target Strategy")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)
st.caption(
    "SOXX and short-term Treasury bills only: participate in long-term semiconductor growth "
    "while reducing exposure when trend weakens or volatility rises."
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
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    frame = pd.DataFrame(
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
    return normalize_index(frame).dropna(subset=["adjclose"])


def adjusted_open(frame: pd.DataFrame) -> pd.Series:
    factor = (frame["adjclose"] / frame["close"]).replace([np.inf, -np.inf], np.nan).ffill()
    return (frame["open"] * factor).replace([np.inf, -np.inf], np.nan).ffill()


def calc_metrics(daily_ret: pd.Series) -> dict[str, object]:
    ret = daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nav = (1 + ret).cumprod()
    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    drawdown = nav / nav.cummax() - 1
    mdd = drawdown.min()
    mdd_date = drawdown.idxmin()
    peak_date = nav.loc[:mdd_date].idxmax()
    std = ret.std()
    sharpe = ret.mean() / std * np.sqrt(TRADING_DAYS) if std > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    monthly_win = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return {
        "nav": nav,
        "drawdown": drawdown,
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "monthly_win": monthly_win,
        "volatility": std * np.sqrt(TRADING_DAYS),
        "mdd_date": mdd_date,
        "peak_date": peak_date,
        "peak_value": nav.loc[peak_date],
        "trough_value": nav.loc[mdd_date],
    }


def metric_row(name: str, ret: pd.Series, soxx_weight: pd.Series | None = None) -> dict[str, object]:
    metrics = calc_metrics(ret)
    return {
        "Strategy": name,
        "Total": metrics["total"],
        "CAGR": metrics["cagr"],
        "MDD": metrics["mdd"],
        "Volatility": metrics["volatility"],
        "Sharpe": metrics["sharpe"],
        "Calmar": metrics["calmar"],
        "Monthly Win": metrics["monthly_win"],
        "Avg SOXX": np.nan if soxx_weight is None else soxx_weight.mean(),
    }


def build_weights(
    price: pd.Series,
    close_ret: pd.Series,
    fast_window: int,
    slow_window: int,
    vol_window: int,
    target_vol: float,
    bear_soxx: float,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series]:
    fast_ma = price.rolling(fast_window).mean()
    slow_ma = price.rolling(slow_window).mean()
    realized_vol = close_ret.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    close_bull = fast_ma > slow_ma
    execution_bull = close_bull.shift(1).fillna(False)
    execution_vol = realized_vol.shift(1).replace(0, np.nan)
    bull_weight = (target_vol / execution_vol).clip(0, 1).fillna(bear_soxx)
    soxx_weight = pd.Series(np.where(execution_bull, bull_weight, bear_soxx), index=price.index)
    weights = pd.DataFrame({"SOXX": soxx_weight, "BIL": 1 - soxx_weight}, index=price.index)
    return weights, close_bull, realized_vol, fast_ma, slow_ma


def backtest_open_execution_close_valuation(
    target_weights: pd.DataFrame,
    open_prices: pd.DataFrame,
    close_prices: pd.DataFrame,
    cost_rate: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Trade at each open, carry holdings, and mark portfolio NAV at each close."""
    units = pd.Series(0.0, index=target_weights.columns)
    cash = 1.0
    previous_close_nav = 1.0
    daily_ret = pd.Series(0.0, index=target_weights.index, name="Strategy")
    turnover = pd.Series(0.0, index=target_weights.index, name="Turnover")
    close_nav = pd.Series(0.0, index=target_weights.index, name="Close NAV")

    for number, date in enumerate(target_weights.index):
        open_px = open_prices.loc[date]
        nav_at_open = float(cash + (units * open_px).sum())
        current_values = units * open_px
        current_weights = current_values / nav_at_open if nav_at_open > 0 else current_values * 0
        desired = target_weights.loc[date].clip(0, 1)
        traded_fraction = float(desired.sum()) if number == 0 else float((desired - current_weights).abs().sum() / 2)
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


def fixed_mix_return(soxx_ret: pd.Series, bil_ret: pd.Series, soxx_weight: float) -> pd.Series:
    return soxx_weight * soxx_ret + (1 - soxx_weight) * bil_ret


def line_chart(data: pd.DataFrame, title: str, yaxis: str = "", percent: bool = False, mdd: dict | None = None):
    fig = go.Figure()
    palette = [COLORS["strategy"], COLORS["soxx"], COLORS["benchmark"], COLORS["bil"]]
    for i, column in enumerate(data.columns):
        fig.add_trace(go.Scatter(x=data.index, y=data[column], mode="lines", name=str(column),
                                 line=dict(color=palette[i % len(palette)], width=2.4 if i == 0 else 1.7)))
    if mdd:
        fig.add_trace(go.Scatter(
            x=[mdd["peak_date"], mdd["mdd_date"]],
            y=[mdd["peak_value"], mdd["trough_value"]],
            mode="markers+lines", name=f"MDD {mdd['mdd']:.1%}",
            line=dict(color=COLORS["dd"], dash="dot"), marker=dict(color=COLORS["dd"], size=8),
        ))
    fig.update_layout(title=title, height=390, hovermode="x unified", plot_bgcolor="white",
                      paper_bgcolor="white", margin=dict(l=10, r=10, t=48, b=20),
                      legend=dict(orientation="h", y=1.08),
                      xaxis=dict(showgrid=False, hoverformat="%Y-%m-%d",
                                 showspikes=True, spikemode="across", spikesnap="cursor",
                                 spikedash="dot", spikethickness=1),
                      yaxis=dict(title=yaxis, showgrid=True, gridcolor="#E5E7EB",
                                 tickformat=".1%" if percent else None))
    return fig


def area_chart(weights: pd.DataFrame):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=weights.index, y=weights["SOXX"], name="SOXX", stackgroup="one",
                             line=dict(width=0.7, color=COLORS["soxx"])))
    fig.add_trace(go.Scatter(x=weights.index, y=weights["BIL"], name="BIL / Cash", stackgroup="one",
                             line=dict(width=0.7, color=COLORS["bil"])))
    fig.update_layout(title="Portfolio Weights", height=330, hovermode="x unified", plot_bgcolor="white",
                      paper_bgcolor="white", margin=dict(l=10, r=10, t=48, b=20),
                      legend=dict(orientation="h", y=1.08),
                      xaxis=dict(hoverformat="%Y-%m-%d", showspikes=True, spikemode="across",
                                 spikesnap="cursor", spikedash="dot", spikethickness=1),
                      yaxis=dict(tickformat=".0%", range=[0, 1]))
    return fig


def signal_dashboard(
    weights: pd.DataFrame,
    signal_frame: pd.DataFrame,
    vol_frame: pd.DataFrame,
):
    """Build linked signal charts so one hover date is shared by all three panels."""
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.28, 0.42, 0.30],
        subplot_titles=("Portfolio Weights", "SOXX Price and Trend Filter", "Realized Volatility"),
    )

    fig.add_trace(
        go.Scatter(
            x=weights.index,
            y=weights["SOXX"],
            name="SOXX Weight",
            stackgroup="weights",
            line=dict(width=0.7, color=COLORS["soxx"]),
            hovertemplate="%{x|%Y-%m-%d}<br>SOXX: %{y:.1%}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=weights.index,
            y=weights["BIL"],
            name="BIL / Cash Weight",
            stackgroup="weights",
            line=dict(width=0.7, color=COLORS["bil"]),
            hovertemplate="%{x|%Y-%m-%d}<br>BIL / Cash: %{y:.1%}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    signal_colors = [COLORS["strategy"], COLORS["fast"], COLORS["slow"]]
    for column, color in zip(signal_frame.columns, signal_colors):
        fig.add_trace(
            go.Scatter(
                x=signal_frame.index,
                y=signal_frame[column],
                mode="lines",
                name=str(column),
                line=dict(color=color, width=2.1 if column == "SOXX" else 1.6),
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>{column}: %{{y:,.2f}}<extra></extra>",
            ),
            row=2,
            col=1,
        )

    for column, color in zip(vol_frame.columns, [COLORS["vol"], COLORS["soxx"]]):
        fig.add_trace(
            go.Scatter(
                x=vol_frame.index,
                y=vol_frame[column],
                mode="lines",
                name=str(column),
                line=dict(color=color, width=2.0 if column != "Target Volatility" else 1.5),
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>{column}: %{{y:.1%}}<extra></extra>",
            ),
            row=3,
            col=1,
        )

    fig.update_layout(
        height=940,
        hovermode="x unified",
        hoversubplots="axis",
        hoverdistance=100,
        spikedistance=-1,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=10, r=10, t=70, b=30),
        legend=dict(orientation="h", y=1.04),
    )
    fig.update_xaxes(
        hoverformat="%Y-%m-%d",
        showgrid=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
        spikethickness=1,
    )
    fig.update_yaxes(showgrid=True, gridcolor="#E5E7EB")
    fig.update_yaxes(tickformat=".0%", range=[0, 1], row=1, col=1)
    fig.update_yaxes(title_text="Adjusted price", row=2, col=1)
    fig.update_yaxes(tickformat=".1%", row=3, col=1)
    return fig


def yearly_returns(ret: pd.Series) -> pd.Series:
    return (1 + ret).resample("YE").prod() - 1


def monthly_table(ret: pd.Series) -> pd.DataFrame:
    monthly = (1 + ret).resample("ME").prod() - 1
    frame = monthly.to_frame("return")
    frame["Year"] = frame.index.year
    frame["Month"] = frame.index.month
    table = frame.pivot(index="Year", columns="Month", values="return")
    table.columns = [datetime(2000, int(month), 1).strftime("%b") for month in table.columns]
    table["Annual"] = (1 + table).prod(axis=1, skipna=True) - 1
    return table


with st.sidebar:
    st.header("Settings")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start", datetime.today() - timedelta(days=3653))
    with col2:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Trend Filter")
    fast_window = st.slider("Fast MA", 10, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Target")
    vol_window = st.slider("Volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 60, 40, 5) / 100
    bear_soxx = st.slider("Bear-regime SOXX weight (%)", 0, 100, 40, 5) / 100
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.10, step=0.01) / 100

    st.subheader("Execution")
    account_state = render_account_controls(
        (SOXX, BIL),
        "soxx_bil",
        preferred_profile="soxx",
    )
    execution_source = account_state["source"]
    kiwoom_snapshot = account_state["snapshot"]
    current_soxx_shares = float(account_state["shares"][SOXX])
    current_bil_shares = float(account_state["shares"][BIL])
    current_cash = float(account_state["cash"])
    account_value = float(account_state["account_value"])


with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| Item | Value |
|---|---|
| Assets | SOXX + BIL only |
| Bull regime | SOXX MA{fast_window} > MA{slow_window} |
| Volatility window | {vol_window} trading days |
| Target volatility | {target_vol:.0%} |
| Bull allocation | min(100%, target volatility / SOXX volatility) |
| Bear allocation | SOXX {bear_soxx:.0%} + BIL {1 - bear_soxx:.0%} |
| Signal execution | Close signal, next open |
| Rebalance | Daily |
| One-way cost | {cost_rate:.2%} |
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
if fast_window >= slow_window:
    st.error("Fast MA must be shorter than Slow MA.")
    st.stop()


progress = st.progress(0, text="Loading SOXX and BIL data...")
try:
    warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=max(slow_window, vol_window) * 3)
    end_dt = datetime.combine(end_date, datetime.min.time())
    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)
    bil = load_yahoo_chart(BIL, warmup_start, end_dt)
except Exception as exc:
    st.error(f"Could not load Yahoo Finance data: {exc}")
    st.stop()

common_idx = soxx.index.intersection(bil.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 200:
    st.error("Not enough data for the selected period.")
    st.stop()

full_idx = common_idx.union(soxx.index[soxx.index < common_idx[0]])
soxx = soxx.reindex(full_idx).sort_index()
bil = bil.reindex(full_idx).sort_index()
price = soxx["adjclose"].ffill()
close_ret = price.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
soxx_open = adjusted_open(soxx)
bil_open = adjusted_open(bil)
ret_soxx_full = soxx["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_bil_full = bil["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)

weights_full, close_bull, realized_vol, fast_ma, slow_ma = build_weights(
    price, close_ret, fast_window, slow_window, vol_window, target_vol, bear_soxx
)
weights = weights_full.reindex(common_idx).ffill().fillna({"SOXX": bear_soxx, "BIL": 1 - bear_soxx})
ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)
ret_bil = ret_bil_full.reindex(common_idx).fillna(0.0)
open_prices = pd.DataFrame({"SOXX": soxx_open, "BIL": bil_open}).reindex(common_idx).ffill()
close_prices = pd.DataFrame({"SOXX": soxx["adjclose"], "BIL": bil["adjclose"]}).reindex(common_idx).ffill()
strategy_ret, turnover, close_nav = backtest_open_execution_close_valuation(
    weights, open_prices, close_prices, cost_rate
)

bench_soxx = ret_soxx
fixed_80 = fixed_mix_return(ret_soxx, ret_bil, 0.8)
fixed_60 = fixed_mix_return(ret_soxx, ret_bil, 0.6)
metrics = calc_metrics(strategy_ret)
summary = pd.DataFrame([
    metric_row("Strategy", strategy_ret, weights["SOXX"]),
    metric_row("SOXX 100%", bench_soxx),
    metric_row("SOXX 80% + BIL 20%", fixed_80),
    metric_row("SOXX 60% + BIL 40%", fixed_60),
])

latest_date = common_idx[-1]
latest_price = price.reindex(common_idx).ffill().iloc[-1]
latest_bil_price = bil["adjclose"].reindex(common_idx).ffill().iloc[-1]
latest_vol = realized_vol.reindex(common_idx).ffill().iloc[-1]
latest_bull = bool(close_bull.reindex(common_idx).fillna(False).iloc[-1])
next_soxx_weight = min(target_vol / latest_vol, 1.0) if latest_bull and latest_vol > 0 else bear_soxx
next_weights = pd.Series({"SOXX": next_soxx_weight, "BIL": 1 - next_soxx_weight})
regime = "Bull" if latest_bull else "Bear"

progress.progress(100, text="Done")
progress.empty()

st.success(
    f"Position and allocation change | Today's target for next open from close signal ({latest_date.date()}): "
    f"{regime} | SOXX {next_weights['SOXX']:.1%}, BIL/Cash {next_weights['BIL']:.1%} | "
    f"SOXX {vol_window}D volatility {latest_vol:.1%}"
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total", f"{metrics['total']:.1%}")
c2.metric("CAGR", f"{metrics['cagr']:.1%}")
c3.metric("MDD", f"{metrics['mdd']:.1%}")
c4.metric("Sharpe", f"{metrics['sharpe']:.2f}")
c5.metric("Calmar", f"{metrics['calmar']:.2f}")
c6.metric("Monthly Win", f"{metrics['monthly_win']:.1%}")

tab_performance, tab_execution, tab_signal, tab_comparison, tab_monthly = st.tabs(
    ["Performance", "Execution", "Signal / Weights", "Comparison", "Monthly"]
)

with tab_performance:
    nav_data = pd.DataFrame({
        "Strategy": metrics["nav"],
        "SOXX": calc_metrics(bench_soxx)["nav"],
        "80/20": calc_metrics(fixed_80)["nav"],
    })
    st.plotly_chart(line_chart(nav_data, "Cumulative NAV with Strategy MDD", "NAV", mdd=metrics), use_container_width=True)
    dd_data = pd.DataFrame({
        "Strategy": metrics["drawdown"],
        "SOXX": calc_metrics(bench_soxx)["drawdown"],
    })
    st.plotly_chart(line_chart(dd_data, "Drawdown", percent=True), use_container_width=True)

    performance_yearly = pd.DataFrame({
        "Strategy": yearly_returns(strategy_ret),
        "SOXX": yearly_returns(bench_soxx),
    })
    performance_yearly_fig = go.Figure()
    for column, color in zip(performance_yearly.columns, [COLORS["strategy"], COLORS["soxx"]]):
        performance_yearly_fig.add_trace(
            go.Bar(
                x=performance_yearly.index.year,
                y=performance_yearly[column],
                name=column,
                marker_color=color,
                hovertemplate=f"Year %{{x}}<br>{column}: %{{y:.1%}}<extra></extra>",
            )
        )
    performance_yearly_fig.update_layout(
        title="Yearly Returns: Strategy vs SOXX",
        barmode="group",
        height=380,
        hovermode="x unified",
        yaxis=dict(tickformat=".0%"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", y=1.08),
    )
    st.plotly_chart(performance_yearly_fig, use_container_width=True)
    st.plotly_chart(area_chart(weights), use_container_width=True)

with tab_execution:
    if execution_source == KIWOOM_SOURCE:
        effective_value = current_soxx_shares * latest_price + current_bil_shares * latest_bil_price + current_cash
        account_value = float(effective_value)
    else:
        effective_value = account_value
    render_account_summary(account_state, effective_value)
    st.subheader("Next Trade Plan")
    if effective_value <= 0:
        effective_value = current_soxx_shares * latest_price + current_bil_shares * latest_bil_price + current_cash
    execution_rows = []
    for symbol, px, current_shares in [
        ("SOXX", latest_price, current_soxx_shares),
        ("BIL", latest_bil_price, current_bil_shares),
    ]:
        target_value = effective_value * next_weights[symbol]
        target_shares = np.floor(target_value / px) if px > 0 else 0
        order_shares = target_shares - current_shares
        execution_rows.append({
            "Symbol": symbol,
            "Latest Price": px,
            "Target Weight": next_weights[symbol],
            "Target Value": target_value,
            "Target Shares": target_shares,
            "Current Shares": current_shares,
            "Order": "Buy" if order_shares > 0 else "Sell" if order_shares < 0 else "Hold",
            "Order Shares": order_shares,
            "Estimated Order Value": abs(order_shares) * px,
        })
    execution = pd.DataFrame(execution_rows)
    st.dataframe(execution.style.format({"Latest Price": "${:,.2f}", "Target Weight": "{:.1%}",
                                         "Target Value": "${:,.0f}", "Estimated Order Value": "${:,.0f}"}),
                 use_container_width=True, hide_index=True)
    invested = sum(row["Target Shares"] * row["Latest Price"] for row in execution_rows)
    st.metric("Estimated residual cash after whole-share orders", f"${max(effective_value - invested, 0):,.2f}")

with tab_signal:
    signal_frame = pd.DataFrame({
        "SOXX": price.reindex(common_idx),
        f"MA{fast_window}": fast_ma.reindex(common_idx),
        f"MA{slow_window}": slow_ma.reindex(common_idx),
    })
    vol_frame = pd.DataFrame({
        f"SOXX {vol_window}D Volatility": realized_vol.reindex(common_idx),
        "Target Volatility": target_vol,
    })
    st.plotly_chart(
        signal_dashboard(weights, signal_frame, vol_frame),
        use_container_width=True,
    )
    st.caption(
        "Move the cursor over any panel to inspect the same YYYY-MM-DD date "
        "across portfolio weights, trend, and volatility."
    )
    st.dataframe(pd.DataFrame({
        "Date": common_idx,
        "Regime": np.where(close_bull.reindex(common_idx).fillna(False), "Bull", "Bear"),
        "SOXX Weight": weights["SOXX"],
        "BIL Weight": weights["BIL"],
        "SOXX Volatility": realized_vol.reindex(common_idx),
        "Daily Turnover": turnover,
    }).tail(120).style.format({"SOXX Weight": "{:.1%}", "BIL Weight": "{:.1%}",
                               "SOXX Volatility": "{:.1%}", "Daily Turnover": "{:.2%}"}),
                 use_container_width=True, hide_index=True)

with tab_comparison:
    formatted = summary.copy()
    st.dataframe(formatted.style.format({
        "Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}", "Volatility": "{:.1%}",
        "Sharpe": "{:.2f}", "Calmar": "{:.2f}", "Monthly Win": "{:.1%}", "Avg SOXX": "{:.1%}",
    }), use_container_width=True, hide_index=True)
    yearly = pd.DataFrame({
        "Strategy": yearly_returns(strategy_ret),
        "SOXX": yearly_returns(bench_soxx),
        "80/20": yearly_returns(fixed_80),
        "60/40": yearly_returns(fixed_60),
    })
    fig = go.Figure()
    for column, color in zip(yearly.columns, [COLORS["strategy"], COLORS["soxx"], COLORS["benchmark"], COLORS["bil"]]):
        fig.add_trace(go.Bar(x=yearly.index.year, y=yearly[column], name=column, marker_color=color))
    fig.update_layout(title="Yearly Returns", barmode="group", height=380, yaxis=dict(tickformat=".0%"),
                      plot_bgcolor="white", paper_bgcolor="white", legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

with tab_monthly:
    strategy_monthly = monthly_table(strategy_ret)
    st.subheader("Strategy Monthly Returns")
    monthly_style = (
        strategy_monthly.style
        .format("{:.1%}")
        .background_gradient(cmap="RdYlGn", axis=None)
        .set_properties(subset=["Annual"], **{"font-weight": "bold", "border-left": "2px solid #9CA3AF"})
    )
    st.dataframe(monthly_style, use_container_width=True)
    daily_export = pd.DataFrame({
        "SOXX_return": ret_soxx,
        "BIL_return": ret_bil,
        "strategy_return": strategy_ret,
        "strategy_NAV": metrics["nav"],
        "strategy_drawdown": metrics["drawdown"],
        "SOXX_weight": weights["SOXX"],
        "BIL_weight": weights["BIL"],
        "SOXX_volatility": realized_vol.reindex(common_idx),
        "turnover": turnover,
    })
    d1, d2 = st.columns(2)
    d1.download_button("Download daily backtest CSV", daily_export.to_csv(index_label="date").encode("utf-8-sig"),
                       file_name="soxx_vol_target_daily.csv", mime="text/csv", use_container_width=True)
    d2.download_button("Download summary CSV", summary.to_csv(index=False).encode("utf-8-sig"),
                       file_name="soxx_vol_target_summary.csv", mime="text/csv", use_container_width=True)

