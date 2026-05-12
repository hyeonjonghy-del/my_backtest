"""SOXX / SOXL trend and volatility-target backtest."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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

st.set_page_config(page_title="SOXX/SOXL Vol Target Backtest", page_icon="US", layout="wide")
st.title("SOXX / SOXL Volatility Target Backtest")
st.caption("Default: SOXX MA50 > MA200, target volatility 45%, SOXL cap 40%, cash in bear regimes")


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


def build_strategy_weights(
    price: pd.Series,
    trend_signal: pd.Series,
    vol: pd.Series,
    target_vol: float,
    soxl_cap: float,
    max_risk_exposure: float,
    allocation_mode: str,
    bear_soxx: float,
    rebalance: str,
) -> pd.DataFrame:
    signal = trend_signal.shift(1).fillna(False)
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (target_vol / vol_lag).clip(0, max_risk_exposure).fillna(0.0)

    weights = pd.DataFrame(0.0, index=price.index, columns=["SOXX", "SOXL"])
    if allocation_mode == "Risk-adjusted":
        soxl_w = (desired_risk / 3).clip(0, soxl_cap)
        soxx_w = (desired_risk - soxl_w * 3).clip(0, 1 - soxl_w)
    elif allocation_mode == "Capital-first":
        capital = desired_risk.clip(0, 1)
        soxl_w = capital.clip(0, soxl_cap)
        soxx_w = (capital - soxl_w).clip(0, 1 - soxl_w)
    else:
        soxl_w = pd.Series(soxl_cap, index=price.index)
        soxx_w = pd.Series(1 - soxl_cap, index=price.index)

    weights["SOXL"] = np.where(signal, soxl_w, 0.0)
    weights["SOXX"] = np.where(signal, soxx_w, bear_soxx)
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)


def backtest(weights: pd.DataFrame, ret_soxx: pd.Series, ret_soxl: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    daily_ret = weights["SOXX"] * ret_soxx + weights["SOXL"] * ret_soxl - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


with st.sidebar:
    st.header("Settings")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start", datetime(2016, 5, 12))
    with col2:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Trend Filter")
    trend_rule = st.selectbox("Rule", ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow + MA Fast > MA Slow"], index=0)
    fast_window = st.slider("Fast MA", 20, 100, 50, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Target")
    vol_window = st.slider("Volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 80, 45, 5) / 100
    soxl_cap = st.slider("SOXL max weight (%)", 0, 80, 40, 5) / 100
    max_risk_exposure = st.slider("Max risk exposure", 0.5, 2.0, 1.2, 0.1)
    allocation_mode = st.selectbox("Allocation mode", ["Risk-adjusted", "Capital-first", "Fixed bull weights"], index=0)

    st.subheader("Bear / Trading")
    bear_soxx = st.slider("Bear-regime SOXX weight (%)", 0, 100, 0, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=0, horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.05, step=0.01) / 100
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| Item | Value |
|---|---|
| Bull regime | SOXX MA{fast_window} > MA{slow_window} |
| Target volatility | {target_vol:.0%} |
| SOXL cap | {soxl_cap:.0%} |
| Allocation | Risk-adjusted: SOXL uses about 3x SOXX risk budget |
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
ret_soxx_full = soxx["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
ret_soxl_full = soxl["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)

fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
vol = ret_soxx_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
trend_signal = build_trend_signal(price, fast_ma, slow_ma, trend_rule)

weights_full = build_strategy_weights(
    price,
    trend_signal,
    vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    allocation_mode,
    bear_soxx,
    rebalance,
)

weights = weights_full.reindex(common_idx).fillna(0.0)
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
latest_trend = bool(trend_signal.reindex(weights.index).ffill().iloc[-1])
latest_vol = vol.reindex(weights.index).ffill().iloc[-1]

st.success(
    f"Current signal ({latest_date}): {'Bull' if latest_trend else 'Bear'} | "
    f"SOXX {latest['SOXX']:.1%}, SOXL {latest['SOXL']:.1%}, Cash {1 - latest.sum():.1%} | "
    f"SOXX {vol_window}D volatility {latest_vol:.1%}"
)

cols = st.columns(6)
cols[0].metric("Total", f"{strategy_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

tab_perf, tab_signal, tab_table, tab_monthly = st.tabs(["Performance", "Signal / Weights", "Comparison", "Monthly"])

with tab_perf:
    nav_df = pd.DataFrame(
        {
            "Strategy": strategy_metrics["nav"],
            "SOXX": calc_metrics(bench_soxx)["nav"],
            "SOXL": calc_metrics(bench_soxl)["nav"],
            "80/20": calc_metrics(fixed_20)["nav"],
        }
    )
    st.plotly_chart(
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
        use_container_width=True,
        config=STATIC_CHART_CONFIG,
    )

    dd_df = pd.DataFrame(
        {
            "Strategy DD": strategy_metrics["dd"],
            "SOXX DD": calc_metrics(bench_soxx)["dd"],
            "SOXL DD": calc_metrics(bench_soxl)["dd"],
        }
    ) * 100
    st.plotly_chart(
        static_line_chart(
            dd_df,
            f"Drawdown | Strategy MDD {strategy_metrics['mdd']:.1%}",
            yaxis_title="Drawdown",
            percent_axis=True,
            height=280,
        ),
        use_container_width=True,
        config=STATIC_CHART_CONFIG,
    )

with tab_signal:
    signal_df = pd.DataFrame(
        {
            "SOXX": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
        }
    )
    st.plotly_chart(
        static_line_chart(signal_df, "SOXX Trend", yaxis_title="Price", height=320),
        use_container_width=True,
        config=STATIC_CHART_CONFIG,
    )

    weight_df = weights.copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.plotly_chart(
        static_area_chart(weight_df, "Portfolio Weights", height=300),
        use_container_width=True,
        config=STATIC_CHART_CONFIG,
    )

    st.subheader("Recent Signals")
    recent = pd.DataFrame(
        {
            "SOXX": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
            f"Vol{vol_window}": vol.reindex(common_idx),
            "SOXX Weight": weights["SOXX"],
            "SOXL Weight": weights["SOXL"],
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
