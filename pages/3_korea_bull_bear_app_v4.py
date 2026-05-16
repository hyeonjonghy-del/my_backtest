"""KODEX 200 / KODEX Leverage volatility-target backtest v4.

SOXX/SOXL-style dynamic allocation adapted for Korean ETFs:
- KODEX 200 creates the bull/bear regime and realized-volatility signal.
- KODEX 200 and KODEX Leverage are mixed to target portfolio volatility.
- KODEX Leverage is treated as roughly 2x KODEX 200 exposure.
- Charts use lightweight static matplotlib figures, like v3.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

warnings.filterwarnings("ignore")

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252
LEV_MULTIPLE = 2.0
KODEX_LABEL = "KODEX 200"
LEV_LABEL = "KODEX Leverage"
REGIME_LABEL = "Regime"
COLORS = {
    "strategy": "#0F766E",
    "kodex200": "#2563EB",
    "leverage": "#DC2626",
    "cash": "#64748B",
    "ma_fast": "#F59E0B",
    "ma_slow": "#111827",
    "vol": "#7C3AED",
    "target": "#F59E0B",
    "dd": "#B91C1C",
}

st.set_page_config(page_title="KODEX Vol Target v4", page_icon="KR", layout="wide")
st.title("KODEX 200 / Leverage Volatility Target v4")
st.caption(
    "Default: KODEX 200 MA30 > MA200, target vol 30%, leverage cap 50%, "
    "max KODEX200-equivalent exposure 1.4x, 10% KODEX 200 in bear regimes"
)


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def finite_return(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def safe_open_to_open(open_price: pd.Series) -> pd.Series:
    return finite_return(open_price.shift(-1) / open_price - 1)


@st.cache_data(show_spinner=False, ttl=3600)
def load_krx_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    from pykrx import stock

    raw = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        {
            "open": pd.to_numeric(raw["\uc2dc\uac00"], errors="coerce"),
            "high": pd.to_numeric(raw["\uace0\uac00"], errors="coerce"),
            "low": pd.to_numeric(raw["\uc800\uac00"], errors="coerce"),
            "close": pd.to_numeric(raw["\uc885\uac00"], errors="coerce"),
            "volume": pd.to_numeric(raw["\uac70\ub798\ub7c9"], errors="coerce"),
        }
    )
    df = normalize_index(df).dropna(how="all")
    return df.where(df > 0)


def calc_metrics(daily_ret: pd.Series) -> dict[str, object]:
    daily_ret = finite_return(daily_ret)
    nav = (1 + daily_ret).cumprod().replace([np.inf, -np.inf], np.nan).dropna()
    if len(nav) < 2:
        return {
            "nav": nav,
            "total": 0.0,
            "cagr": 0.0,
            "mdd": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "win_m": 0.0,
            "dd": pd.Series(dtype=float),
        }

    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS) if daily_ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("M").last().pct_change().dropna() > 0).mean()
    return {"nav": nav, "total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win_m": win_m, "dd": dd}


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
        "Avg KODEX 200": np.nan if weights is None else weights[KODEX_LABEL].mean(),
        "Avg Leverage": np.nan if weights is None else weights[LEV_LABEL].mean(),
        "Max Leverage": np.nan if weights is None else weights[LEV_LABEL].max(),
    }


def chart_data(data: pd.DataFrame, max_points: int = 900) -> pd.DataFrame:
    clean = data.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if len(clean) <= max_points:
        return clean
    step = int(np.ceil(len(clean) / max_points))
    return clean.iloc[::step].copy()


def render_static_line(data: pd.DataFrame, title: str, ylabel: str = "", height: float = 3.8, percent_axis: bool = False) -> None:
    clean = chart_data(data)
    fig, ax = plt.subplots(figsize=(11, height), dpi=120)
    palette = [
        COLORS["strategy"],
        COLORS["kodex200"],
        COLORS["leverage"],
        COLORS["ma_fast"],
        COLORS["ma_slow"],
        COLORS["vol"],
        COLORS["target"],
        COLORS["dd"],
    ]
    for i, column in enumerate(clean.columns):
        series = clean[column].dropna()
        ax.plot(series.index, series.values, label=str(column), color=palette[i % len(palette)], linewidth=2.0 if i == 0 else 1.5)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=min(len(clean.columns), 3), frameon=False)
    if percent_axis:
        ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def render_static_area(data: pd.DataFrame, title: str, height: float = 3.2) -> None:
    clean = chart_data(data)
    fig, ax = plt.subplots(figsize=(11, height), dpi=120)
    columns = list(clean.columns)
    colors = [COLORS["kodex200"], COLORS["leverage"], COLORS["cash"]][: len(columns)]
    ax.stackplot(clean.index, [clean[col].values for col in columns], labels=columns, colors=colors, alpha=0.78)
    ax.set_ylim(0, 1)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=3, frameon=False)
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def render_yearly_bars(strategy_ret: pd.Series, kodex200_ret: pd.Series, leverage_ret: pd.Series) -> None:
    yearly = pd.DataFrame(
        {
            "Strategy": (1 + strategy_ret).groupby(strategy_ret.index.year).prod() - 1,
            KODEX_LABEL: (1 + kodex200_ret).groupby(kodex200_ret.index.year).prod() - 1,
            LEV_LABEL: (1 + leverage_ret).groupby(leverage_ret.index.year).prod() - 1,
        }
    ).dropna(how="all") * 100

    fig, ax = plt.subplots(figsize=(11, 4.0), dpi=120)
    x = np.arange(len(yearly.index))
    width = 0.25
    bars = [("Strategy", COLORS["strategy"], -width), (KODEX_LABEL, COLORS["kodex200"], 0), (LEV_LABEL, COLORS["leverage"], width)]
    for name, color, offset in bars:
        ax.bar(x + offset, yearly[name], width=width, label=name, color=color)
    ax.axhline(0, color="#6B7280", linewidth=1, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels(yearly.index.astype(str))
    ax.set_ylabel("Return (%)")
    ax.set_title("Yearly Returns", loc="left", fontsize=13, fontweight="bold")
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=3, frameon=False)
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def build_regime(close: pd.Series, fast_ma: pd.Series, slow_ma: pd.Series, trend_rule: str) -> pd.Series:
    if trend_rule == "MA Fast > MA Slow":
        trend = fast_ma > slow_ma
    elif trend_rule == "Close > MA Slow":
        trend = close > slow_ma
    else:
        trend = (close > slow_ma) & (fast_ma > slow_ma)

    regime = pd.Series("Bear", index=close.index, dtype=object)
    regime.loc[trend.fillna(False)] = "Bull"
    return regime.rename(REGIME_LABEL)


def rebalance_weights(weights: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "Daily":
        return weights

    out = weights.copy() * 0.0
    current = pd.Series({KODEX_LABEL: 0.0, LEV_LABEL: 0.0})
    last_key = None
    for date, row in weights.iterrows():
        key = date.isocalendar()[:2] if frequency == "Weekly" else (date.year, date.month)
        if key != last_key:
            current = row
            last_key = key
        out.loc[date] = current
    return out


def exposure_to_weights(target_exposure: float, leverage_cap: float) -> tuple[float, float]:
    target_exposure = max(float(target_exposure), 0.0)
    if target_exposure <= 1:
        return min(target_exposure, 1.0), 0.0

    leverage_weight = min((target_exposure - 1) / (LEV_MULTIPLE - 1), leverage_cap, 1.0)
    kodex_weight = min(max(target_exposure - LEV_MULTIPLE * leverage_weight, 0.0), 1 - leverage_weight)
    total = kodex_weight + leverage_weight
    if total > 1:
        kodex_weight /= total
        leverage_weight /= total
    return kodex_weight, leverage_weight


def build_strategy_weights(
    price: pd.Series,
    regime: pd.Series,
    vol: pd.Series,
    target_vol: float,
    leverage_cap: float,
    max_exposure: float,
    bear_kodex: float,
    rebalance: str,
) -> pd.DataFrame:
    executable_regime = regime.shift(1).reindex(price.index).ffill().fillna("Bear")
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_exposure = (target_vol / vol_lag).clip(0, max_exposure).fillna(0.0)

    rows = []
    for date in price.index:
        state = executable_regime.loc[date]
        if state == "Bear":
            kodex_weight, leverage_weight = bear_kodex, 0.0
        else:
            kodex_weight, leverage_weight = exposure_to_weights(desired_exposure.loc[date], leverage_cap)
        rows.append({KODEX_LABEL: kodex_weight, LEV_LABEL: leverage_weight, REGIME_LABEL: state})

    weights = pd.DataFrame(rows, index=price.index)
    numeric = rebalance_weights(weights[[KODEX_LABEL, LEV_LABEL]].clip(0, 1), rebalance)
    numeric[REGIME_LABEL] = weights[REGIME_LABEL]
    return numeric


def calc_target_weight(state: str, current_vol: float, target_vol: float, leverage_cap: float, max_exposure: float, bear_kodex: float) -> pd.Series:
    if state == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({KODEX_LABEL: bear_kodex, LEV_LABEL: 0.0})

    target_exposure = min(target_vol / current_vol, max_exposure)
    kodex_weight, leverage_weight = exposure_to_weights(target_exposure, leverage_cap)
    return pd.Series({KODEX_LABEL: kodex_weight, LEV_LABEL: leverage_weight})


def backtest(weights: pd.DataFrame, ret_kodex: pd.Series, ret_lev: pd.Series, cost_rate: float) -> pd.Series:
    numeric = weights[[KODEX_LABEL, LEV_LABEL]]
    turnover = numeric.diff().abs().sum(axis=1).fillna(numeric.abs().sum(axis=1))
    daily_ret = numeric[KODEX_LABEL] * ret_kodex + numeric[LEV_LABEL] * ret_lev - turnover * cost_rate
    return finite_return(daily_ret)


with st.sidebar:
    st.header("Strategy Settings")
    st.subheader("Period")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 5, 16))
    with c2:
        end_date = st.date_input("End", datetime.today().date())

    st.subheader("Trend Filter")
    trend_rule = st.selectbox("Bull trend rule", ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow + MA Fast > MA Slow"], index=0)
    fast_window = st.slider("KODEX 200 fast MA", 20, 120, 30, 5)
    slow_window = st.slider("KODEX 200 slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Target")
    vol_window = st.slider("Realized volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 60, 30, 5) / 100
    max_exposure = st.slider("Max KODEX200-equivalent exposure (%)", 50, 200, 140, 5) / 100
    leverage_cap = st.slider("KODEX Leverage max weight (%)", 0, 80, 50, 5) / 100

    st.subheader("Bear / Trading")
    bear_kodex = st.slider("Bear-regime KODEX 200 weight (%)", 0, 100, 10, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=1, horizontal=True)
    cost_rate = st.number_input("Trading cost per turnover (%)", min_value=0.0, value=0.03, step=0.01) / 100
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

with st.expander("Strategy Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Bull regime | {trend_rule} |
| Volatility | KODEX 200 RV{vol_window}, target {target_vol:.0%} |
| Exposure | Max KODEX200-equivalent {max_exposure:.2f}x, leverage cap {leverage_cap:.0%} |
| Bull allocation | KODEX 200 + KODEX Leverage + cash, sized to target volatility |
| Bear allocation | KODEX 200 {bear_kodex:.0%}, cash {1 - bear_kodex:.0%} |

KODEX200-equivalent exposure = KODEX 200 weight + KODEX Leverage weight x 2
"""
    )

if not run_btn:
    st.info("Adjust the sidebar settings, then run the backtest. Defaults are conservative for Korean ETFs.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup_days = max(slow_window, vol_window) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="Loading data...")
progress.progress(20, text="Loading KODEX 200 data...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(45, text="Loading KODEX Leverage data...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)

if kodex_200.empty or kodex_lev.empty:
    st.error("Could not load KODEX ETF data. Check pykrx or the KRX data connection.")
    st.stop()

common_idx = kodex_200.index.intersection(kodex_lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 60:
    st.error("Not enough trading-day data for the selected backtest period.")
    st.stop()

full_idx = kodex_200.index.intersection(kodex_lev.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

kodex_close = kodex_200["close"].reindex(full_idx).ffill()
fast_ma = kodex_close.rolling(fast_window).mean()
slow_ma = kodex_close.rolling(slow_window).mean()
close_ret_kodex_full = finite_return(kodex_close.pct_change())
realized_vol = close_ret_kodex_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
regime_full = build_regime(kodex_close, fast_ma, slow_ma, trend_rule)

ret_kodex_full = safe_open_to_open(kodex_200["open"].reindex(full_idx).ffill())
ret_lev_full = safe_open_to_open(kodex_lev["open"].reindex(full_idx).ffill())

progress.progress(75, text="Calculating weights and returns...")
weights_full = build_strategy_weights(kodex_close, regime_full, realized_vol, target_vol, leverage_cap, max_exposure, bear_kodex, rebalance)
weights = weights_full.reindex(common_idx).ffill().fillna({KODEX_LABEL: 0.0, LEV_LABEL: 0.0, REGIME_LABEL: "Bear"})
ret_kodex = ret_kodex_full.reindex(common_idx).fillna(0.0)
ret_lev = ret_lev_full.reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_kodex, ret_lev, cost_rate)

bench_kodex = ret_kodex
bench_lev = ret_lev
fixed_70_30 = 0.7 * ret_kodex + 0.3 * ret_lev
fixed_50_50 = 0.5 * ret_kodex + 0.5 * ret_lev

strategy_metrics = calc_metrics(strategy_ret)
kodex_metrics = calc_metrics(bench_kodex)
lev_metrics = calc_metrics(bench_lev)
progress.empty()

latest_date = common_idx[-1].date()
latest_regime = str(regime_full.reindex(common_idx).ffill().iloc[-1])
latest_vol = realized_vol.reindex(common_idx).ffill().iloc[-1]
next_target = calc_target_weight(latest_regime, latest_vol, target_vol, leverage_cap, max_exposure, bear_kodex)
target_cash = max(0.0, 1 - next_target.sum())
latest_close = kodex_close.reindex(common_idx).iloc[-1]
latest_fast = fast_ma.reindex(common_idx).iloc[-1]
latest_slow = slow_ma.reindex(common_idx).iloc[-1]

st.success(
    f"Next-open target from close signal ({latest_date}): {latest_regime} | "
    f"KODEX 200 {next_target[KODEX_LABEL]:.1%}, KODEX Leverage {next_target[LEV_LABEL]:.1%}, Cash {target_cash:.1%}"
)
st.caption(
    f"KODEX 200 {latest_close:,.0f} / MA{fast_window} {latest_fast:,.0f} / "
    f"MA{slow_window} {latest_slow:,.0f} / RV{vol_window} {latest_vol:.1%} / Target {target_vol:.0%}"
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"KODEX200 {kodex_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"LEV {lev_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"LEV {lev_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"KODEX200 {kodex_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

avg_exposure = (weights[KODEX_LABEL] + weights[LEV_LABEL] * LEV_MULTIPLE).mean()
trade_count = int(weights[[KODEX_LABEL, LEV_LABEL]].diff().abs().sum(axis=1).fillna(0).gt(0).sum())
trade_cols = st.columns(3)
trade_cols[0].metric("Avg Equivalent Exposure", f"{avg_exposure:.2f}x")
trade_cols[1].metric("Avg Leverage Weight", f"{weights[LEV_LABEL].mean():.1%}")
trade_cols[2].metric("Weight Changes", f"{trade_count:,}")

tab_perf, tab_signal, tab_weights, tab_table, tab_monthly = st.tabs(["Performance", "Signal", "Weights", "Comparison", "Monthly"])

with tab_perf:
    nav_df = pd.DataFrame(
        {
            "Strategy": strategy_metrics["nav"],
            KODEX_LABEL: kodex_metrics["nav"],
            LEV_LABEL: lev_metrics["nav"],
            "KODEX 70% + Leverage 30%": calc_metrics(fixed_70_30)["nav"],
        }
    )
    render_static_line(nav_df, "Cumulative NAV", "NAV", 3.8, False)
    render_yearly_bars(strategy_ret, bench_kodex, bench_lev)

    dd_chart = pd.DataFrame(
        {
            "Strategy DD": strategy_metrics["dd"],
            "KODEX 200 DD": kodex_metrics["dd"],
            "KODEX Leverage DD": lev_metrics["dd"],
        }
    ) * 100
    render_static_line(dd_chart, "Drawdown", "%", 3.0, True)

with tab_signal:
    trend_chart = pd.DataFrame(
        {
            KODEX_LABEL: kodex_close.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
        }
    )
    render_static_line(trend_chart, "Trend", "Price", 3.2, False)

    vol_chart = pd.DataFrame(
        {
            f"KODEX 200 RV{vol_window}": realized_vol.reindex(common_idx) * 100,
            "Target Vol": pd.Series(target_vol * 100, index=common_idx),
        }
    )
    render_static_line(vol_chart, "Annualized Realized Volatility", "%", 3.0, True)

    st.subheader("Recent Signals")
    signal_table = pd.DataFrame(
        {
            "Signal Regime": regime_full.reindex(common_idx).ffill(),
            "Held Regime": weights[REGIME_LABEL],
            KODEX_LABEL: kodex_close.reindex(common_idx),
            f"MA{fast_window}": fast_ma.reindex(common_idx),
            f"MA{slow_window}": slow_ma.reindex(common_idx),
            f"RV{vol_window}": realized_vol.reindex(common_idx),
        }
    ).tail(40)
    st.dataframe(signal_table, use_container_width=True)

with tab_weights:
    weight_chart = weights[[KODEX_LABEL, LEV_LABEL]].copy()
    weight_chart["Cash"] = (1 - weight_chart.sum(axis=1)).clip(0, 1)
    render_static_area(weight_chart, "Portfolio Weights", 3.2)

    exposure_chart = pd.DataFrame(
        {
            "KODEX200-equivalent exposure": weights[KODEX_LABEL] + weights[LEV_LABEL] * LEV_MULTIPLE,
            "KODEX 200 Weight": weights[KODEX_LABEL],
            "Leverage Weight": weights[LEV_LABEL],
        }
    )
    render_static_line(exposure_chart, "Exposure and Weights", "Weight / Exposure", 3.2, False)

    st.subheader("Recent Target Weights")
    recent_weights = weights[[KODEX_LABEL, LEV_LABEL, REGIME_LABEL]].tail(40).copy()
    recent_weights["Cash"] = (1 - weights[[KODEX_LABEL, LEV_LABEL]].sum(axis=1)).clip(0, 1).tail(40)
    st.dataframe(recent_weights, use_container_width=True)

with tab_table:
    comparison = pd.DataFrame(
        [
            metric_row("Strategy", strategy_ret, weights),
            metric_row("KODEX 200 100%", bench_kodex),
            metric_row("KODEX Leverage 100%", bench_lev),
            metric_row("KODEX 70% + Leverage 30%", fixed_70_30),
            metric_row("KODEX 50% + Leverage 50%", fixed_50_50),
        ]
    )
    formatted = comparison.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg KODEX 200", "Avg Leverage", "Max Leverage"]:
        formatted[col] = formatted[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        formatted[col] = formatted[col].map(lambda x: f"{x:.2f}")
    st.dataframe(formatted, use_container_width=True, hide_index=True)

    weights_download = weights[[KODEX_LABEL, LEV_LABEL, REGIME_LABEL]].copy()
    weights_download["Cash"] = (1 - weights[[KODEX_LABEL, LEV_LABEL]].sum(axis=1)).clip(0, 1)
    st.download_button("Weights CSV", weights_download.to_csv(index=True).encode("utf-8-sig"), "kodex_vol_target_v4_weights.csv", "text/csv")

with tab_monthly:
    monthly_strategy = strategy_ret.add(1).resample("M").prod().sub(1)
    monthly_kodex = bench_kodex.add(1).resample("M").prod().sub(1)
    monthly_lev = bench_lev.add(1).resample("M").prod().sub(1)
    monthly = pd.DataFrame({"Strategy": monthly_strategy, KODEX_LABEL: monthly_kodex, LEV_LABEL: monthly_lev}).dropna()

    pivot_source = monthly_strategy.to_frame("Return")
    pivot_source["Year"] = pivot_source.index.year
    pivot_source["Month"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    render_static_line(monthly * 100, "Monthly Strategy vs KODEX", "%", 3.0, True)
    st.download_button(
        "Monthly Returns CSV",
        monthly.reset_index().rename(columns={"index": "Date"}).to_csv(index=False).encode("utf-8-sig"),
        "kodex_vol_target_v4_monthly.csv",
        "text/csv",
    )
