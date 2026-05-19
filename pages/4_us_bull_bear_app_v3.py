"""SPY / UPRO trend and volatility-target backtest."""

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

SPY = "SPY"
UPRO = "UPRO"
TRADING_DAYS = 252
STATIC_CHART_CONFIG = {
    "staticPlot": True,
    "displayModeBar": False,
    "responsive": True,
}
COLORS = {
    "strategy": "#0F766E",
    "spy": "#2563EB",
    "upro": "#DC2626",
    "cash": "#64748B",
    "ma_fast": "#F59E0B",
    "ma_slow": "#111827",
    "dd": "#B91C1C",
}

st.set_page_config(page_title="SPY / UPRO Vol Target Bull/Bear", page_icon="US", layout="wide")
st.title("SPY / UPRO Bull/Bear Volatility Target Backtest")
st.caption("SPY trend + SPY-volatility target sizing")


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
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
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
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
        COLORS["spy"],
        COLORS["upro"],
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
    area_colors = {
        "SPY": "rgba(37, 99, 235, 0.72)",
        "UPRO": "rgba(220, 38, 38, 0.72)",
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


def build_regime(
    spy_close: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    trend_rule: str,
) -> pd.Series:
    if trend_rule == "MA Fast > MA Slow":
        trend = fast_ma > slow_ma
    elif trend_rule == "Close > MA Slow":
        trend = spy_close > slow_ma
    else:
        trend = (spy_close > slow_ma) & (fast_ma > slow_ma)

    regime = pd.Series("Bear", index=spy_close.index, dtype=object)
    regime.loc[trend.fillna(False)] = "Bull"
    return regime.rename("Regime")


def rebalance_weights(weights: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "Daily":
        return weights

    out = weights.copy() * 0.0
    current = pd.Series({"SPY": 0.0, "UPRO": 0.0})
    last_key = None
    for date, row in weights.iterrows():
        key = date.isocalendar()[:2] if frequency == "Weekly" else (date.year, date.month)
        if key != last_key:
            current = row
            last_key = key
        out.loc[date] = current
    return out


def beta_to_weights(target_beta: float, upro_cap: float) -> tuple[float, float]:
    target_beta = max(float(target_beta), 0.0)
    if target_beta <= 1:
        return min(target_beta, 1.0), 0.0

    upro_weight = min((target_beta - 1) / 2, upro_cap, 1.0)
    spy_weight = min(max(target_beta - 3 * upro_weight, 0.0), 1 - upro_weight)
    if spy_weight + upro_weight > 1:
        scale = 1 / (spy_weight + upro_weight)
        spy_weight *= scale
        upro_weight *= scale
    return spy_weight, upro_weight


def build_strategy_weights(
    price: pd.Series,
    regime: pd.Series,
    vol: pd.Series,
    target_vol: float,
    upro_cap: float,
    max_beta: float,
    bear_spy: float,
    rebalance: str,
) -> pd.DataFrame:
    executable_regime = regime.shift(1).reindex(price.index).ffill().fillna("Bear")
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_beta = (target_vol / vol_lag).clip(0, max_beta).fillna(0.0)

    rows = []
    for date in price.index:
        state = executable_regime.loc[date]
        if state == "Bear":
            spy_weight, upro_weight = bear_spy, 0.0
        else:
            spy_weight, upro_weight = beta_to_weights(desired_beta.loc[date], upro_cap)
        rows.append({"SPY": spy_weight, "UPRO": upro_weight, "Regime": state})

    weights = pd.DataFrame(rows, index=price.index)
    numeric = rebalance_weights(weights[["SPY", "UPRO"]].clip(0, 1), rebalance)
    numeric["Regime"] = weights["Regime"]
    return numeric


def calc_target_weight(
    state: str,
    current_vol: float,
    target_vol: float,
    upro_cap: float,
    max_beta: float,
    bear_spy: float,
) -> pd.Series:
    if state == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({"SPY": bear_spy, "UPRO": 0.0})
    target_beta = min(target_vol / current_vol, max_beta)
    spy_weight, upro_weight = beta_to_weights(target_beta, upro_cap)
    return pd.Series({"SPY": spy_weight, "UPRO": upro_weight})


def backtest(weights: pd.DataFrame, ret_spy: pd.Series, ret_upro: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights[["SPY", "UPRO"]].diff().abs().sum(axis=1).fillna(weights[["SPY", "UPRO"]].abs().sum(axis=1))
    daily_ret = weights["SPY"] * ret_spy + weights["UPRO"] * ret_upro - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


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
    peak_date = nav.loc[:mdd_date].idxmax()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS) if daily_ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return {
        "nav": nav,
        "dd": dd,
        "mdd_date": mdd_date,
        "mdd_peak_date": peak_date,
        "mdd_peak_value": peak_nav.loc[mdd_date],
        "mdd_trough_value": nav.loc[mdd_date],
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "win_m": win_m,
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
        "Avg SPY": np.nan if weights is None else weights["SPY"].mean(),
        "Avg UPRO": np.nan if weights is None else weights["UPRO"].mean(),
        "Max UPRO": np.nan if weights is None else weights["UPRO"].max(),
    }


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
    for symbol in [SPY, UPRO]:
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
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 1, 4))
    with c2:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Trend Filter")
    fast_window = st.slider("SPY fast MA", 20, 120, 30, 5)
    slow_window = st.slider("SPY slow MA", 100, 250, 200, 5)
    trend_rule = st.selectbox("Bull trend rule", ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow and Fast > Slow"], index=0)

    st.subheader("Volatility Target")
    preset = st.selectbox("Preset", ["Balanced return", "Lower MDD", "Aggressive"], index=0)
    defaults = {
        "Balanced return": {"target_vol": 35, "max_beta": 180, "upro_cap": 50, "bear_spy": 50, "rebalance_index": 0},
        "Lower MDD": {"target_vol": 30, "max_beta": 130, "upro_cap": 35, "bear_spy": 10, "rebalance_index": 1},
        "Aggressive": {"target_vol": 55, "max_beta": 220, "upro_cap": 65, "bear_spy": 30, "rebalance_index": 1},
    }[preset]
    vol_window = st.slider("SPY volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 80, defaults["target_vol"], 5) / 100
    max_beta = st.slider("Max SPY-equivalent exposure (%)", 50, 250, defaults["max_beta"], 5) / 100
    upro_cap = st.slider("UPRO max weight (%)", 0, 80, defaults["upro_cap"], 5) / 100
    bear_spy = st.slider("Bear-regime SPY weight (%)", 0, 100, defaults["bear_spy"], 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=defaults["rebalance_index"], horizontal=True)
    cost_rate = st.number_input("Trading cost per turnover (%)", min_value=0.0, value=0.25, step=0.05) / 100

    st.subheader("Execution Plan")
    account_value = st.number_input("Account value ($)", min_value=0.0, value=0.0, step=1000.0)
    current_spy_shares = st.number_input("Current SPY shares", min_value=0.0, value=0.0, step=1.0)
    current_upro_shares = st.number_input("Current UPRO shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash ($)", min_value=0.0, value=0.0, step=1000.0)
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

with st.expander("Strategy Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Bull trend | {trend_rule} |
| Bear | Bull trend is false |
| Target volatility | {target_vol:.0%}, based on SPY {vol_window}D realized volatility |
| UPRO cap | {upro_cap:.0%} |
| Bear allocation | SPY {bear_spy:.0%}, Cash {1 - bear_spy:.0%} |
"""
    )

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

progress = st.progress(0, text="Loading market data...")
end_dt = datetime.combine(end_date, datetime.min.time())
warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=max(slow_window, vol_window) * 3)

try:
    progress.progress(20, text="Loading SPY data...")
    spy = load_yahoo_chart(SPY, warmup_start, end_dt)
    progress.progress(40, text="Loading UPRO data...")
    upro = load_yahoo_chart(UPRO, warmup_start, end_dt)
except Exception as exc:
    st.error(f"Failed to load Yahoo data: {exc}")
    st.stop()

common_idx = spy.index.intersection(upro.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 60:
    st.error("Not enough data for the selected backtest period.")
    st.stop()

full_idx = spy.index.intersection(upro.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

price = spy["adjclose"].reindex(full_idx).ffill()
fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
regime_full = build_regime(price, fast_ma, slow_ma, trend_rule)

spy_ret_full = spy["adjclose"].pct_change().reindex(full_idx).fillna(0.0)
vol = spy_ret_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
weights_full = build_strategy_weights(
    price,
    regime_full,
    vol,
    target_vol,
    upro_cap,
    max_beta,
    bear_spy,
    rebalance,
)

weights = weights_full.reindex(common_idx).ffill().fillna({"SPY": 0.0, "UPRO": 0.0, "Regime": "Bear"})
ret_spy = spy["adjclose"].pct_change().reindex(common_idx).fillna(0.0)
ret_upro = upro["adjclose"].pct_change().reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_spy, ret_upro, cost_rate)
bench_spy = ret_spy
bench_upro = ret_upro
fixed_80_20 = 0.8 * ret_spy + 0.2 * ret_upro - 0.0
fixed_70_30 = 0.7 * ret_spy + 0.3 * ret_upro - 0.0

progress.progress(100, text="Done")
progress.empty()

strategy_metrics = calc_metrics(strategy_ret)
spy_metrics = calc_metrics(bench_spy)
latest = weights.iloc[-1]
latest_date = weights.index[-1].date()
latest_regime = str(regime_full.reindex(weights.index).ffill().iloc[-1])
latest_vol = vol.reindex(weights.index).ffill().iloc[-1]
next_target = calc_target_weight(latest_regime, latest_vol, target_vol, upro_cap, max_beta, bear_spy)
latest_prices = pd.Series(
    {
        SPY: spy["adjclose"].reindex(weights.index).ffill().iloc[-1],
        UPRO: upro["adjclose"].reindex(weights.index).ffill().iloc[-1],
    }
)
execution_plan, target_cash = build_execution_plan(
    next_target,
    latest_prices,
    account_value,
    pd.Series({SPY: current_spy_shares, UPRO: current_upro_shares}),
    current_cash,
)
action_label = position_action_label(execution_plan["Order Shares"].abs().sum(), tolerance=0.5)

st.success(
    f"{action_label} | Next target from close signal ({latest_date}): {latest_regime} | "
    f"SPY {next_target[SPY]:.1%}, UPRO {next_target[UPRO]:.1%}, Cash {1 - next_target.sum():.1%} | "
    f"SPY {vol_window}D volatility {latest_vol:.1%}"
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"SPY {spy_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"SPY {spy_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"SPY {spy_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"SPY {spy_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}", f"SPY {spy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

tab_perf, tab_signal, tab_weights, tab_execution, tab_table = st.tabs(
    ["Performance", "Signals", "Weights", "Execution", "Tables"]
)

with tab_perf:
    nav_df = pd.DataFrame(
        {
            "Strategy": strategy_metrics["nav"],
            "SPY 100%": calc_metrics(bench_spy)["nav"],
            "UPRO 100%": calc_metrics(bench_upro)["nav"],
            "SPY 80% + UPRO 20%": calc_metrics(fixed_80_20)["nav"],
            "SPY 70% + UPRO 30%": calc_metrics(fixed_70_30)["nav"],
        }
    )
    st.pyplot(
        static_line_chart(
            nav_df,
            "Cumulative NAV with Strategy MDD",
            height=420,
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
            "Strategy DD": strategy_metrics["dd"] * 100,
            "SPY DD": calc_metrics(bench_spy)["dd"] * 100,
            "UPRO DD": calc_metrics(bench_upro)["dd"] * 100,
        }
    )
    st.pyplot(
        static_line_chart(dd_df, f"Drawdown | Strategy MDD {strategy_metrics['mdd']:.1%}", percent_axis=True, height=320),
        clear_figure=True,
    )

with tab_signal:
    signal_df = pd.DataFrame(
        {
            "SPY": price.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
        }
    )
    st.pyplot(static_line_chart(signal_df, "SPY Trend", yaxis_title="Price", height=320), clear_figure=True)
    st.dataframe(
        pd.DataFrame(
            {
                "Regime Signal": regime_full.reindex(common_idx).ffill(),
                "Held Regime": weights["Regime"],
                "SPY Vol": vol.reindex(common_idx),
            }
        ).tail(40),
        use_container_width=True,
    )

with tab_weights:
    weight_df = weights[["SPY", "UPRO"]].copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.pyplot(static_area_chart(weight_df, "Portfolio Weights", height=320), clear_figure=True)
    exposure_df = pd.DataFrame(
        {
            "SPY-equivalent exposure": weights["SPY"] + weights["UPRO"] * 3,
            "SPY Vol": vol.reindex(common_idx),
            "Target Vol": target_vol,
            "SPY Weight": weights["SPY"],
            "UPRO Weight": weights["UPRO"],
        }
    )
    st.pyplot(static_line_chart(exposure_df, "Exposure and Volatility", height=340), clear_figure=True)

with tab_execution:
    c1, c2, c3 = st.columns(3)
    c1.metric("Target Cash", f"${target_cash:,.2f}")
    c2.metric("Latest SPY Vol", f"{latest_vol:.1%}")
    c3.metric("Target Invested", f"{next_target.sum():.1%}")
    st.dataframe(
        execution_plan.style.format(
            {
                "Latest Price": "${:,.2f}",
                "Target Weight": "{:.1%}",
                "Target Value": "${:,.2f}",
                "Target Shares": "{:,.0f}",
                "Current Shares": "{:,.0f}",
                "Order Shares": "{:,.0f}",
                "Estimated Order Value": "${:,.2f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

with tab_table:
    comparison = pd.DataFrame(
        [
            metric_row("Strategy", strategy_ret, weights),
            metric_row("SPY 100%", bench_spy),
            metric_row("UPRO 100%", bench_upro),
            metric_row("SPY 80% + UPRO 20%", fixed_80_20),
            metric_row("SPY 70% + UPRO 30%", fixed_70_30),
        ]
    )
    formatted = comparison.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SPY", "Avg UPRO", "Max UPRO"]:
        formatted[col] = formatted[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    formatted["Sharpe"] = formatted["Sharpe"].map(lambda x: f"{x:.2f}")
    formatted["Calmar"] = formatted["Calmar"].map(lambda x: f"{x:.2f}")
    st.subheader("Performance Summary")
    st.dataframe(formatted, use_container_width=True, hide_index=True)

    monthly = strategy_ret.add(1).resample("ME").prod().sub(1).to_frame("Strategy")
    monthly["SPY"] = bench_spy.add(1).resample("ME").prod().sub(1)
    monthly["UPRO"] = bench_upro.add(1).resample("ME").prod().sub(1)
    monthly["SPY 80% + UPRO 20%"] = fixed_80_20.add(1).resample("ME").prod().sub(1)
    monthly["SPY 70% + UPRO 30%"] = fixed_70_30.add(1).resample("ME").prod().sub(1)

    st.subheader("Monthly Returns")
    monthly_pivot = monthly["Strategy"].to_frame("Return")
    monthly_pivot["Year"] = monthly_pivot.index.year
    monthly_pivot["Month"] = monthly_pivot.index.month
    monthly_table = monthly_pivot.pivot(index="Year", columns="Month", values="Return")
    monthly_table.columns = [f"{month}M" for month in monthly_table.columns]
    monthly_table["Year"] = strategy_ret.add(1).groupby(strategy_ret.index.year).prod().sub(1)
    st.dataframe(monthly_table.map(lambda x: "-" if pd.isna(x) else f"{x:.1%}"), use_container_width=True)

    st.subheader("Yearly Returns")
    yearly = monthly.add(1).groupby(monthly.index.year).prod().sub(1)
    st.dataframe(yearly.map(lambda x: "-" if pd.isna(x) else f"{x:.1%}"), use_container_width=True)

    st.download_button(
        "Monthly Returns CSV",
        monthly.reset_index().rename(columns={"index": "Date"}).to_csv(index=False).encode("utf-8-sig"),
        "spy_upro_vol_target_monthly.csv",
        "text/csv",
    )
    st.download_button(
        "Weights CSV",
        weights[["SPY", "UPRO", "Regime"]].to_csv(index=True).encode("utf-8-sig"),
        "spy_upro_vol_target_weights.csv",
        "text/csv",
    )
