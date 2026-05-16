"""KODEX 200 / KODEX Leverage ON/OFF strategy v5.

v5 keeps the v3 core idea because it fits the KODEX data better:
- Signal asset: KODEX 200.
- Trading asset: KODEX Leverage.
- Hold KODEX Leverage only when trend and volatility filters pass.
- Hold cash otherwise.

Additions over v3:
- Full-period vs recent-period performance tables.
- Parameter sensitivity table for MA / realized-vol window / volatility cap.
- Whipsaw and trade diagnostics.
- Lightweight static matplotlib charts only.
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
    "ma": "#111827",
    "vol": "#7C3AED",
    "threshold": "#F59E0B",
    "dd": "#B91C1C",
}

st.set_page_config(page_title="KODEX ON/OFF v5", page_icon="KR", layout="wide")
st.title("KODEX 200 / Leverage ON-OFF Strategy v5")
st.caption(
    "Core v3 logic retained: KODEX 200 trend + realized-volatility filter. "
    "v5 adds regime diagnostics, recent-period checks, and parameter sensitivity."
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
    df = normalize_index(df).dropna(how="all")
    return df.where(df > 0)


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
    palette = [COLORS["strategy"], COLORS["kodex200"], COLORS["leverage"], COLORS["ma"], COLORS["vol"], COLORS["threshold"], COLORS["dd"]]
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


def render_yearly_bars(strategy_nav: pd.Series, kodex_nav: pd.Series, lev_nav: pd.Series) -> None:
    yearly = pd.DataFrame(
        {
            "Strategy": (1 + strategy_nav.pct_change().fillna(0)).groupby(strategy_nav.index.year).prod() - 1,
            "KODEX 200": (1 + kodex_nav.pct_change().fillna(0)).groupby(kodex_nav.index.year).prod() - 1,
            "KODEX Leverage": (1 + lev_nav.pct_change().fillna(0)).groupby(lev_nav.index.year).prod() - 1,
        }
    ).dropna(how="all") * 100

    fig, ax = plt.subplots(figsize=(11, 4.0), dpi=120)
    x = np.arange(len(yearly.index))
    width = 0.25
    bars = [("Strategy", COLORS["strategy"], -width), ("KODEX 200", COLORS["kodex200"], 0), ("KODEX Leverage", COLORS["leverage"], width)]
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


def build_signal(close: pd.Series, ma_window: int, vol_price: pd.Series, vol_window: int, vol_threshold: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    ma = close.rolling(ma_window).mean()
    realized_vol = finite_return(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    signal = ((close > ma) & (realized_vol < vol_threshold)).rename("Buy Signal")
    return signal, ma, realized_vol


def backtest_next_open(
    dates: pd.DatetimeIndex,
    signal: pd.Series,
    leverage_weight: float,
    ret_lev_co: pd.Series,
    ret_lev_oc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    prev_weight = 0.0
    nav_rows: list[float] = []
    weight_rows: list[float] = []
    trades: list[dict[str, object]] = []
    executable_weight = signal.shift(1).reindex(dates).fillna(False).astype(float) * leverage_weight

    for i, date in enumerate(dates):
        nav *= 1 + prev_weight * ret_lev_co.loc[date]
        new_weight = float(executable_weight.loc[date])
        turnover = abs(new_weight - prev_weight)
        if i > 0 and turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * turnover, 0.0), 0.99)
            trades.append(
                {
                    "Date": date.date(),
                    "Execution": "Next open",
                    "Old Weight": prev_weight,
                    "New Weight": new_weight,
                    "Turnover": turnover,
                    "Fee Cost": before_fee - nav,
                    "NAV": nav,
                }
            )
        prev_weight = new_weight
        nav *= 1 + prev_weight * ret_lev_oc.loc[date]
        nav_rows.append(nav)
        weight_rows.append(prev_weight)

    return pd.Series(nav_rows, index=dates, name="Strategy"), pd.Series(weight_rows, index=dates, name="Leverage Weight"), pd.DataFrame(trades)


def backtest_after_close_fill(
    dates: pd.DatetimeIndex,
    signal: pd.Series,
    leverage_weight: float,
    ret_lev_co: pd.Series,
    ret_lev_oc: pd.Series,
    fee_rate: float,
    after_close_fill_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    close_weight = 0.0
    open_weight = 0.0
    nav_rows: list[float] = []
    weight_rows: list[float] = []
    trades: list[dict[str, object]] = []
    close_signal_target = signal.reindex(dates).fillna(False).astype(float) * leverage_weight
    open_target = close_signal_target.shift(1).fillna(0.0)

    for i, date in enumerate(dates):
        nav *= 1 + open_weight * ret_lev_co.loc[date]
        intraday_target = float(open_target.loc[date])
        intraday_turnover = abs(intraday_target - open_weight)
        if i > 0 and intraday_turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * intraday_turnover, 0.0), 0.99)
            trades.append(
                {
                    "Date": date.date(),
                    "Execution": "Next open residual",
                    "Old Weight": open_weight,
                    "New Weight": intraday_target,
                    "Turnover": intraday_turnover,
                    "Fee Cost": before_fee - nav,
                    "NAV": nav,
                }
            )
        nav *= 1 + intraday_target * ret_lev_oc.loc[date]

        next_target = float(close_signal_target.loc[date])
        desired_change = next_target - intraday_target
        close_change = desired_change * after_close_fill_rate
        close_weight = intraday_target + close_change
        close_turnover = abs(close_change)
        if close_turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * close_turnover, 0.0), 0.99)
            trades.append(
                {
                    "Date": date.date(),
                    "Execution": "After-close fixed close",
                    "Old Weight": intraday_target,
                    "New Weight": close_weight,
                    "Turnover": close_turnover,
                    "Fee Cost": before_fee - nav,
                    "NAV": nav,
                }
            )

        open_weight = close_weight
        nav_rows.append(nav)
        weight_rows.append(close_weight)

    return pd.Series(nav_rows, index=dates, name="After-close Fill Strategy"), pd.Series(weight_rows, index=dates, name="Leverage Weight"), pd.DataFrame(trades)


def backtest_same_close(
    dates: pd.DatetimeIndex,
    signal: pd.Series,
    leverage_weight: float,
    ret_lev_cc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    prev_weight = 0.0
    nav_rows: list[float] = []
    weight_rows: list[float] = []
    trades: list[dict[str, object]] = []
    target_weight = signal.reindex(dates).fillna(False).astype(float) * leverage_weight

    for date in dates:
        nav *= 1 + prev_weight * ret_lev_cc.loc[date]
        new_weight = float(target_weight.loc[date])
        turnover = abs(new_weight - prev_weight)
        if turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * turnover, 0.0), 0.99)
            trades.append(
                {
                    "Date": date.date(),
                    "Execution": "Ideal same close",
                    "Old Weight": prev_weight,
                    "New Weight": new_weight,
                    "Turnover": turnover,
                    "Fee Cost": before_fee - nav,
                    "NAV": nav,
                }
            )
        prev_weight = new_weight
        nav_rows.append(nav)
        weight_rows.append(prev_weight)

    return pd.Series(nav_rows, index=dates, name="Ideal Same-Close Strategy"), pd.Series(weight_rows, index=dates, name="Leverage Weight"), pd.DataFrame(trades)


def slice_nav(nav: pd.Series, start: pd.Timestamp | None) -> pd.Series:
    out = nav.dropna()
    if start is not None:
        out = out[out.index >= start]
    if len(out) < 2:
        return out
    return out / out.iloc[0]


def metrics_table(rows: list[tuple[str, pd.Series]]) -> pd.DataFrame:
    records = []
    for name, nav in rows:
        m = calc_metrics(nav)
        records.append(
            {
                "Name": name,
                "Total": m["total"],
                "CAGR": m["cagr"],
                "MDD": m["mdd"],
                "Sharpe": m["sharpe"],
                "Calmar": m["calmar"],
                "Monthly Win": m["win_m"],
            }
        )
    return pd.DataFrame(records)


def format_metric_table(df: pd.DataFrame) -> pd.DataFrame:
    shown = df.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.2f}")
    return shown


def trade_diagnostics(weight: pd.Series, trade_log: pd.DataFrame, nav: pd.Series) -> dict[str, object]:
    exposure_days = int((weight > 0).sum())
    total_days = max(len(weight), 1)
    years = max((weight.index[-1] - weight.index[0]).days / 365.25, 1 / 365.25)
    changes = weight.diff().abs().fillna(0)
    round_trips = int((changes > 0).sum() / 2)
    monthly = nav.resample("M").last().pct_change().dropna()
    bad_months = int((monthly < 0).sum())
    return {
        "Exposure Days": exposure_days,
        "Exposure Ratio": exposure_days / total_days,
        "Trades": len(trade_log),
        "Trades / Year": len(trade_log) / years,
        "Approx Round Trips": round_trips,
        "Negative Months": bad_months,
    }


def build_execution_plan(
    target_weight: float,
    after_close_fill_rate: float,
    latest_close: float,
    account_value: float,
    current_shares: float,
    current_cash: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    current_value = current_shares * latest_close
    effective_value = account_value if account_value > 0 else current_value + current_cash
    target_value = effective_value * target_weight
    target_shares = np.floor(target_value / latest_close) if latest_close > 0 else 0.0
    order_shares = target_shares - current_shares
    after_close_order_shares = np.trunc(order_shares * after_close_fill_rate)
    residual_shares = order_shares - after_close_order_shares
    estimated_after_close_value = abs(after_close_order_shares) * latest_close
    estimated_residual_value = abs(residual_shares) * latest_close
    target_cash = effective_value - target_shares * latest_close

    rows = [
        {
            "Step": "After-close fixed-price order",
            "Action": "Buy" if after_close_order_shares > 0 else "Sell" if after_close_order_shares < 0 else "Hold",
            "Shares": after_close_order_shares,
            "Reference Price": latest_close,
            "Estimated Value": estimated_after_close_value,
        },
        {
            "Step": "Next-open residual order",
            "Action": "Buy" if residual_shares > 0 else "Sell" if residual_shares < 0 else "Hold",
            "Shares": residual_shares,
            "Reference Price": latest_close,
            "Estimated Value": estimated_residual_value,
        },
    ]
    summary = {
        "effective_value": effective_value,
        "current_value": current_value,
        "target_weight": target_weight,
        "target_value": target_value,
        "target_shares": target_shares,
        "current_shares": current_shares,
        "total_order_shares": order_shares,
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

    st.subheader("Core v3 Signal")
    ma_window = st.slider("KODEX 200 MA", 20, 250, 100, 5)
    vol_window = st.slider("Realized volatility window", 5, 120, 20, 1)
    vol_threshold_pct = st.slider("Realized volatility cap (%)", 10, 120, 50, 5)
    vol_source = st.selectbox("Volatility source", ["KODEX 200", "KODEX Leverage"], index=0)

    st.subheader("Position / Cost")
    leverage_weight_pct = st.slider("KODEX Leverage weight when signal passes (%)", 0, 100, 100, 5)
    execution_model = st.selectbox(
        "Execution model",
        ["Next open", "After-close fill + next-open residual", "Ideal same-close"],
        index=1,
    )
    after_close_fill_pct = st.slider("After-close fixed-price fill rate (%)", 0, 100, 70, 10)
    fee = st.number_input("Trading cost per turnover (%)", value=0.03, step=0.01, min_value=0.0) / 100

    st.subheader("Execution Planner")
    account_value = st.number_input("Account value (KRW)", min_value=0.0, value=0.0, step=1_000_000.0)
    current_lev_shares = st.number_input("Current KODEX Leverage shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash (KRW)", min_value=0.0, value=0.0, step=1_000_000.0)

    st.subheader("Diagnostics")
    run_sensitivity = st.checkbox("Show sensitivity table", value=True)
    recent_years = st.slider("Recent-period comparison years", 1, 5, 3, 1)
    st.caption("These diagnostics do not change the main strategy result.")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

vol_threshold = vol_threshold_pct / 100
leverage_weight = leverage_weight_pct / 100

with st.expander("Strategy Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Signal asset | KODEX 200 |
| Trading asset | KODEX Leverage |
| Entry / hold | KODEX 200 close > MA{ma_window} AND {vol_source} RV{vol_window} < {vol_threshold_pct}% |
| Exit | Any condition fails |
| Execution | {execution_model} |
| After-close fill assumption | {after_close_fill_pct}% of required trade at same-day close; residual at next open |
| Position | KODEX Leverage {leverage_weight_pct}%, cash {100 - leverage_weight_pct}% |
"""
    )

if not run_btn:
    st.info("Adjust the settings, then run the backtest. v5 keeps the v3 ON/OFF core and adds diagnostics.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup_days = max(ma_window, vol_window, 120) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="Loading data...")
progress.progress(20, text="Loading KODEX 200 data...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(45, text="Loading KODEX Leverage data...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)

if kodex_200.empty or kodex_lev.empty:
    st.error("Could not load KODEX ETF data. Check pykrx or KRX data access.")
    st.stop()

common_idx = kodex_200.index.intersection(kodex_lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 60:
    st.error("Not enough trading-day data for the selected backtest period.")
    st.stop()

full_idx = kodex_200.index.intersection(kodex_lev.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

kodex_close = kodex_200["close"].reindex(full_idx).ffill()
lev_close = kodex_lev["close"].reindex(full_idx).ffill()
vol_price = kodex_close if vol_source == "KODEX 200" else lev_close
signal, ma, realized_vol = build_signal(kodex_close, ma_window, vol_price, vol_window, vol_threshold)

ret_lev_co = safe_divide(kodex_lev["open"] - kodex_lev["close"].shift(1), kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_oc = safe_divide(kodex_lev["close"] - kodex_lev["open"], kodex_lev["open"]).reindex(common_idx).fillna(0)
ret_lev_cc = finite_return(kodex_lev["close"].pct_change()).reindex(common_idx).fillna(0)

progress.progress(75, text="Calculating strategy...")
nav_next_open, weight_next_open, trades_next_open = backtest_next_open(common_idx, signal, leverage_weight, ret_lev_co, ret_lev_oc, fee)
nav_after_close, weight_after_close, trades_after_close = backtest_after_close_fill(
    common_idx,
    signal,
    leverage_weight,
    ret_lev_co,
    ret_lev_oc,
    fee,
    after_close_fill_pct / 100,
)
nav_same_close, weight_same_close, trades_same_close = backtest_same_close(common_idx, signal, leverage_weight, ret_lev_cc, fee)

if execution_model == "Ideal same-close":
    nav_s, weight_s, trade_log = nav_same_close, weight_same_close, trades_same_close
elif execution_model == "After-close fill + next-open residual":
    nav_s, weight_s, trade_log = nav_after_close, weight_after_close, trades_after_close
else:
    nav_s, weight_s, trade_log = nav_next_open, weight_next_open, trades_next_open

benchmark_200 = kodex_200["close"].reindex(common_idx).ffill()
benchmark_200 = benchmark_200 / benchmark_200.iloc[0]
benchmark_lev = kodex_lev["close"].reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]

strategy_metrics = calc_metrics(nav_s)
next_open_metrics = calc_metrics(nav_next_open)
after_close_metrics = calc_metrics(nav_after_close)
same_close_metrics = calc_metrics(nav_same_close)
benchmark_200_metrics = calc_metrics(benchmark_200)
benchmark_lev_metrics = calc_metrics(benchmark_lev)
progress.empty()

current_date = common_idx[-1].date()
latest_signal = bool(signal.reindex(common_idx).iloc[-1])
latest_weight = weight_s.iloc[-1]
latest_close = kodex_close.reindex(common_idx).iloc[-1]
latest_ma = ma.reindex(common_idx).iloc[-1]
latest_vol = realized_vol.reindex(common_idx).iloc[-1]
target_weight_for_plan = float(signal.reindex(common_idx).iloc[-1]) * leverage_weight
execution_plan, execution_summary = build_execution_plan(
    target_weight_for_plan,
    after_close_fill_pct / 100,
    float(kodex_lev["close"].reindex(common_idx).ffill().iloc[-1]),
    account_value,
    current_lev_shares,
    current_cash,
)

st.success(
    f"Current state ({current_date}): {'Hold leverage' if latest_weight > 0 else 'Cash'} | "
    f"KODEX Leverage {latest_weight:.0%}, Cash {1 - latest_weight:.0%}"
)
st.caption(
    f"Latest raw signal: {'Pass' if latest_signal else 'Wait'} | "
    f"KODEX 200 {latest_close:,.0f} / MA{ma_window} {latest_ma:,.0f} / "
    f"{vol_source} RV{vol_window} {latest_vol:.1%} / cap {vol_threshold:.0%}"
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"KODEX200 {benchmark_200_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"LEV {benchmark_lev_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"LEV {benchmark_lev_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"LEV {benchmark_lev_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

diag = trade_diagnostics(weight_s, trade_log, nav_s)
diag_cols = st.columns(6)
diag_cols[0].metric("Trades", f"{diag['Trades']:,}")
diag_cols[1].metric("Trades / Year", f"{diag['Trades / Year']:.1f}")
diag_cols[2].metric("Exposure Ratio", f"{diag['Exposure Ratio']:.1%}")
diag_cols[3].metric("Approx Round Trips", f"{diag['Approx Round Trips']:,}")
diag_cols[4].metric("Negative Months", f"{diag['Negative Months']:,}")
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
            "KODEX 200 B&H": benchmark_200 / benchmark_200.iloc[0],
            "KODEX Leverage B&H": benchmark_lev / benchmark_lev.iloc[0],
        }
    )
    render_static_line(nav_chart, "Cumulative NAV", "NAV", 3.8, False)
    render_yearly_bars(nav_s, benchmark_200, benchmark_lev)

    dd_chart = pd.DataFrame(
        {
            "Strategy DD": strategy_metrics["dd"],
            "KODEX 200 DD": benchmark_200_metrics["dd"],
            "KODEX Leverage DD": benchmark_lev_metrics["dd"],
        }
    ) * 100
    render_static_line(dd_chart, "Drawdown", "%", 3.0, True)

with tab_execution:
    st.subheader("Practical Order Plan")
    st.caption(
        "The plan uses the latest KODEX Leverage close as the reference price. "
        "After-close orders target the fixed closing price; any unfilled portion is planned as next-open residual."
    )
    exec_cols = st.columns(5)
    exec_cols[0].metric("Target Weight", f"{execution_summary['target_weight']:.0%}")
    exec_cols[1].metric("Target Shares", f"{execution_summary['target_shares']:,.0f}")
    exec_cols[2].metric("Current Shares", f"{execution_summary['current_shares']:,.0f}")
    exec_cols[3].metric("Total Order", f"{execution_summary['total_order_shares']:,.0f}")
    exec_cols[4].metric("Target Cash", f"{execution_summary['target_cash']:,.0f} KRW")

    shown_plan = execution_plan.copy()
    shown_plan["Shares"] = shown_plan["Shares"].map(lambda x: f"{x:,.0f}")
    shown_plan["Reference Price"] = shown_plan["Reference Price"].map(lambda x: f"{x:,.0f} KRW")
    shown_plan["Estimated Value"] = shown_plan["Estimated Value"].map(lambda x: f"{x:,.0f} KRW")
    st.dataframe(shown_plan, use_container_width=True, hide_index=True)

    st.info(
        "Operational sequence: calculate the signal after the 15:30 close, place the after-close fixed-price order from 15:40 to 16:00, "
        "then handle any unfilled residual at the next open."
    )

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
                ("KODEX 200", slice_nav(benchmark_200, period_start)),
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
        ma_values = sorted(set([80, 100, 120, ma_window]))
        vol_values = sorted(set([20, 30, vol_window]))
        threshold_values = sorted(set([45, 50, 55, vol_threshold_pct]))
        records = []
        for ma_w in ma_values:
            for vol_w in vol_values:
                for threshold_pct in threshold_values:
                    sig, _, _ = build_signal(kodex_close, ma_w, vol_price, vol_w, threshold_pct / 100)
                    if execution_model == "Ideal same-close":
                        test_nav, test_weight, test_trades = backtest_same_close(common_idx, sig, leverage_weight, ret_lev_cc, fee)
                    elif execution_model == "After-close fill + next-open residual":
                        test_nav, test_weight, test_trades = backtest_after_close_fill(
                            common_idx,
                            sig,
                            leverage_weight,
                            ret_lev_co,
                            ret_lev_oc,
                            fee,
                            after_close_fill_pct / 100,
                        )
                    else:
                        test_nav, test_weight, test_trades = backtest_next_open(common_idx, sig, leverage_weight, ret_lev_co, ret_lev_oc, fee)
                    m = calc_metrics(test_nav)
                    records.append(
                        {
                            "MA": ma_w,
                            "RV Window": vol_w,
                            "RV Cap": threshold_pct / 100,
                            "Execution": execution_model,
                            "CAGR": m["cagr"],
                            "MDD": m["mdd"],
                            "Calmar": m["calmar"],
                            "Sharpe": m["sharpe"],
                            "Total": m["total"],
                            "Exposure": (test_weight > 0).mean(),
                            "Trades": len(test_trades),
                        }
                    )
        sensitivity = pd.DataFrame(records).sort_values(["Calmar", "CAGR"], ascending=False)
        shown = sensitivity.copy()
        for col in ["RV Cap", "CAGR", "MDD", "Total", "Exposure"]:
            shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        for col in ["Calmar", "Sharpe"]:
            shown[col] = shown[col].map(lambda x: f"{x:.2f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)

with tab_signal:
    trend_chart = pd.DataFrame({"KODEX 200": kodex_close.reindex(common_idx), f"MA{ma_window}": ma.reindex(common_idx)})
    render_static_line(trend_chart, "Trend Filter", "Price", 3.2, False)

    vol_chart = pd.DataFrame(
        {
            f"{vol_source} RV{vol_window}": realized_vol.reindex(common_idx) * 100,
            "Vol Cap": pd.Series(vol_threshold_pct, index=common_idx),
        }
    )
    render_static_line(vol_chart, "Annualized Realized Volatility", "%", 3.0, True)

    recent_signal = pd.DataFrame(
        {
            "Raw Signal": signal.reindex(common_idx),
            "Held Leverage Weight": weight_s,
            "KODEX 200": kodex_close.reindex(common_idx),
            f"MA{ma_window}": ma.reindex(common_idx),
            f"RV{vol_window}": realized_vol.reindex(common_idx),
        }
    ).tail(40)
    st.dataframe(recent_signal, use_container_width=True)

with tab_trades:
    if trade_log.empty:
        st.info("No trades in the selected period.")
    else:
        shown = trade_log.copy()
        for col in ["Old Weight", "New Weight", "Turnover"]:
            shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        shown["Fee Cost"] = shown["Fee Cost"].map(lambda x: f"{x:.4f}")
        shown["NAV"] = shown["NAV"].map(lambda x: f"{x:.4f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "kodex_onoff_v5_trades.csv", "text/csv")

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_200 = benchmark_200.resample("M").last().pct_change().dropna()
    monthly_lev = benchmark_lev.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame({"Strategy": monthly_strategy, "KODEX 200": monthly_200, "KODEX Leverage": monthly_lev}).dropna()

    pivot_source = monthly_strategy.to_frame("Return")
    pivot_source["Year"] = pivot_source.index.year
    pivot_source["Month"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    render_static_line(monthly * 100, "Monthly Strategy vs KODEX", "%", 3.0, True)
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "Date"}).to_csv(index=False).encode("utf-8-sig"), "kodex_onoff_v5_monthly.csv", "text/csv")
