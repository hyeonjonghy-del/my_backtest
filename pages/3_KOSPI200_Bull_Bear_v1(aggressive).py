"""KODEX 200 / Leverage ON/OFF strategy v5.

Version 5 keeps the v4 trend and early-reentry framework, but splits high-RV
bull markets into upside-led and downside-stress regimes. High volatility no
longer forces one blunt cash allocation when the long trend is still healthy.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from chart_utils import static_yearly_returns_chart

from kiwoom_account import (
    KIWOOM_DOMESTIC_SOURCE,
    build_holdings_trade_plan,
    render_domestic_account_controls,
    render_domestic_account_summary,
)

warnings.filterwarnings("ignore")

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252
COLORS = {
    "strategy": "#0F766E",
    "kodex200": "#2563EB",
    "leverage": "#DC2626",
    "ma": "#F59E0B",
}

st.set_page_config(page_title="KODEX ON/OFF v5", page_icon="KR", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("KODEX 200 / Leverage ON-OFF Strategy v5")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True, key="run_backtest_top")
st.caption(
    "v5 core rules with a high-volatility bull regime split: upside-led volatility keeps some leverage, "
    "while downside-stress volatility shifts toward KODEX 200 and cash."
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
    primary = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if not raw.empty:
        primary = pd.DataFrame(
            {
                "open": pd.to_numeric(raw["시가"], errors="coerce"),
                "high": pd.to_numeric(raw["고가"], errors="coerce"),
                "low": pd.to_numeric(raw["저가"], errors="coerce"),
                "close": pd.to_numeric(raw["종가"], errors="coerce"),
                "volume": pd.to_numeric(raw["거래량"], errors="coerce"),
            }
        )
        primary = normalize_index(primary).dropna(how="all")

    # FinanceDataReader can return a truncated history for Korean ETFs. Fill
    # missing early dates from Yahoo so KODEX Leverage can be tested from its
    # 2010-02-22 listing date without changing the shared loader used by v1-v3.
    yahoo = pd.DataFrame(columns=primary.columns)
    try:
        import yfinance as yf

        start_dt = pd.to_datetime(start_str)
        end_dt = pd.to_datetime(end_str) + timedelta(days=1)
        yf_raw = yf.download(
            f"{ticker}.KS",
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
        )
        if isinstance(yf_raw.columns, pd.MultiIndex):
            yf_raw.columns = yf_raw.columns.get_level_values(0)
        if not yf_raw.empty:
            yahoo = yf_raw.rename(
                columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            ).reindex(columns=primary.columns)
            yahoo = normalize_index(yahoo).apply(pd.to_numeric, errors="coerce").dropna(how="all")
    except Exception:
        pass

    combined = primary.combine_first(yahoo) if not primary.empty else yahoo
    return normalize_index(combined).dropna(how="all").where(combined > 0)


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


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return finite_return(numerator / denominator.where(denominator > 0))


def portfolio_turnover(new_weights: pd.Series, old_weights: pd.Series) -> float:
    assets = [asset for asset in new_weights.index if asset != "Cash"]
    return float((new_weights[assets] - old_weights[assets]).abs().sum())


def backtest_next_open(
    dates: pd.DatetimeIndex,
    target_weights: pd.DataFrame,
    ret_co: pd.DataFrame,
    ret_oc: pd.DataFrame,
    fee_rate: float,
) -> tuple[pd.Series, pd.DataFrame]:
    nav = 1.0
    assets = list(target_weights.columns)
    current = pd.Series(0.0, index=assets)
    nav_rows = []
    weight_rows = []
    executable = target_weights.shift(1).reindex(dates).fillna(0.0)
    for i, date in enumerate(dates):
        nav *= 1 + float((current * ret_co.loc[date, assets]).sum())
        new_weights = executable.loc[date, assets].astype(float)
        turnover = portfolio_turnover(new_weights, current)
        if i > 0 and turnover > 0:
            nav *= 1 - min(fee_rate * turnover, 0.99)
        current = new_weights
        nav *= 1 + float((current * ret_oc.loc[date, assets]).sum())
        nav_rows.append(nav)
        weight_rows.append(current.copy())
    return pd.Series(nav_rows, index=dates, name="Next Open"), pd.DataFrame(weight_rows, index=dates)


def backtest_after_close_fill(
    dates: pd.DatetimeIndex,
    target_weights: pd.DataFrame,
    ret_co: pd.DataFrame,
    ret_oc: pd.DataFrame,
    fee_rate: float,
    fill_rate: float,
) -> tuple[pd.Series, pd.DataFrame]:
    nav = 1.0
    assets = list(target_weights.columns)
    open_weights = pd.Series(0.0, index=assets)
    nav_rows = []
    weight_rows = []
    open_targets = target_weights.shift(1).reindex(dates).fillna(0.0)
    for i, date in enumerate(dates):
        nav *= 1 + float((open_weights * ret_co.loc[date, assets]).sum())
        intraday = open_targets.loc[date, assets].astype(float)
        turnover = portfolio_turnover(intraday, open_weights)
        if i > 0 and turnover > 0:
            nav *= 1 - min(fee_rate * turnover, 0.99)
        nav *= 1 + float((intraday * ret_oc.loc[date, assets]).sum())
        close_weights = intraday + (target_weights.loc[date, assets].astype(float) - intraday) * fill_rate
        close_turnover = portfolio_turnover(close_weights, intraday)
        if close_turnover > 0:
            nav *= 1 - min(fee_rate * close_turnover, 0.99)
        open_weights = close_weights
        nav_rows.append(nav)
        weight_rows.append(close_weights.copy())
    return pd.Series(nav_rows, index=dates, name="After-Close Fill"), pd.DataFrame(weight_rows, index=dates)


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
    clean = downsample(weights[["KODEX Leverage", "KODEX 200", "Cash"]].clip(0.0, 1.0) * 100)
    fig, ax = plt.subplots(figsize=(11, 3.0), dpi=120)
    ax.stackplot(
        clean.index,
        clean["KODEX Leverage"],
        clean["KODEX 200"],
        clean["Cash"],
        labels=["KODEX Leverage", "KODEX 200", "Cash"],
        colors=[COLORS["leverage"], COLORS["kodex200"], "#9CA3AF"],
        alpha=0.82,
    )
    ax.set_ylim(0, 100)
    ax.set_title("Portfolio Weights", loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel("%")
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=3, frameon=False)
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def build_v5_high_vol_split_strategy(
    kodex_close: pd.Series,
    lev_close: pd.Series,
    kodex_open: pd.Series,
    lev_open: pd.Series,
    vol_price: pd.Series,
    long_ma_window: int,
    vol_window: int,
    vol_cap: float,
    bull_leverage_weight: float,
    upside_high_vol_kodex_weight: float,
    upside_high_vol_leverage_weight: float,
    stress_high_vol_kodex_weight: float,
    stress_high_vol_leverage_weight: float,
    recent_return_window: int,
    drawdown_window: int,
    pullback_threshold: float,
    downside_vol_ratio: float,
    early_reentry_on: bool,
    short_ma_window: int,
    short_ma_slope_window: int,
    recent_low_window: int,
    rebound_from_low: float,
    early_kodex_weight: float,
    early_leverage_weight: float,
    fee_rate: float,
) -> dict[str, object]:
    long_ma = kodex_close.rolling(long_ma_window).mean()
    short_ma = kodex_close.rolling(short_ma_window).mean()
    vol_returns = finite_return(vol_price.pct_change())
    realized_vol = vol_returns.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    upside_vol = vol_returns.where(vol_returns > 0, 0.0).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    downside_vol = vol_returns.where(vol_returns < 0, 0.0).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    recent_return = (kodex_close / kodex_close.shift(recent_return_window) - 1).rename("Recent Return")
    recent_high = kodex_close.rolling(drawdown_window).max()
    pullback = (kodex_close / recent_high - 1).rename("Pullback From Recent High")
    trend_signal = (kodex_close > long_ma).rename("Long Trend Signal")
    low_vol_signal = (realized_vol < vol_cap).rename("Low Vol Signal")
    bull_signal = (trend_signal & low_vol_signal).rename("Bull Leverage Signal")
    high_vol_bull = (trend_signal & ~low_vol_signal).rename("High Vol Bull Signal")

    short_ma_rising = short_ma > short_ma.shift(short_ma_slope_window)
    short_trend_ok = ((kodex_close > short_ma) & short_ma_rising).rename("Short Trend OK")
    downside_stress = (
        (recent_return < 0)
        | (pullback <= -pullback_threshold)
        | (downside_vol > upside_vol * downside_vol_ratio)
        | (~short_trend_ok)
    ).rename("Downside Stress Signal")
    upside_high_vol_bull = (high_vol_bull & ~downside_stress).rename("Upside High Vol Bull Signal")
    stress_high_vol_bull = (high_vol_bull & downside_stress).rename("Downside Stress Bull Signal")
    recent_low = kodex_close.rolling(recent_low_window).min()
    rebound_return = (kodex_close / recent_low - 1).rename("Rebound From Recent Low")
    early_reentry = (
        (~trend_signal)
        & (kodex_close > short_ma)
        & short_ma_rising
        & (rebound_return >= rebound_from_low)
    )
    if not early_reentry_on:
        early_reentry = pd.Series(False, index=kodex_close.index)
    early_reentry = early_reentry.rename("Early Reentry Signal")

    weights = pd.DataFrame(index=kodex_close.index)
    weights["KODEX Leverage"] = bull_signal.astype(float) * bull_leverage_weight
    weights["KODEX 200"] = 0.0
    weights.loc[upside_high_vol_bull, "KODEX Leverage"] = upside_high_vol_leverage_weight
    weights.loc[upside_high_vol_bull, "KODEX 200"] = upside_high_vol_kodex_weight
    weights.loc[stress_high_vol_bull, "KODEX Leverage"] = stress_high_vol_leverage_weight
    weights.loc[stress_high_vol_bull, "KODEX 200"] = stress_high_vol_kodex_weight
    weights.loc[early_reentry, "KODEX Leverage"] = early_leverage_weight
    weights.loc[early_reentry, "KODEX 200"] = early_kodex_weight
    invested = weights["KODEX Leverage"] + weights["KODEX 200"]
    scale = pd.Series(np.where(invested > 1.0, 1.0 / invested, 1.0), index=weights.index)
    weights[["KODEX Leverage", "KODEX 200"]] = weights[["KODEX Leverage", "KODEX 200"]].mul(scale, axis=0)
    weights["Cash"] = (1.0 - weights["KODEX Leverage"] - weights["KODEX 200"]).clip(0.0, 1.0)

    regime = pd.Series("Bear / Cash", index=kodex_close.index, name="Regime")
    regime.loc[upside_high_vol_bull] = "Upside High Vol Bull / KODEX 200 + Leverage"
    regime.loc[stress_high_vol_bull] = "Downside Stress Bull / KODEX 200 + Cash"
    regime.loc[bull_signal] = "Bull / KODEX Leverage"
    regime.loc[early_reentry] = "Early Reentry / Mixed Position"

    ret_co = pd.DataFrame(
        {
            "KODEX Leverage": safe_divide(lev_open - lev_close.shift(1), lev_close.shift(1)),
            "KODEX 200": safe_divide(kodex_open - kodex_close.shift(1), kodex_close.shift(1)),
            "Cash": 0.0,
        },
        index=kodex_close.index,
    ).fillna(0.0)
    ret_oc = pd.DataFrame(
        {
            "KODEX Leverage": safe_divide(lev_close - lev_open, lev_open),
            "KODEX 200": safe_divide(kodex_close - kodex_open, kodex_open),
            "Cash": 0.0,
        },
        index=kodex_close.index,
    ).fillna(0.0)
    nav, executed_weights = backtest_next_open(kodex_close.index, weights, ret_co, ret_oc, fee_rate)
    turnover = executed_weights.drop(columns=["Cash"]).diff().abs().sum(axis=1).fillna(0.0)

    return {
        "long_ma": long_ma,
        "short_ma": short_ma,
        "realized_vol": realized_vol.rename("Realized Volatility"),
        "upside_vol": upside_vol.rename("Upside Volatility"),
        "downside_vol": downside_vol.rename("Downside Volatility"),
        "recent_return": recent_return,
        "pullback": pullback,
        "trend_signal": trend_signal,
        "low_vol_signal": low_vol_signal,
        "bull_signal": bull_signal,
        "high_vol_bull": high_vol_bull,
        "short_trend_ok": short_trend_ok,
        "downside_stress": downside_stress,
        "upside_high_vol_bull": upside_high_vol_bull,
        "stress_high_vol_bull": stress_high_vol_bull,
        "short_ma_rising": short_ma_rising.rename("Short MA Rising"),
        "recent_low": recent_low.rename("Recent Low"),
        "rebound_return": rebound_return,
        "early_reentry": early_reentry,
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

    st.subheader("Core Trend / Vol Signal")
    long_ma_window = st.slider("KODEX 200 long MA", 20, 250, 100, 5)
    vol_window = st.slider("Realized volatility window", 5, 120, 20, 1)
    vol_cap_pct = st.slider("Realized volatility cap (%)", 10, 120, 50, 5)
    vol_source = st.selectbox("Volatility source", ["KODEX 200", "KODEX Leverage"], index=0)
    bull_leverage_pct = st.slider("Bull KODEX Leverage weight (%)", 0, 100, 100, 5)
    st.subheader("High-Vol Bull Split")
    recent_return_window = st.slider("Recent return window (days)", 5, 60, 20, 5)
    drawdown_window = st.slider("Recent high window (days)", 5, 90, 20, 5)
    pullback_threshold_pct = st.slider("Stress pullback threshold (%)", 1, 20, 5, 1)
    downside_vol_ratio = st.slider("Downside stress vol ratio", 0.5, 2.0, 1.0, 0.1)
    upside_high_vol_kodex_pct = st.slider("Upside high-vol KODEX 200 weight (%)", 0, 100, 60, 5)
    upside_high_vol_leverage_pct = st.slider("Upside high-vol KODEX Leverage weight (%)", 0, 100, 25, 5)
    stress_high_vol_kodex_pct = st.slider("Stress high-vol KODEX 200 weight (%)", 0, 100, 50, 5)
    stress_high_vol_leverage_pct = st.slider("Stress high-vol KODEX Leverage weight (%)", 0, 100, 10, 5)
    if upside_high_vol_kodex_pct + upside_high_vol_leverage_pct > 100:
        st.info("Upside high-vol weights exceed 100%, so they will be normalized proportionally.")
    if stress_high_vol_kodex_pct + stress_high_vol_leverage_pct > 100:
        st.info("Stress high-vol weights exceed 100%, so they will be normalized proportionally.")

    st.subheader("Limited Early Reentry")
    early_reentry_on = st.checkbox("Use early reentry below long MA", value=True)
    short_ma_window = st.slider("Early reentry short MA", 5, 60, 20, 5)
    short_ma_slope_window = st.slider("Short MA rising lookback (days)", 2, 20, 5, 1)
    recent_low_window = st.slider("Recent-low window (days)", 10, 60, 20, 5)
    rebound_from_low_pct = st.slider("Minimum rebound from recent low (%)", 1, 20, 5, 1)
    early_kodex_pct = st.slider("Early reentry KODEX 200 weight (%)", 0, 100, 50, 5)
    early_leverage_pct = st.slider("Early reentry KODEX Leverage weight (%)", 0, 50, 20, 5)
    if early_kodex_pct + early_leverage_pct > 100:
        st.info("Early-reentry weights exceed 100%, so they will be normalized proportionally.")
    st.subheader("Execution / Cost")
    execution_model = st.selectbox(
        "Execution model",
        ["Next open", "After-close fill + next-open residual"],
        index=0,
    )
    after_close_fill_pct = st.slider("After-close fixed-price fill rate (%)", 0, 100, 70, 10)
    fee_pct = st.number_input("Trading cost per turnover (%)", min_value=0.0, value=0.03, step=0.01)

    st.subheader("Execution Planner")
    account_state = render_domestic_account_controls(
        {
            "KODEX 200": KODEX_200,
            "KODEX Leverage": KODEX_LEVERAGE,
        },
        "kospi200_bull_bear_aggressive",
        preferred_profile="korea",
    )
    account_value = float(account_state["account_value"])
    current_cash = float(account_state["cash"])
    current_lev_shares = float(account_state["shares"].get(KODEX_LEVERAGE, 0.0))
    current_kodex_shares = float(account_state["shares"].get(KODEX_200, 0.0))

if not run_btn:
    st.info("Run the v5 strategy with high-volatility bull split and optional early reentry below the long moving average.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()
if account_state["source"] == KIWOOM_DOMESTIC_SOURCE and not account_state["snapshot"]:
    st.error("Load the selected Kiwoom domestic account before running the strategy.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup_days = max(long_ma_window, vol_window, recent_low_window, 120) * 3
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

vol_price = kodex_close_full if vol_source == "KODEX 200" else lev_close_full
result = build_v5_high_vol_split_strategy(
    kodex_close_full,
    lev_close_full,
    kodex_200["open"].reindex(full_idx).ffill(),
    kodex_lev["open"].reindex(full_idx).ffill(),
    vol_price,
    long_ma_window,
    vol_window,
    vol_cap_pct / 100,
    bull_leverage_pct / 100,
    upside_high_vol_kodex_pct / 100,
    upside_high_vol_leverage_pct / 100,
    stress_high_vol_kodex_pct / 100,
    stress_high_vol_leverage_pct / 100,
    recent_return_window,
    drawdown_window,
    pullback_threshold_pct / 100,
    downside_vol_ratio,
    early_reentry_on,
    short_ma_window,
    short_ma_slope_window,
    recent_low_window,
    rebound_from_low_pct / 100,
    early_kodex_pct / 100,
    early_leverage_pct / 100,
    fee_pct / 100,
)

progress.progress(80, text="Calculating execution models...")
target_weights = result["weights"].reindex(common_idx).fillna(0.0)
ret_lev_co = safe_divide(kodex_lev["open"] - kodex_lev["close"].shift(1), kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0.0)
ret_lev_oc = safe_divide(kodex_lev["close"] - kodex_lev["open"], kodex_lev["open"]).reindex(common_idx).fillna(0.0)
ret_kodex_co = safe_divide(kodex_200["open"] - kodex_200["close"].shift(1), kodex_200["close"].shift(1)).reindex(common_idx).fillna(0.0)
ret_kodex_oc = safe_divide(kodex_200["close"] - kodex_200["open"], kodex_200["open"]).reindex(common_idx).fillna(0.0)
ret_co = pd.DataFrame({"KODEX Leverage": ret_lev_co, "KODEX 200": ret_kodex_co, "Cash": 0.0}, index=common_idx)
ret_oc = pd.DataFrame({"KODEX Leverage": ret_lev_oc, "KODEX 200": ret_kodex_oc, "Cash": 0.0}, index=common_idx)
fee_rate = fee_pct / 100
nav_next_open, weights_next_open = backtest_next_open(common_idx, target_weights, ret_co, ret_oc, fee_rate)
nav_after_close, weights_after_close = backtest_after_close_fill(
    common_idx, target_weights, ret_co, ret_oc, fee_rate, after_close_fill_pct / 100
)

if execution_model == "Next open":
    nav, weights = nav_next_open, weights_next_open
else:
    nav, weights = nav_after_close, weights_after_close

progress.progress(90, text="Rendering results...")
benchmark_200 = kodex_close_full.reindex(common_idx).ffill()
benchmark_200 = benchmark_200 / benchmark_200.iloc[0]
benchmark_lev = lev_close_full.reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]
progress.empty()

strategy_metrics = calc_metrics(nav)
next_open_metrics = calc_metrics(nav_next_open)
after_close_metrics = calc_metrics(nav_after_close)
benchmark_200_metrics = calc_metrics(benchmark_200)
benchmark_lev_metrics = calc_metrics(benchmark_lev)
latest_date = common_idx[-1]
latest_weights = weights.iloc[-1]
latest_regime = str(result["regime"].reindex(common_idx).iloc[-1])
latest_price = float(kodex_close_full.reindex(common_idx).iloc[-1])
latest_long_ma = float(result["long_ma"].reindex(common_idx).iloc[-1])
latest_short_ma = float(result["short_ma"].reindex(common_idx).iloc[-1])
latest_vol = float(result["realized_vol"].reindex(common_idx).iloc[-1])
target_weights_for_plan = target_weights.iloc[-1]
execution_plan, execution_summary = build_holdings_trade_plan(
    target_weights_for_plan,
    {
        "KODEX Leverage": float(lev_close_full.reindex(common_idx).ffill().iloc[-1]),
        "KODEX 200": float(kodex_close_full.reindex(common_idx).ffill().iloc[-1]),
    },
    {
        "KODEX Leverage": current_lev_shares,
        "KODEX 200": current_kodex_shares,
    },
    current_cash,
    account_value,
)
action_label = "Hold" if execution_summary["total_order_value"] <= 0 else "Rebalance"

st.success(
    f"{action_label} | Target for next open from close signal ({latest_date.date()}): {latest_regime} | "
    f"KODEX 200 {target_weights_for_plan['KODEX 200']:.0%}, "
    f"KODEX Leverage {target_weights_for_plan['KODEX Leverage']:.0%}, "
    f"Cash {target_weights_for_plan['Cash']:.0%}"
)
st.caption(
    f"KODEX 200 {latest_price:,.0f} / MA{long_ma_window} {latest_long_ma:,.0f} / "
    f"MA{short_ma_window} {latest_short_ma:,.0f} | {vol_source} RV{vol_window} {latest_vol:.1%} | "
    f"Execution: {execution_model}"
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"KODEX200 {benchmark_200_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"KODEX200 {benchmark_200_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"KODEX200 {benchmark_200_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

tab_perf, tab_execution, tab_returns, tab_rules, tab_data = st.tabs(
    ["Performance", "Execution", "Monthly / Yearly Returns", "Rules", "Data"]
)

with tab_perf:
    nav_chart = pd.DataFrame(
        {
            "Selected Strategy": nav / nav.iloc[0],
            "Next open": nav_next_open / nav_next_open.iloc[0],
            f"After-close {after_close_fill_pct}%": nav_after_close / nav_after_close.iloc[0],
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
    st.pyplot(
        static_yearly_returns_chart(
            {
                "Strategy": nav,
                "KODEX 200": benchmark_200,
                "KODEX Leverage": benchmark_lev,
            },
            "Yearly Returns",
            height=330,
        ),
        clear_figure=True,
    )
    plot_weights(weights)
    signal_chart = pd.DataFrame(
        {
            "KODEX 200": kodex_close_full.reindex(common_idx),
            f"MA{long_ma_window}": result["long_ma"].reindex(common_idx),
            f"MA{short_ma_window}": result["short_ma"].reindex(common_idx),
        }
    )
    plot_lines(signal_chart, "Long Trend and Early-Reentry Filters", "Price", height=3.0)

    vol_chart = pd.DataFrame(
        {
            f"{vol_source} RV{vol_window}": result["realized_vol"].reindex(common_idx) * 100,
            "Upside RV": result["upside_vol"].reindex(common_idx) * 100,
            "Downside RV": result["downside_vol"].reindex(common_idx) * 100,
            "RV Cap": pd.Series(vol_cap_pct, index=common_idx),
        }
    )
    plot_lines(vol_chart, "Realized Volatility", "%", percent_axis=True, height=2.8)

    diag = pd.DataFrame(
        {
            "Metric": [
                "Exposure Ratio",
                "Leverage Days",
                "KODEX 200 Days",
                "Upside High-Vol Days",
                "Stress High-Vol Days",
                "Early Reentry Days",
                "Cash Days",
                "Turnover Sum",
            ],
            "Value": [
                f"{((weights['KODEX Leverage'] + weights['KODEX 200']) > 0).mean():.1%}",
                f"{int((weights['KODEX Leverage'] > 0).sum()):,}",
                f"{int((weights['KODEX 200'] > 0).sum()):,}",
                f"{int(result['upside_high_vol_bull'].reindex(common_idx).fillna(False).sum()):,}",
                f"{int(result['stress_high_vol_bull'].reindex(common_idx).fillna(False).sum()):,}",
                f"{int(result['early_reentry'].reindex(common_idx).fillna(False).sum()):,}",
                f"{int((weights['Cash'] >= 0.999).sum()):,}",
                f"{weights.drop(columns=['Cash']).diff().abs().sum(axis=1).sum():.1f}",
            ],
        }
    )
    st.dataframe(diag, use_container_width=True, hide_index=True)

with tab_execution:
    render_domestic_account_summary(account_state, execution_summary["effective_value"])
    st.subheader("Next Trade Plan")
    st.caption(
        "The latest close determines target weights for the next regular-session open. "
        "Current Kiwoom holdings are compared with whole-share targets; this page never submits orders."
    )
    exec_cols = st.columns(4)
    exec_cols[0].metric(
        "LEV Target",
        f"{float(target_weights_for_plan.get('KODEX Leverage', 0.0)):.0%}",
    )
    exec_cols[1].metric(
        "KODEX200 Target",
        f"{float(target_weights_for_plan.get('KODEX 200', 0.0)):.0%}",
    )
    exec_cols[2].metric("Target Cash", f"{execution_summary['target_cash']:,.0f} KRW")
    exec_cols[3].metric("Order Value", f"{execution_summary['total_order_value']:,.0f} KRW")
    st.dataframe(
        execution_plan.style.format(
            {
                "Latest Price": "₩{:,.0f}",
                "Target Weight": "{:.1%}",
                "Target Value": "₩{:,.0f}",
                "Target Shares": "{:,.0f}",
                "Current Shares": "{:,.0f}",
                "Order Shares": "{:+,.0f}",
                "Estimated Order Value": "₩{:,.0f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.info(
        "Operational rule: after the close signal is confirmed, execute positive Order Shares as buys "
        "and negative Order Shares as sells at the next regular-session open."
    )

    execution_comparison = pd.DataFrame(
        [
            {"Execution": "Next open", **{k: next_open_metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
            {"Execution": f"After-close {after_close_fill_pct}% + residual", **{k: after_close_metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
        ]
    )
    shown_execution = execution_comparison.rename(
        columns={"total": "Total", "cagr": "CAGR", "mdd": "MDD", "sharpe": "Sharpe", "calmar": "Calmar"}
    )
    for col in ["Total", "CAGR", "MDD"]:
        shown_execution[col] = shown_execution[col].map(lambda value: f"{value:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown_execution[col] = shown_execution[col].map(lambda value: f"{value:.2f}")
    st.subheader("Execution Model Comparison")
    st.dataframe(shown_execution, use_container_width=True, hide_index=True)
    st.caption(
        "Next open executes the full target change at the next trading day's opening price. After-close fill executes the "
        "selected share of the target change at today's official close in the after-hours fixed-price session, then executes "
        "the remaining share at the next opening price."
    )

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
            "kodex_onoff_v5_monthly_returns.csv",
            "text/csv",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "Yearly Returns CSV",
            yearly_returns.to_csv(index=True).encode("utf-8-sig"),
            "kodex_onoff_v5_yearly_returns.csv",
            "text/csv",
            use_container_width=True,
        )

with tab_rules:
    st.markdown(
        f"""
| State | Condition | KODEX 200 | KODEX Leverage | Cash |
|---|---|---:|---:|---:|
| Bull | Price > MA{long_ma_window} and RV{vol_window} < {vol_cap_pct}% | 0% | {bull_leverage_pct}% | {100 - bull_leverage_pct}% |
| Upside high-vol bull | Price > MA{long_ma_window}, RV{vol_window} >= {vol_cap_pct}%, and no downside-stress confirmation | {upside_high_vol_kodex_pct}% | {upside_high_vol_leverage_pct}% | {max(0, 100 - upside_high_vol_kodex_pct - upside_high_vol_leverage_pct)}% |
| Downside-stress bull | Price > MA{long_ma_window}, RV{vol_window} >= {vol_cap_pct}%, and recent return / pullback / downside RV is stressed | {stress_high_vol_kodex_pct}% | {stress_high_vol_leverage_pct}% | {max(0, 100 - stress_high_vol_kodex_pct - stress_high_vol_leverage_pct)}% |
| Bear | Price <= MA{long_ma_window}, without early-reentry confirmation | 0% | 0% | 100% |
| Early reentry | Price <= MA{long_ma_window}, Price > MA{short_ma_window}, MA{short_ma_window} rising, and rebound from {recent_low_window}d low >= {rebound_from_low_pct}% | {early_kodex_pct}% | {early_leverage_pct}% | {max(0, 100 - early_kodex_pct - early_leverage_pct)}% |

**Downside-stress confirmation:** recent {recent_return_window}d return < 0%, or pullback from {drawdown_window}d high <= -{pullback_threshold_pct}%, or downside RV > upside RV x {downside_vol_ratio:.1f}, or short MA trend is not rising.<br>
**Selected execution:** {execution_model}<br>
**After-close fill assumption:** {after_close_fill_pct}%
"""
    )
    st.warning(
        "Early reentry is deliberately limited. If price falls back below the short MA or the rebound condition fails, the allocation returns to cash."
    )

with tab_data:
    recent = pd.DataFrame(
        {
            "Regime": result["regime"].reindex(common_idx),
            "KODEX 200 Weight": weights["KODEX 200"],
            "KODEX Leverage Weight": weights["KODEX Leverage"],
            "Cash Weight": weights["Cash"],
            "KODEX 200": kodex_close_full.reindex(common_idx),
            f"MA{long_ma_window}": result["long_ma"].reindex(common_idx),
            f"MA{short_ma_window}": result["short_ma"].reindex(common_idx),
            f"RV{vol_window}": result["realized_vol"].reindex(common_idx),
            "Upside RV": result["upside_vol"].reindex(common_idx),
            "Downside RV": result["downside_vol"].reindex(common_idx),
            f"{recent_return_window}d Return": result["recent_return"].reindex(common_idx),
            f"{drawdown_window}d Pullback": result["pullback"].reindex(common_idx),
            "Long Trend Signal": result["trend_signal"].reindex(common_idx),
            "Low Vol Signal": result["low_vol_signal"].reindex(common_idx),
            "Short Trend OK": result["short_trend_ok"].reindex(common_idx),
            "Downside Stress": result["downside_stress"].reindex(common_idx),
            "Upside High Vol Bull": result["upside_high_vol_bull"].reindex(common_idx),
            "Stress High Vol Bull": result["stress_high_vol_bull"].reindex(common_idx),
            "Short MA Rising": result["short_ma_rising"].reindex(common_idx),
            "Recent Low": result["recent_low"].reindex(common_idx),
            "Rebound From Recent Low": result["rebound_return"].reindex(common_idx),
            "Early Reentry Signal": result["early_reentry"].reindex(common_idx),
        }
    )
    st.dataframe(recent.tail(100), use_container_width=True)
    st.download_button(
        "Signal CSV",
        recent.to_csv(index=True).encode("utf-8-sig"),
        "kodex_onoff_v5_signal.csv",
        "text/csv",
    )

