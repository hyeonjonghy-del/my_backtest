"""KODEX 200 drawdown-recovery leverage rotation strategy v4.

The portfolio remains invested in KODEX 200. As the index falls from a rolling
high, part of KODEX 200 is replaced with KODEX Leverage. Large leverage weights
require a price-reversal signal, and the portfolio returns to KODEX 200 as the
drawdown is recovered.
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
COLORS = {
    "strategy": "#0F766E",
    "kodex200": "#2563EB",
    "leverage": "#DC2626",
    "drawdown": "#7C3AED",
    "ma": "#F59E0B",
}

st.set_page_config(page_title="KODEX Recovery Rotation v4", page_icon="KR", layout="wide")
st.title("KODEX 200 / Leverage Drawdown Recovery Strategy v4")
st.caption(
    "Stay invested in KODEX 200, replace part of it with KODEX Leverage after a drawdown, "
    "increase leverage only after a rebound signal, and return to KODEX 200 near recovery."
)


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def finite_return(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


@st.cache_data(show_spinner=False, ttl=3600)
def load_krx_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    from pykrx import stock

    raw = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        {
            "open": pd.to_numeric(raw["시가"], errors="coerce"),
            "high": pd.to_numeric(raw["고가"], errors="coerce"),
            "low": pd.to_numeric(raw["저가"], errors="coerce"),
            "close": pd.to_numeric(raw["종가"], errors="coerce"),
            "volume": pd.to_numeric(raw["거래량"], errors="coerce"),
        }
    )
    return normalize_index(df).dropna(how="all").where(df > 0)


def calc_metrics(nav: pd.Series) -> dict[str, object]:
    nav = nav.replace([np.inf, -np.inf], np.nan).dropna()
    nav = nav[nav > 0]
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
    ret = finite_return(nav.pct_change()).dropna()
    years = max(len(nav) / TRADING_DAYS, 1 / TRADING_DAYS)
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    monthly_nav = nav.groupby(nav.index.to_period("M")).last()
    win_m = (monthly_nav.pct_change().dropna() > 0).mean()
    return {
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "win_m": win_m,
        "dd": dd,
    }


def period_returns(nav: pd.Series, frequency: str) -> pd.Series:
    clean = nav.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return pd.Series(dtype=float)
    period_end = clean.groupby(clean.index.to_period(frequency)).last()
    previous = period_end.shift(1)
    previous.iloc[0] = clean.iloc[0]
    out = (period_end / previous - 1).dropna()
    out.index = out.index.to_timestamp(how="end").normalize()
    return out


def calendar_return_table(series_map: dict[str, pd.Series], frequency: str) -> pd.DataFrame:
    table = pd.concat(
        {name: period_returns(nav, frequency) for name, nav in series_map.items()},
        axis=1,
    )
    table.index = table.index.strftime("%Y") if frequency == "Y" else table.index.strftime("%Y-%m")
    return table


def monthly_return_matrix(nav: pd.Series) -> pd.DataFrame:
    monthly = period_returns(nav, "M")
    if monthly.empty:
        return pd.DataFrame()
    frame = monthly.rename("Return").to_frame()
    frame["Year"] = frame.index.year
    frame["Month"] = frame.index.month
    matrix = frame.pivot(index="Year", columns="Month", values="Return")
    return matrix.reindex(columns=range(1, 13)).rename(
        columns={month: pd.Timestamp(2000, month, 1).strftime("%b") for month in range(1, 13)}
    )


def format_return_table(df: pd.DataFrame) -> pd.DataFrame:
    return df.applymap(lambda value: "-" if pd.isna(value) else f"{value:.2%}")


def downsample(data: pd.DataFrame, max_points: int = 900) -> pd.DataFrame:
    clean = data.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if len(clean) <= max_points:
        return clean
    return clean.iloc[:: int(np.ceil(len(clean) / max_points))].copy()


def plot_lines(
    data: pd.DataFrame,
    title: str,
    ylabel: str = "",
    percent_axis: bool = False,
    height: float = 3.5,
) -> None:
    clean = downsample(data)
    fig, ax = plt.subplots(figsize=(11, height), dpi=120)
    palette = [COLORS["strategy"], COLORS["kodex200"], COLORS["leverage"], COLORS["ma"]]
    for i, col in enumerate(clean.columns):
        series = clean[col].dropna()
        ax.plot(series.index, series.values, label=str(col), color=palette[i % len(palette)], linewidth=2 if i == 0 else 1.5)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=min(len(clean.columns), 4), frameon=False)
    if percent_axis:
        ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def plot_weights(weights: pd.DataFrame) -> None:
    clean = downsample(weights[["KODEX Leverage", "KODEX 200"]].clip(0.0, 1.0) * 100)
    fig, ax = plt.subplots(figsize=(11, 3.0), dpi=120)
    ax.stackplot(
        clean.index,
        clean["KODEX Leverage"],
        clean["KODEX 200"],
        labels=["KODEX Leverage", "KODEX 200"],
        colors=[COLORS["leverage"], COLORS["kodex200"]],
        alpha=0.82,
    )
    ax.set_ylim(0, 100)
    ax.set_title("Portfolio Weights", loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel("%")
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=2, frameon=False)
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def build_recovery_strategy(
    kodex_close: pd.Series,
    lev_close: pd.Series,
    peak_window: int,
    reversal_ma_window: int,
    ma_slope_window: int,
    stage1_drawdown: float,
    stage2_drawdown: float,
    stage3_drawdown: float,
    recovery_exit_drawdown: float,
    stage1_leverage_weight: float,
    stage2_leverage_weight: float,
    rebound_leverage_weight: float,
    deep_rebound_leverage_weight: float,
    fee_rate: float,
) -> dict[str, object]:
    rolling_peak = kodex_close.rolling(peak_window, min_periods=1).max()
    drawdown = (kodex_close / rolling_peak - 1).rename("KODEX 200 Drawdown")
    reversal_ma = kodex_close.rolling(reversal_ma_window).mean()
    ma_rising = reversal_ma > reversal_ma.shift(ma_slope_window)
    above_reversal_ma = kodex_close > reversal_ma

    leverage_weight = pd.Series(0.0, index=kodex_close.index)
    leverage_weight.loc[drawdown <= -stage1_drawdown] = stage1_leverage_weight
    leverage_weight.loc[drawdown <= -stage2_drawdown] = stage2_leverage_weight

    rebound = (drawdown <= -stage2_drawdown) & above_reversal_ma
    deep_rebound = (drawdown <= -stage3_drawdown) & above_reversal_ma & ma_rising
    leverage_weight.loc[rebound] = rebound_leverage_weight
    leverage_weight.loc[deep_rebound] = deep_rebound_leverage_weight
    leverage_weight.loc[drawdown >= -recovery_exit_drawdown] = 0.0
    leverage_weight = leverage_weight.clip(0.0, 1.0).rename("KODEX Leverage")

    weights = pd.DataFrame(index=kodex_close.index)
    weights["KODEX Leverage"] = leverage_weight
    weights["KODEX 200"] = 1.0 - leverage_weight

    regime = pd.Series("Normal / KODEX 200", index=kodex_close.index, name="Regime")
    regime.loc[drawdown <= -stage1_drawdown] = "Stage 1 / Small Leverage"
    regime.loc[drawdown <= -stage2_drawdown] = "Stage 2 / Drawdown Leverage"
    regime.loc[rebound] = "Rebound / Leverage Increase"
    regime.loc[deep_rebound] = "Deep Rebound / Maximum Leverage"
    regime.loc[drawdown >= -recovery_exit_drawdown] = "Recovered / KODEX 200"

    returns = pd.DataFrame(
        {
            "KODEX Leverage": finite_return(lev_close.pct_change()),
            "KODEX 200": finite_return(kodex_close.pct_change()),
        },
        index=kodex_close.index,
    )
    executable = weights.shift(1).fillna({"KODEX Leverage": 0.0, "KODEX 200": 1.0})
    turnover = weights.diff().abs().sum(axis=1).fillna(0.0)
    strategy_ret = (executable * returns).sum(axis=1) - turnover.shift(1).fillna(0.0) * fee_rate
    nav = (1 + strategy_ret).cumprod().rename("Strategy")

    return {
        "rolling_peak": rolling_peak,
        "drawdown": drawdown,
        "reversal_ma": reversal_ma,
        "ma_rising": ma_rising.rename("MA Rising"),
        "above_reversal_ma": above_reversal_ma.rename("Above Reversal MA"),
        "rebound": rebound.rename("Rebound Signal"),
        "deep_rebound": deep_rebound.rename("Deep Rebound Signal"),
        "regime": regime,
        "weights": weights,
        "turnover": turnover,
        "nav": nav,
    }


with st.sidebar:
    st.header("Strategy Settings")
    st.subheader("Period")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 5, 16))
    with c2:
        end_date = st.date_input("End", datetime.today().date())

    st.subheader("Drawdown Reference")
    peak_window = st.slider("Rolling peak window (days)", 60, 756, 252, 21)
    reversal_ma_window = st.slider("Reversal MA (days)", 5, 60, 20, 5)
    ma_slope_window = st.slider("MA rising lookback (days)", 3, 30, 5, 1)

    st.subheader("Drawdown Stages")
    stage1_drawdown_pct = st.slider("Stage 1 drawdown (%)", 5, 20, 10, 1)
    stage2_drawdown_pct = st.slider("Stage 2 drawdown (%)", 10, 35, 20, 1)
    stage3_drawdown_pct = st.slider("Deep rebound drawdown (%)", 20, 50, 30, 1)
    recovery_exit_drawdown_pct = st.slider("Return to KODEX 200 within peak (%)", 0, 15, 8, 1)

    st.subheader("Leverage Replacement")
    stage1_leverage_pct = st.slider("Stage 1 leverage weight (%)", 0, 30, 10, 5)
    stage2_leverage_pct = st.slider("Stage 2 leverage weight (%)", 0, 40, 20, 5)
    rebound_leverage_pct = st.slider("Rebound leverage weight (%)", 0, 60, 35, 5)
    deep_rebound_leverage_pct = st.slider("Deep rebound leverage weight (%)", 0, 75, 50, 5)
    fee_pct = st.number_input("Trading cost per turnover (%)", min_value=0.0, value=0.03, step=0.01)
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

if not run_btn:
    st.info("Set the drawdown and rebound rules, then run the backtest. KODEX 200 remains the core holding at all times.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

if not (stage1_drawdown_pct < stage2_drawdown_pct < stage3_drawdown_pct):
    st.error("Drawdown stages must satisfy Stage 1 < Stage 2 < Deep Rebound.")
    st.stop()

if recovery_exit_drawdown_pct >= stage1_drawdown_pct:
    st.error("Recovery exit must be closer to the peak than Stage 1.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup_days = max(peak_window, reversal_ma_window, 120) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="Loading data...")
progress.progress(25, text="Loading KODEX 200 data...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(55, text="Loading KODEX Leverage data...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)

if kodex_200.empty or kodex_lev.empty:
    st.error("Could not load KODEX ETF data. Check pykrx or KRX data access.")
    st.stop()

full_idx = kodex_200.index.intersection(kodex_lev.index)
kodex_close_full = kodex_200["close"].reindex(full_idx).ffill()
lev_close_full = kodex_lev["close"].reindex(full_idx).ffill()
common_idx = full_idx[(full_idx.date >= start_date) & (full_idx.date <= end_date)]
if len(common_idx) < 60:
    st.error("Not enough trading-day data for the selected period.")
    st.stop()

result = build_recovery_strategy(
    kodex_close_full,
    lev_close_full,
    peak_window,
    reversal_ma_window,
    ma_slope_window,
    stage1_drawdown_pct / 100,
    stage2_drawdown_pct / 100,
    stage3_drawdown_pct / 100,
    recovery_exit_drawdown_pct / 100,
    stage1_leverage_pct / 100,
    stage2_leverage_pct / 100,
    rebound_leverage_pct / 100,
    deep_rebound_leverage_pct / 100,
    fee_pct / 100,
)

progress.progress(90, text="Rendering results...")
nav = result["nav"].reindex(common_idx).dropna()
weights = result["weights"].reindex(common_idx).fillna({"KODEX Leverage": 0.0, "KODEX 200": 1.0})
benchmark_200 = kodex_close_full.reindex(common_idx).ffill()
benchmark_200 = benchmark_200 / benchmark_200.iloc[0]
benchmark_lev = lev_close_full.reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]
drawdown = result["drawdown"].reindex(common_idx)
progress.empty()

strategy_metrics = calc_metrics(nav)
benchmark_200_metrics = calc_metrics(benchmark_200)
benchmark_lev_metrics = calc_metrics(benchmark_lev)
latest_date = common_idx[-1]
latest_weights = weights.iloc[-1]
latest_drawdown = float(drawdown.iloc[-1])
latest_regime = str(result["regime"].reindex(common_idx).iloc[-1])
latest_price = float(kodex_close_full.reindex(common_idx).iloc[-1])
latest_ma = float(result["reversal_ma"].reindex(common_idx).iloc[-1])

st.success(
    f"Current state ({latest_date.date()}): {latest_regime} | "
    f"KODEX 200 {latest_weights['KODEX 200']:.0%}, "
    f"KODEX Leverage {latest_weights['KODEX Leverage']:.0%}"
)
st.caption(
    f"Rolling-{peak_window}d drawdown {latest_drawdown:.1%} | "
    f"KODEX 200 {latest_price:,.0f} / MA{reversal_ma_window} {latest_ma:,.0f} | "
    "Today's signal is applied to the next trading day's return."
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"KODEX200 {benchmark_200_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"KODEX200 {benchmark_200_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"KODEX200 {benchmark_200_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

tab_perf, tab_returns, tab_rules, tab_data = st.tabs(["Performance", "Monthly / Yearly Returns", "Rules", "Data"])

with tab_perf:
    nav_chart = pd.DataFrame(
        {
            "Strategy": nav / nav.iloc[0],
            "KODEX 200 B&H": benchmark_200,
            "KODEX Leverage B&H": benchmark_lev,
        }
    )
    plot_lines(nav_chart, "Cumulative NAV", "NAV")
    dd_chart = pd.DataFrame(
        {
            "Strategy DD": strategy_metrics["dd"] * 100,
            "KODEX 200 DD": benchmark_200_metrics["dd"] * 100,
            "KODEX Leverage DD": benchmark_lev_metrics["dd"] * 100,
        }
    )
    plot_lines(dd_chart, "Portfolio Drawdown", "%", percent_axis=True, height=3.0)
    plot_weights(weights)
    signal_chart = pd.DataFrame(
        {
            "KODEX 200 Drawdown": drawdown * 100,
            "Stage 1": pd.Series(-stage1_drawdown_pct, index=common_idx),
            "Stage 2": pd.Series(-stage2_drawdown_pct, index=common_idx),
            "Deep Rebound": pd.Series(-stage3_drawdown_pct, index=common_idx),
        }
    )
    plot_lines(signal_chart, "Drawdown from Rolling Peak", "%", percent_axis=True, height=3.0)

    diag = pd.DataFrame(
        {
            "Metric": ["Average Leverage Weight", "Maximum Leverage Weight", "Leverage Days", "Rebound Days", "Turnover Sum"],
            "Value": [
                f"{weights['KODEX Leverage'].mean():.1%}",
                f"{weights['KODEX Leverage'].max():.1%}",
                f"{int((weights['KODEX Leverage'] > 0).sum()):,}",
                f"{int(result['rebound'].reindex(common_idx).fillna(False).sum()):,}",
                f"{result['turnover'].reindex(common_idx).sum():.1f}",
            ],
        }
    )
    st.dataframe(diag, use_container_width=True, hide_index=True)

with tab_returns:
    return_series = {"Strategy": nav, "KODEX 200": benchmark_200, "KODEX Leverage": benchmark_lev}
    monthly_returns = calendar_return_table(return_series, "M")
    yearly_returns = calendar_return_table(return_series, "Y")
    st.subheader("Strategy Monthly Returns")
    st.dataframe(format_return_table(monthly_return_matrix(nav)), use_container_width=True)
    st.subheader("Monthly Returns by Asset")
    st.dataframe(format_return_table(monthly_returns.sort_index(ascending=False)), use_container_width=True)
    st.subheader("Yearly Returns")
    st.dataframe(format_return_table(yearly_returns.sort_index(ascending=False)), use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Monthly Returns CSV",
            monthly_returns.to_csv(index=True).encode("utf-8-sig"),
            "kodex_drawdown_recovery_v4_monthly_returns.csv",
            "text/csv",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "Yearly Returns CSV",
            yearly_returns.to_csv(index=True).encode("utf-8-sig"),
            "kodex_drawdown_recovery_v4_yearly_returns.csv",
            "text/csv",
            use_container_width=True,
        )

with tab_rules:
    st.markdown(
        f"""
| State | Condition | KODEX 200 | KODEX Leverage |
|---|---|---:|---:|
| Normal / recovered | Drawdown is less than {stage1_drawdown_pct}% or recovers within {recovery_exit_drawdown_pct}% of peak | 100% | 0% |
| Stage 1 | Drawdown is at least {stage1_drawdown_pct}% | {100 - stage1_leverage_pct}% | {stage1_leverage_pct}% |
| Stage 2 | Drawdown is at least {stage2_drawdown_pct}% | {100 - stage2_leverage_pct}% | {stage2_leverage_pct}% |
| Rebound | Drawdown is at least {stage2_drawdown_pct}% and price is above MA{reversal_ma_window} | {100 - rebound_leverage_pct}% | {rebound_leverage_pct}% |
| Deep rebound | Drawdown is at least {stage3_drawdown_pct}%, price is above MA{reversal_ma_window}, and MA is rising | {100 - deep_rebound_leverage_pct}% | {deep_rebound_leverage_pct}% |
"""
    )
    st.warning(
        "This strategy does not avoid a bear market. It intentionally absorbs KODEX 200 losses and may increase market exposure during a deep drawdown."
    )

with tab_data:
    recent = pd.DataFrame(
        {
            "Regime": result["regime"].reindex(common_idx),
            "KODEX 200 Weight": weights["KODEX 200"],
            "KODEX Leverage Weight": weights["KODEX Leverage"],
            "Rolling Peak": result["rolling_peak"].reindex(common_idx),
            "KODEX 200": kodex_close_full.reindex(common_idx),
            "Drawdown": drawdown,
            f"MA{reversal_ma_window}": result["reversal_ma"].reindex(common_idx),
            "Above Reversal MA": result["above_reversal_ma"].reindex(common_idx),
            "MA Rising": result["ma_rising"].reindex(common_idx),
            "Rebound Signal": result["rebound"].reindex(common_idx),
            "Deep Rebound Signal": result["deep_rebound"].reindex(common_idx),
        }
    )
    st.dataframe(recent.tail(100), use_container_width=True)
    st.download_button(
        "Signal CSV",
        recent.to_csv(index=True).encode("utf-8-sig"),
        "kodex_drawdown_recovery_v4_signal.csv",
        "text/csv",
    )
