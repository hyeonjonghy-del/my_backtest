"""KODEX KOSDAQ150 volatility-harvesting strategy v4.

This app uses only two daily ETF OHLCV series:
- KODEX KOSDAQ150: 229200
- KODEX KOSDAQ150 Leverage: 233740

The strategy is a rebalancing model, not a binary trend model. It raises
effective exposure after pullbacks and cuts exposure after rallies, then maps
that target exposure into KODEX KOSDAQ150, KODEX Leverage, and cash.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

warnings.filterwarnings("ignore")

KOSDAQ150 = "229200"
KOSDAQ150_LEV = "233740"
TRADING_DAYS = 252
COLORS = {
    "strategy": "#0F766E",
    "kosdaq": "#2563EB",
    "lev": "#DC2626",
    "mix": "#F59E0B",
    "same": "#111827",
    "vol": "#7C3AED",
    "dd": "#B91C1C",
}

st.set_page_config(page_title="KOSDAQ150 Vol Harvest v4", page_icon="KR", layout="wide")
st.title("KODEX KOSDAQ150 / Leverage Volatility Harvest v4")
st.caption(
    "Daily rebalancing strategy using only KODEX KOSDAQ150 and KODEX KOSDAQ150 Leverage. "
    "It buys pullbacks, trims rallies, and models after-close execution."
)


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def finite_return(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return finite_return(numerator / denominator.where(denominator > 0))


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
    return normalize_index(df).dropna(how="all").where(df > 0)


def calc_metrics(nav: pd.Series) -> dict[str, object]:
    nav = nav.replace([np.inf, -np.inf], np.nan).dropna()
    nav = nav[nav > 0]
    if len(nav) < 2:
        return {"total": 0.0, "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "calmar": 0.0, "win_m": 0.0, "dd": pd.Series(dtype=float)}

    ret = finite_return(nav.pct_change()).dropna()
    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("M").last().pct_change().dropna() > 0).mean()
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win_m": win_m, "dd": dd}


def chart_data(data: pd.DataFrame, max_points: int = 900) -> pd.DataFrame:
    clean = data.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if len(clean) <= max_points:
        return clean
    step = int(np.ceil(len(clean) / max_points))
    return clean.iloc[::step].copy()


def render_static_line(data: pd.DataFrame, title: str, ylabel: str = "", height: float = 3.8, percent_axis: bool = False) -> None:
    clean = chart_data(data)
    fig, ax = plt.subplots(figsize=(11, height), dpi=120)
    palette = [COLORS["strategy"], COLORS["kosdaq"], COLORS["lev"], COLORS["mix"], COLORS["same"], COLORS["vol"], COLORS["dd"]]
    for i, column in enumerate(clean.columns):
        series = clean[column].dropna()
        ax.plot(series.index, series.values, label=str(column), color=palette[i % len(palette)], linewidth=2.0 if i == 0 else 1.5)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", color="#E5E7EB", linewidth=0.8)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", ncols=min(len(clean.columns), 4), frameon=False)
    if percent_axis:
        ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def render_yearly_bars(strategy_nav: pd.Series, kosdaq_nav: pd.Series, lev_nav: pd.Series) -> None:
    yearly = pd.DataFrame(
        {
            "Strategy": (1 + strategy_nav.pct_change().fillna(0)).groupby(strategy_nav.index.year).prod() - 1,
            "KODEX KOSDAQ150": (1 + kosdaq_nav.pct_change().fillna(0)).groupby(kosdaq_nav.index.year).prod() - 1,
            "KODEX Leverage": (1 + lev_nav.pct_change().fillna(0)).groupby(lev_nav.index.year).prod() - 1,
        }
    ).dropna(how="all") * 100

    fig, ax = plt.subplots(figsize=(11, 4.0), dpi=120)
    x = np.arange(len(yearly.index))
    width = 0.25
    for name, color, offset in [
        ("Strategy", COLORS["strategy"], -width),
        ("KODEX KOSDAQ150", COLORS["kosdaq"], 0),
        ("KODEX Leverage", COLORS["lev"], width),
    ]:
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


def exposure_to_weights(target_exposure: pd.Series) -> pd.DataFrame:
    exposure = target_exposure.clip(0.0, 2.0)
    lev_w = (exposure - 1.0).clip(0.0, 1.0)
    kosdaq_w = np.minimum(exposure, 1.0) - lev_w
    cash_w = 1.0 - kosdaq_w - lev_w
    return pd.DataFrame(
        {
            "KODEX KOSDAQ150 Weight": kosdaq_w,
            "KODEX Leverage Weight": lev_w,
            "Cash Weight": cash_w.clip(0.0, 1.0),
            "Equivalent Exposure": exposure,
        }
    )


def apply_rebalance_band(targets: pd.DataFrame, rebalance_band: float) -> pd.DataFrame:
    held_rows: list[pd.Series] = []
    held = pd.Series({"KODEX KOSDAQ150 Weight": 0.0, "KODEX Leverage Weight": 0.0, "Cash Weight": 1.0, "Equivalent Exposure": 0.0})
    for _, row in targets.iterrows():
        diff = abs(row["KODEX KOSDAQ150 Weight"] - held["KODEX KOSDAQ150 Weight"]) + abs(row["KODEX Leverage Weight"] - held["KODEX Leverage Weight"])
        if diff >= rebalance_band or not held_rows:
            held = row.copy()
        held_rows.append(held.copy())
    return pd.DataFrame(held_rows, index=targets.index)


def build_vol_harvest_targets(
    close: pd.Series,
    vol_price: pd.Series,
    anchor_window: int,
    vol_window: int,
    base_exposure: float,
    dip_boost: float,
    rally_trim: float,
    min_exposure: float,
    max_exposure: float,
    stress_ma: int,
    stress_vol_cap: float,
    stress_exposure_cap: float,
    rebalance_band: float,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series]:
    anchor = close.rolling(anchor_window).mean()
    realized_vol = finite_return(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    daily_move = (realized_vol / np.sqrt(TRADING_DAYS)).replace(0, np.nan)
    zscore = ((close / anchor) - 1.0) / (daily_move * np.sqrt(anchor_window))
    zscore = zscore.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-3.0, 3.0)

    pullback = (-zscore).clip(lower=0.0, upper=2.0) / 2.0
    rally = zscore.clip(lower=0.0, upper=2.0) / 2.0
    raw_exposure = base_exposure + dip_boost * pullback - rally_trim * rally
    raw_exposure = raw_exposure.clip(min_exposure, max_exposure)

    stress_line = close.rolling(stress_ma).mean()
    stress = (close < stress_line) & (realized_vol > stress_vol_cap)
    target_exposure = raw_exposure.where(~stress, raw_exposure.clip(upper=stress_exposure_cap))

    targets = exposure_to_weights(target_exposure).fillna(0.0)
    targets = apply_rebalance_band(targets, rebalance_band)
    return targets, anchor, realized_vol, zscore, stress


def turnover_cost(old_weights: pd.Series, new_weights: pd.Series, fee_rate: float) -> float:
    traded = abs(new_weights["KODEX KOSDAQ150 Weight"] - old_weights["KODEX KOSDAQ150 Weight"])
    traded += abs(new_weights["KODEX Leverage Weight"] - old_weights["KODEX Leverage Weight"])
    return min(max(fee_rate * traded, 0.0), 0.99)


def backtest_next_open(
    dates: pd.DatetimeIndex,
    targets: pd.DataFrame,
    ret_1x_co: pd.Series,
    ret_1x_oc: pd.Series,
    ret_lev_co: pd.Series,
    ret_lev_oc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    nav = 1.0
    weights = pd.Series({"KODEX KOSDAQ150 Weight": 0.0, "KODEX Leverage Weight": 0.0, "Cash Weight": 1.0})
    nav_rows: list[float] = []
    weight_rows: list[pd.Series] = []
    trades: list[dict[str, object]] = []
    open_targets = targets.shift(1).reindex(dates).fillna({"KODEX KOSDAQ150 Weight": 0.0, "KODEX Leverage Weight": 0.0, "Cash Weight": 1.0})

    for i, date in enumerate(dates):
        nav *= 1 + weights["KODEX KOSDAQ150 Weight"] * ret_1x_co.loc[date] + weights["KODEX Leverage Weight"] * ret_lev_co.loc[date]
        new_weights = open_targets.loc[date, ["KODEX KOSDAQ150 Weight", "KODEX Leverage Weight", "Cash Weight"]]
        cost = turnover_cost(weights, new_weights, fee_rate)
        if i > 0 and cost > 0:
            before_fee = nav
            nav *= 1 - cost
            trades.append({"Date": date.date(), "Execution": "Next open", "KOSDAQ150 Weight": new_weights.iloc[0], "Leverage Weight": new_weights.iloc[1], "Fee Cost": before_fee - nav, "NAV": nav})
        weights = new_weights
        nav *= 1 + weights["KODEX KOSDAQ150 Weight"] * ret_1x_oc.loc[date] + weights["KODEX Leverage Weight"] * ret_lev_oc.loc[date]
        nav_rows.append(nav)
        weight_rows.append(weights.copy())

    return pd.Series(nav_rows, index=dates, name="Next Open Strategy"), pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades)


def backtest_after_close_fill(
    dates: pd.DatetimeIndex,
    targets: pd.DataFrame,
    ret_1x_co: pd.Series,
    ret_1x_oc: pd.Series,
    ret_lev_co: pd.Series,
    ret_lev_oc: pd.Series,
    fee_rate: float,
    fill_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    nav = 1.0
    weights = pd.Series({"KODEX KOSDAQ150 Weight": 0.0, "KODEX Leverage Weight": 0.0, "Cash Weight": 1.0})
    nav_rows: list[float] = []
    weight_rows: list[pd.Series] = []
    trades: list[dict[str, object]] = []
    open_targets = targets.shift(1).reindex(dates).fillna({"KODEX KOSDAQ150 Weight": 0.0, "KODEX Leverage Weight": 0.0, "Cash Weight": 1.0})
    close_targets = targets.reindex(dates).ffill().fillna(0.0)

    for i, date in enumerate(dates):
        nav *= 1 + weights["KODEX KOSDAQ150 Weight"] * ret_1x_co.loc[date] + weights["KODEX Leverage Weight"] * ret_lev_co.loc[date]
        open_w = open_targets.loc[date, ["KODEX KOSDAQ150 Weight", "KODEX Leverage Weight", "Cash Weight"]]
        open_cost = turnover_cost(weights, open_w, fee_rate)
        if i > 0 and open_cost > 0:
            before_fee = nav
            nav *= 1 - open_cost
            trades.append({"Date": date.date(), "Execution": "Next open residual", "KOSDAQ150 Weight": open_w.iloc[0], "Leverage Weight": open_w.iloc[1], "Fee Cost": before_fee - nav, "NAV": nav})
        weights = open_w
        nav *= 1 + weights["KODEX KOSDAQ150 Weight"] * ret_1x_oc.loc[date] + weights["KODEX Leverage Weight"] * ret_lev_oc.loc[date]

        close_target = close_targets.loc[date, ["KODEX KOSDAQ150 Weight", "KODEX Leverage Weight", "Cash Weight"]]
        close_w = weights + (close_target - weights) * fill_rate
        close_cost = turnover_cost(weights, close_w, fee_rate)
        if close_cost > 0:
            before_fee = nav
            nav *= 1 - close_cost
            trades.append({"Date": date.date(), "Execution": "After-close fixed close", "KOSDAQ150 Weight": close_w.iloc[0], "Leverage Weight": close_w.iloc[1], "Fee Cost": before_fee - nav, "NAV": nav})
        weights = close_w
        nav_rows.append(nav)
        weight_rows.append(weights.copy())

    return pd.Series(nav_rows, index=dates, name="After-close Fill Strategy"), pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades)


def backtest_same_close(
    dates: pd.DatetimeIndex,
    targets: pd.DataFrame,
    ret_1x_cc: pd.Series,
    ret_lev_cc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    nav = 1.0
    weights = pd.Series({"KODEX KOSDAQ150 Weight": 0.0, "KODEX Leverage Weight": 0.0, "Cash Weight": 1.0})
    nav_rows: list[float] = []
    weight_rows: list[pd.Series] = []
    trades: list[dict[str, object]] = []
    close_targets = targets.reindex(dates).ffill().fillna(0.0)

    for date in dates:
        nav *= 1 + weights["KODEX KOSDAQ150 Weight"] * ret_1x_cc.loc[date] + weights["KODEX Leverage Weight"] * ret_lev_cc.loc[date]
        new_weights = close_targets.loc[date, ["KODEX KOSDAQ150 Weight", "KODEX Leverage Weight", "Cash Weight"]]
        cost = turnover_cost(weights, new_weights, fee_rate)
        if cost > 0:
            before_fee = nav
            nav *= 1 - cost
            trades.append({"Date": date.date(), "Execution": "Ideal same close", "KOSDAQ150 Weight": new_weights.iloc[0], "Leverage Weight": new_weights.iloc[1], "Fee Cost": before_fee - nav, "NAV": nav})
        weights = new_weights
        nav_rows.append(nav)
        weight_rows.append(weights.copy())

    return pd.Series(nav_rows, index=dates, name="Ideal Same-Close Strategy"), pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades)


def slice_nav(nav: pd.Series, start: pd.Timestamp | None) -> pd.Series:
    out = nav.dropna()
    if start is not None:
        out = out[out.index >= start]
    return out / out.iloc[0] if len(out) >= 2 else out


def metrics_table(rows: list[tuple[str, pd.Series]]) -> pd.DataFrame:
    records = []
    for name, nav in rows:
        m = calc_metrics(nav)
        records.append({"Name": name, "Total": m["total"], "CAGR": m["cagr"], "MDD": m["mdd"], "Sharpe": m["sharpe"], "Calmar": m["calmar"], "Monthly Win": m["win_m"]})
    return pd.DataFrame(records)


def format_metric_table(df: pd.DataFrame) -> pd.DataFrame:
    shown = df.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.2f}")
    return shown


def build_execution_plan(
    target_weights: pd.Series,
    after_close_fill_rate: float,
    latest_1x_close: float,
    latest_lev_close: float,
    account_value: float,
    current_1x_shares: float,
    current_lev_shares: float,
    current_cash: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    current_value = current_1x_shares * latest_1x_close + current_lev_shares * latest_lev_close
    effective_value = account_value if account_value > 0 else current_value + current_cash
    target_1x_value = effective_value * target_weights["KODEX KOSDAQ150 Weight"]
    target_lev_value = effective_value * target_weights["KODEX Leverage Weight"]
    target_1x_shares = np.floor(target_1x_value / latest_1x_close) if latest_1x_close > 0 else 0.0
    target_lev_shares = np.floor(target_lev_value / latest_lev_close) if latest_lev_close > 0 else 0.0

    rows = []
    for name, price, target_shares, current_shares in [
        ("KODEX KOSDAQ150", latest_1x_close, target_1x_shares, current_1x_shares),
        ("KODEX KOSDAQ150 Leverage", latest_lev_close, target_lev_shares, current_lev_shares),
    ]:
        total_order = target_shares - current_shares
        after_close_order = np.trunc(total_order * after_close_fill_rate)
        residual_order = total_order - after_close_order
        rows.append({"Asset": name, "Step": "After-close fixed-price order", "Action": "Buy" if after_close_order > 0 else "Sell" if after_close_order < 0 else "Hold", "Shares": after_close_order, "Reference Price": price, "Estimated Value": abs(after_close_order) * price})
        rows.append({"Asset": name, "Step": "Next-open residual order", "Action": "Buy" if residual_order > 0 else "Sell" if residual_order < 0 else "Hold", "Shares": residual_order, "Reference Price": price, "Estimated Value": abs(residual_order) * price})

    target_cash = effective_value - target_1x_shares * latest_1x_close - target_lev_shares * latest_lev_close
    summary = {
        "effective_value": effective_value,
        "target_1x_weight": target_weights["KODEX KOSDAQ150 Weight"],
        "target_lev_weight": target_weights["KODEX Leverage Weight"],
        "target_cash_weight": target_weights["Cash Weight"],
        "target_1x_shares": target_1x_shares,
        "target_lev_shares": target_lev_shares,
        "target_cash": target_cash,
    }
    return pd.DataFrame(rows), summary


with st.sidebar:
    st.header("Strategy Settings")
    st.subheader("Period")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 5, 16))
    with c2:
        end_date = st.date_input("End", datetime.today().date())

    st.subheader("Volatility Harvest")
    anchor_window = st.slider("Mean-reversion anchor MA", 10, 180, 40, 5)
    vol_window = st.slider("Realized volatility window", 5, 120, 20, 1)
    vol_source = st.selectbox("Volatility source", ["KODEX KOSDAQ150", "KODEX Leverage"], index=0)
    base_exposure_pct = st.slider("Base equivalent exposure (%)", 0, 200, 100, 5)
    dip_boost_pct = st.slider("Pullback exposure boost (%)", 0, 120, 80, 5)
    rally_trim_pct = st.slider("Rally exposure trim (%)", 0, 120, 70, 5)
    min_exposure_pct = st.slider("Minimum equivalent exposure (%)", 0, 150, 30, 5)
    max_exposure_pct = st.slider("Maximum equivalent exposure (%)", 50, 200, 180, 5)

    st.subheader("Stress / Trading")
    stress_ma = st.slider("Stress MA", 60, 260, 120, 10)
    stress_vol_cap_pct = st.slider("Stress volatility cap (%)", 30, 200, 90, 5)
    stress_exposure_cap_pct = st.slider("Stress exposure cap (%)", 0, 150, 70, 5)
    rebalance_band_pct = st.slider("Rebalance band (%)", 0, 50, 10, 1)

    st.subheader("Execution / Cost")
    execution_model = st.selectbox("Execution model", ["Next open", "After-close fill + next-open residual", "Ideal same-close"], index=1)
    after_close_fill_pct = st.slider("After-close fixed-price fill rate (%)", 0, 100, 60, 10)
    fee = st.number_input("Trading cost per traded weight (%)", value=0.03, step=0.01, min_value=0.0) / 100

    st.subheader("Execution Planner")
    account_value = st.number_input("Account value (KRW)", min_value=0.0, value=0.0, step=1_000_000.0)
    current_1x_shares = st.number_input("Current KODEX KOSDAQ150 shares", min_value=0.0, value=0.0, step=1.0)
    current_lev_shares = st.number_input("Current KODEX Leverage shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash (KRW)", min_value=0.0, value=0.0, step=1_000_000.0)

    st.subheader("Diagnostics")
    run_sensitivity = st.checkbox("Show sensitivity table", value=True)
    recent_years = st.slider("Recent-period comparison years", 1, 5, 3, 1)
    st.caption("Diagnostics do not change the main strategy result.")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

base_exposure = base_exposure_pct / 100
dip_boost = dip_boost_pct / 100
rally_trim = rally_trim_pct / 100
min_exposure = min_exposure_pct / 100
max_exposure = max_exposure_pct / 100
stress_vol_cap = stress_vol_cap_pct / 100
stress_exposure_cap = stress_exposure_cap_pct / 100
rebalance_band = rebalance_band_pct / 100

with st.expander("Strategy Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Data | KODEX KOSDAQ150 and KODEX KOSDAQ150 Leverage daily OHLCV |
| Core idea | Buy more exposure after pullbacks, trim exposure after rallies |
| Anchor | KODEX KOSDAQ150 MA{anchor_window} |
| Base exposure | {base_exposure_pct}% KOSDAQ150-equivalent |
| Exposure range | {min_exposure_pct}% to {max_exposure_pct}% |
| Stress cap | If KOSDAQ150 < MA{stress_ma} and RV{vol_window} > {stress_vol_cap_pct}%, exposure is capped at {stress_exposure_cap_pct}% |
| Position mapping | 0-100% exposure uses KODEX KOSDAQ150/cash; 100-200% exposure mixes KODEX KOSDAQ150 and leverage |
| Execution | {execution_model}; after-close fill assumption {after_close_fill_pct}% |
"""
    )

if not run_btn:
    st.info("Adjust the settings, then run the backtest. Defaults are tuned for volatility harvesting rather than pure trend following.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup_days = max(anchor_window, vol_window, stress_ma, 120) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="Loading data...")
progress.progress(20, text="Loading KODEX KOSDAQ150 data...")
kosdaq = load_krx_ohlcv(KOSDAQ150, extended_start_str, end_str)
progress.progress(45, text="Loading KODEX KOSDAQ150 Leverage data...")
lev = load_krx_ohlcv(KOSDAQ150_LEV, extended_start_str, end_str)

if kosdaq.empty or lev.empty:
    st.error("Could not load KRX ETF data. Check pykrx or KRX data access.")
    st.stop()

common_idx = kosdaq.index.intersection(lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 60:
    st.error("Not enough trading-day data for the selected backtest period.")
    st.stop()

full_idx = kosdaq.index.intersection(lev.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

kosdaq_close = kosdaq["close"].reindex(full_idx).ffill()
lev_close = lev["close"].reindex(full_idx).ffill()
vol_price = kosdaq_close if vol_source == "KODEX KOSDAQ150" else lev_close

progress.progress(65, text="Building target weights...")
targets, anchor, realized_vol, zscore, stress = build_vol_harvest_targets(
    kosdaq_close,
    vol_price,
    anchor_window,
    vol_window,
    base_exposure,
    dip_boost,
    rally_trim,
    min_exposure,
    max_exposure,
    stress_ma,
    stress_vol_cap,
    stress_exposure_cap,
    rebalance_band,
)

ret_1x_co = safe_divide(kosdaq["open"] - kosdaq["close"].shift(1), kosdaq["close"].shift(1)).reindex(common_idx).fillna(0)
ret_1x_oc = safe_divide(kosdaq["close"] - kosdaq["open"], kosdaq["open"]).reindex(common_idx).fillna(0)
ret_1x_cc = finite_return(kosdaq["close"].pct_change()).reindex(common_idx).fillna(0)
ret_lev_co = safe_divide(lev["open"] - lev["close"].shift(1), lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_oc = safe_divide(lev["close"] - lev["open"], lev["open"]).reindex(common_idx).fillna(0)
ret_lev_cc = finite_return(lev["close"].pct_change()).reindex(common_idx).fillna(0)

progress.progress(80, text="Calculating strategy...")
nav_next_open, weights_next_open, trades_next_open = backtest_next_open(common_idx, targets, ret_1x_co, ret_1x_oc, ret_lev_co, ret_lev_oc, fee)
nav_after_close, weights_after_close, trades_after_close = backtest_after_close_fill(
    common_idx, targets, ret_1x_co, ret_1x_oc, ret_lev_co, ret_lev_oc, fee, after_close_fill_pct / 100
)
nav_same_close, weights_same_close, trades_same_close = backtest_same_close(common_idx, targets, ret_1x_cc, ret_lev_cc, fee)

if execution_model == "Ideal same-close":
    nav_s, weights_s, trade_log = nav_same_close, weights_same_close, trades_same_close
elif execution_model == "After-close fill + next-open residual":
    nav_s, weights_s, trade_log = nav_after_close, weights_after_close, trades_after_close
else:
    nav_s, weights_s, trade_log = nav_next_open, weights_next_open, trades_next_open

benchmark_1x = kosdaq["close"].reindex(common_idx).ffill()
benchmark_1x = benchmark_1x / benchmark_1x.iloc[0]
benchmark_lev = lev["close"].reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]
benchmark_mix = 0.5 * benchmark_1x + 0.5 * benchmark_lev

strategy_metrics = calc_metrics(nav_s)
next_open_metrics = calc_metrics(nav_next_open)
after_close_metrics = calc_metrics(nav_after_close)
same_close_metrics = calc_metrics(nav_same_close)
benchmark_1x_metrics = calc_metrics(benchmark_1x)
benchmark_lev_metrics = calc_metrics(benchmark_lev)
progress.empty()

latest_date = common_idx[-1].date()
latest_weights = weights_s.iloc[-1]
latest_target = targets.reindex(common_idx).ffill().iloc[-1]
latest_close = kosdaq_close.reindex(common_idx).iloc[-1]
latest_anchor = anchor.reindex(common_idx).iloc[-1]
latest_vol = realized_vol.reindex(common_idx).iloc[-1]
latest_z = zscore.reindex(common_idx).iloc[-1]
latest_stress = bool(stress.reindex(common_idx).fillna(False).iloc[-1])

execution_plan, execution_summary = build_execution_plan(
    latest_target,
    after_close_fill_pct / 100,
    float(kosdaq["close"].reindex(common_idx).ffill().iloc[-1]),
    float(lev["close"].reindex(common_idx).ffill().iloc[-1]),
    account_value,
    current_1x_shares,
    current_lev_shares,
    current_cash,
)

st.success(
    f"Current state ({latest_date}): KOSDAQ150 {latest_weights['KODEX KOSDAQ150 Weight']:.0%}, "
    f"Leverage {latest_weights['KODEX Leverage Weight']:.0%}, Cash {latest_weights['Cash Weight']:.0%}"
)
st.caption(
    f"Latest signal: KOSDAQ150 {latest_close:,.0f} / anchor MA{anchor_window} {latest_anchor:,.0f} / "
    f"{vol_source} RV{vol_window} {latest_vol:.1%} / z-score {latest_z:.2f} / stress {'ON' if latest_stress else 'OFF'}"
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"KOSDAQ150 {benchmark_1x_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"LEV {benchmark_lev_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"LEV {benchmark_lev_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"LEV {benchmark_lev_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

changes = weights_s[["KODEX KOSDAQ150 Weight", "KODEX Leverage Weight"]].diff().abs().sum(axis=1).fillna(0)
diag_cols = st.columns(6)
diag_cols[0].metric("Trades", f"{len(trade_log):,}")
diag_cols[1].metric("Weight Changes", f"{int((changes > 0).sum()):,}")
diag_cols[2].metric("Avg Exposure", f"{weights_s.eval('`KODEX KOSDAQ150 Weight` + 2 * `KODEX Leverage Weight`').mean():.2f}x")
diag_cols[3].metric("Avg Leverage Weight", f"{weights_s['KODEX Leverage Weight'].mean():.1%}")
diag_cols[4].metric("Stress Days", f"{stress.reindex(common_idx).fillna(False).mean():.1%}")
diag_cols[5].metric("Last Trade", str(trade_log["Date"].iloc[-1]) if len(trade_log) else "-")

exec_comparison = metrics_table(
    [
        ("Next open", nav_next_open),
        (f"After-close {after_close_fill_pct}% + residual", nav_after_close),
        ("Ideal same-close", nav_same_close),
    ]
)

tab_perf, tab_execution, tab_periods, tab_sensitivity, tab_signal, tab_trades, tab_monthly = st.tabs(
    ["Performance", "Execution", "Periods", "Sensitivity", "Signal", "Trades", "Monthly"]
)

with tab_perf:
    nav_chart = pd.DataFrame(
        {
            "Selected Strategy": nav_s / nav_s.iloc[0],
            "Next open": nav_next_open / nav_next_open.iloc[0],
            f"After-close {after_close_fill_pct}%": nav_after_close / nav_after_close.iloc[0],
            "Ideal same-close": nav_same_close / nav_same_close.iloc[0],
            "KODEX KOSDAQ150 B&H": benchmark_1x / benchmark_1x.iloc[0],
            "KODEX Leverage B&H": benchmark_lev / benchmark_lev.iloc[0],
        }
    )
    render_static_line(nav_chart, "Cumulative NAV", "NAV", 3.8, False)
    render_yearly_bars(nav_s, benchmark_1x, benchmark_lev)

    dd_chart = pd.DataFrame(
        {
            "Strategy DD": strategy_metrics["dd"],
            "KODEX KOSDAQ150 DD": benchmark_1x_metrics["dd"],
            "KODEX Leverage DD": benchmark_lev_metrics["dd"],
        }
    ) * 100
    render_static_line(dd_chart, "Drawdown", "%", 3.0, True)

with tab_execution:
    st.subheader("Practical Order Plan")
    st.caption("The plan uses the latest ETF closes as reference prices. After-close orders target fixed close; residual orders are planned for next open.")
    exec_cols = st.columns(6)
    exec_cols[0].metric("Target KOSDAQ150", f"{execution_summary['target_1x_weight']:.0%}")
    exec_cols[1].metric("Target Leverage", f"{execution_summary['target_lev_weight']:.0%}")
    exec_cols[2].metric("Target Cash", f"{execution_summary['target_cash_weight']:.0%}")
    exec_cols[3].metric("KOSDAQ150 Shares", f"{execution_summary['target_1x_shares']:,.0f}")
    exec_cols[4].metric("Leverage Shares", f"{execution_summary['target_lev_shares']:,.0f}")
    exec_cols[5].metric("Cash", f"{execution_summary['target_cash']:,.0f} KRW")

    shown_plan = execution_plan.copy()
    shown_plan["Shares"] = shown_plan["Shares"].map(lambda x: f"{x:,.0f}")
    shown_plan["Reference Price"] = shown_plan["Reference Price"].map(lambda x: f"{x:,.0f} KRW")
    shown_plan["Estimated Value"] = shown_plan["Estimated Value"].map(lambda x: f"{x:,.0f} KRW")
    st.dataframe(shown_plan, use_container_width=True, hide_index=True)

    st.info("Operational sequence: calculate target weights after the 15:30 close, place after-close fixed-price orders from 15:40 to 16:00, then handle residual orders at the next open.")

with tab_periods:
    st.subheader("Execution Model Comparison")
    st.dataframe(format_metric_table(exec_comparison), use_container_width=True, hide_index=True)

    st.subheader("Period Comparison")
    recent_start = common_idx[-1] - pd.DateOffset(years=recent_years)
    post_2025 = pd.Timestamp("2025-01-01")
    period_rows = []
    for period_name, period_start in [("Full", None), (f"Recent {recent_years}Y", recent_start), ("Since 2025", post_2025)]:
        table = metrics_table(
            [
                ("Strategy", slice_nav(nav_s, period_start)),
                ("KODEX KOSDAQ150", slice_nav(benchmark_1x, period_start)),
                ("KODEX Leverage", slice_nav(benchmark_lev, period_start)),
            ]
        )
        table.insert(0, "Period", period_name)
        period_rows.append(table)
    st.dataframe(format_metric_table(pd.concat(period_rows, ignore_index=True)), use_container_width=True, hide_index=True)

with tab_sensitivity:
    if not run_sensitivity:
        st.info("Enable 'Show sensitivity table' in the sidebar to calculate this section. This option does not change the main strategy result.")
    else:
        anchor_values = sorted(set([30, 40, 60, anchor_window]))
        base_values = sorted(set([80, 100, 120, base_exposure_pct]))
        band_values = sorted(set([5, 10, 15, rebalance_band_pct]))
        records = []
        for anchor_w in anchor_values:
            for base_pct in base_values:
                for band_pct in band_values:
                    test_targets, _, _, _, _ = build_vol_harvest_targets(
                        kosdaq_close,
                        vol_price,
                        anchor_w,
                        vol_window,
                        base_pct / 100,
                        dip_boost,
                        rally_trim,
                        min_exposure,
                        max_exposure,
                        stress_ma,
                        stress_vol_cap,
                        stress_exposure_cap,
                        band_pct / 100,
                    )
                    if execution_model == "Ideal same-close":
                        test_nav, test_weights, test_trades = backtest_same_close(common_idx, test_targets, ret_1x_cc, ret_lev_cc, fee)
                    elif execution_model == "After-close fill + next-open residual":
                        test_nav, test_weights, test_trades = backtest_after_close_fill(common_idx, test_targets, ret_1x_co, ret_1x_oc, ret_lev_co, ret_lev_oc, fee, after_close_fill_pct / 100)
                    else:
                        test_nav, test_weights, test_trades = backtest_next_open(common_idx, test_targets, ret_1x_co, ret_1x_oc, ret_lev_co, ret_lev_oc, fee)
                    m = calc_metrics(test_nav)
                    records.append(
                        {
                            "Anchor MA": anchor_w,
                            "Base Exposure": base_pct / 100,
                            "Rebalance Band": band_pct / 100,
                            "CAGR": m["cagr"],
                            "MDD": m["mdd"],
                            "Calmar": m["calmar"],
                            "Sharpe": m["sharpe"],
                            "Total": m["total"],
                            "Avg Exposure": test_weights.eval("`KODEX KOSDAQ150 Weight` + 2 * `KODEX Leverage Weight`").mean(),
                            "Trades": len(test_trades),
                        }
                    )
        sensitivity = pd.DataFrame(records).sort_values(["CAGR", "Calmar"], ascending=False)
        shown = sensitivity.copy()
        for col in ["Base Exposure", "Rebalance Band", "CAGR", "MDD", "Total"]:
            shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        for col in ["Calmar", "Sharpe", "Avg Exposure"]:
            shown[col] = shown[col].map(lambda x: f"{x:.2f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)

with tab_signal:
    price_chart = pd.DataFrame({"KODEX KOSDAQ150": kosdaq_close.reindex(common_idx), f"Anchor MA{anchor_window}": anchor.reindex(common_idx)})
    render_static_line(price_chart, "Mean-Reversion Anchor", "Price", 3.2, False)

    weight_chart = targets.reindex(common_idx).ffill()[["KODEX KOSDAQ150 Weight", "KODEX Leverage Weight", "Cash Weight", "Equivalent Exposure"]] * 100
    render_static_line(weight_chart, "Target Weights and Exposure", "%", 3.2, True)

    z_chart = pd.DataFrame({"Z-score": zscore.reindex(common_idx), "Stress": stress.reindex(common_idx).astype(float)})
    render_static_line(z_chart, "Pullback / Rally Score", "", 3.0, False)

    recent_signal = pd.DataFrame(
        {
            "KODEX KOSDAQ150": kosdaq_close.reindex(common_idx),
            f"Anchor MA{anchor_window}": anchor.reindex(common_idx),
            f"RV{vol_window}": realized_vol.reindex(common_idx),
            "Z-score": zscore.reindex(common_idx),
            "Stress": stress.reindex(common_idx),
            "Target KOSDAQ150": targets.reindex(common_idx)["KODEX KOSDAQ150 Weight"],
            "Target Leverage": targets.reindex(common_idx)["KODEX Leverage Weight"],
            "Target Cash": targets.reindex(common_idx)["Cash Weight"],
        }
    ).tail(40)
    st.dataframe(recent_signal, use_container_width=True)

with tab_trades:
    if trade_log.empty:
        st.info("No trades in the selected period.")
    else:
        shown = trade_log.copy()
        for col in ["KOSDAQ150 Weight", "Leverage Weight"]:
            shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        shown["Fee Cost"] = shown["Fee Cost"].map(lambda x: f"{x:.4f}")
        shown["NAV"] = shown["NAV"].map(lambda x: f"{x:.4f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "kosdaq150_vol_harvest_v4_trades.csv", "text/csv")

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_1x = benchmark_1x.resample("M").last().pct_change().dropna()
    monthly_lev = benchmark_lev.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame({"Strategy": monthly_strategy, "KODEX KOSDAQ150": monthly_1x, "KODEX Leverage": monthly_lev}).dropna()

    pivot_source = monthly_strategy.to_frame("Return")
    pivot_source["Year"] = pivot_source.index.year
    pivot_source["Month"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    render_static_line(monthly * 100, "Monthly Strategy vs KODEX", "%", 3.0, True)
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "Date"}).to_csv(index=False).encode("utf-8-sig"), "kosdaq150_vol_harvest_v4_monthly.csv", "text/csv")
