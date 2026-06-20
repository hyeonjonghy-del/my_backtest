"""KODEX 200 / KODEX Leverage ON/OFF strategy v1.

v1 keeps the v0 core idea and adds a high-volatility bull fallback:
- Signal asset: KODEX 200.
- Main trading asset: KODEX Leverage.
- Hold KODEX Leverage when trend and volatility filters pass.
- Optionally hold KODEX 200 + cash when trend passes but RV is above the cap.
- Hold cash when the trend filter fails.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from chart_utils import static_area_chart

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

st.set_page_config(page_title="KODEX ON/OFF v1", page_icon="KR", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("KODEX 200 / Leverage ON-OFF Strategy v1")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True, key="run_backtest_top")
st.caption(
    "v1 adds a high-volatility bull fallback: when trend passes but RV exceeds the cap, "
    "the strategy can hold KODEX 200 plus cash instead of moving fully to cash."
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


def build_signal(close: pd.Series, ma_window: int, vol_price: pd.Series, vol_window: int, vol_threshold: float) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    ma = close.rolling(ma_window).mean()
    realized_vol = finite_return(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    trend_signal = (close > ma).rename("Trend Signal")
    low_vol_signal = (realized_vol < vol_threshold).rename("Low Vol Signal")
    signal = (trend_signal & low_vol_signal).rename("Leverage Signal")
    return signal, trend_signal, ma, realized_vol


def build_target_weights(
    dates: pd.DatetimeIndex,
    leverage_signal: pd.Series,
    trend_signal: pd.Series,
    realized_vol: pd.Series,
    leverage_weight: float,
    use_high_vol_fallback: bool,
    high_vol_kodex_weight: float,
    vol_threshold: float,
) -> pd.DataFrame:
    leverage_signal = leverage_signal.reindex(dates).fillna(False)
    trend_signal = trend_signal.reindex(dates).fillna(False)
    realized_vol = realized_vol.reindex(dates)
    high_vol_bull = trend_signal & (~leverage_signal) & (realized_vol >= vol_threshold)

    lev_weight = leverage_signal.astype(float) * leverage_weight
    kodex_weight = pd.Series(0.0, index=dates)
    if use_high_vol_fallback:
        kodex_weight = high_vol_bull.astype(float) * high_vol_kodex_weight
    cash_weight = (1.0 - lev_weight - kodex_weight).clip(lower=0.0)
    return pd.DataFrame(
        {
            "KODEX Leverage": lev_weight.clip(0.0, 1.0),
            "KODEX 200": kodex_weight.clip(0.0, 1.0),
            "Cash": cash_weight.clip(0.0, 1.0),
        },
        index=dates,
    )


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


def portfolio_turnover(new_weights: pd.Series, old_weights: pd.Series) -> float:
    tradable_assets = [asset for asset in new_weights.index if asset != "Cash"]
    return float((new_weights[tradable_assets] - old_weights[tradable_assets]).abs().sum())


def format_allocation(weights: pd.Series) -> str:
    ordered_assets = ["KODEX Leverage", "KODEX 200", "Cash"]
    parts = []
    for asset in ordered_assets:
        if asset in weights.index:
            parts.append(f"{asset} {float(weights[asset]):.0%}")
    return ", ".join(parts)


def portfolio_trade_row(
    date: pd.Timestamp,
    execution: str,
    old_weights: pd.Series,
    new_weights: pd.Series,
    turnover: float,
    fee_cost: float,
    nav: float,
) -> dict[str, object]:
    return {
        "Date": date.date(),
        "Execution": execution,
        "Before Allocation": format_allocation(old_weights),
        "After Allocation": format_allocation(new_weights),
        "Old KODEX Leverage": float(old_weights.get("KODEX Leverage", 0.0)),
        "Old KODEX 200": float(old_weights.get("KODEX 200", 0.0)),
        "Old Cash": float(old_weights.get("Cash", 0.0)),
        "New KODEX Leverage": float(new_weights.get("KODEX Leverage", 0.0)),
        "New KODEX 200": float(new_weights.get("KODEX 200", 0.0)),
        "New Cash": float(new_weights.get("Cash", 0.0)),
        "Old Weight": old_weights.drop("Cash", errors="ignore").sum(),
        "New Weight": new_weights.drop("Cash", errors="ignore").sum(),
        "Turnover": turnover,
        "Fee Cost": fee_cost,
        "NAV": nav,
    }


def backtest_portfolio_next_open(
    dates: pd.DatetimeIndex,
    target_weights: pd.DataFrame,
    ret_co: pd.DataFrame,
    ret_oc: pd.DataFrame,
    fee_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    nav = 1.0
    assets = list(target_weights.columns)
    prev_weights = pd.Series(0.0, index=assets)
    nav_rows: list[float] = []
    weight_rows: list[pd.Series] = []
    trades: list[dict[str, object]] = []
    executable_weights = target_weights.shift(1).reindex(dates).fillna(0.0)

    for i, date in enumerate(dates):
        nav *= 1 + float((prev_weights * ret_co.loc[date, assets]).sum())
        new_weights = executable_weights.loc[date, assets].astype(float)
        turnover = portfolio_turnover(new_weights, prev_weights)
        if i > 0 and turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * turnover, 0.0), 0.99)
            trades.append(
                portfolio_trade_row(date, "Next open", prev_weights, new_weights, turnover, before_fee - nav, nav)
            )
        prev_weights = new_weights
        nav *= 1 + float((prev_weights * ret_oc.loc[date, assets]).sum())
        nav_rows.append(nav)
        weight_rows.append(prev_weights.copy())

    return pd.Series(nav_rows, index=dates, name="Strategy"), pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades)


def backtest_portfolio_after_close_fill(
    dates: pd.DatetimeIndex,
    target_weights: pd.DataFrame,
    ret_co: pd.DataFrame,
    ret_oc: pd.DataFrame,
    fee_rate: float,
    after_close_fill_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    nav = 1.0
    assets = list(target_weights.columns)
    open_weights = pd.Series(0.0, index=assets)
    nav_rows: list[float] = []
    weight_rows: list[pd.Series] = []
    trades: list[dict[str, object]] = []
    open_targets = target_weights.shift(1).reindex(dates).fillna(0.0)

    for i, date in enumerate(dates):
        nav *= 1 + float((open_weights * ret_co.loc[date, assets]).sum())
        intraday_target = open_targets.loc[date, assets].astype(float)
        intraday_turnover = portfolio_turnover(intraday_target, open_weights)
        if i > 0 and intraday_turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * intraday_turnover, 0.0), 0.99)
            trades.append(
                portfolio_trade_row(date, "Next open residual", open_weights, intraday_target, intraday_turnover, before_fee - nav, nav)
            )
        nav *= 1 + float((intraday_target * ret_oc.loc[date, assets]).sum())

        next_target = target_weights.loc[date, assets].astype(float)
        close_weights = intraday_target + (next_target - intraday_target) * after_close_fill_rate
        close_turnover = portfolio_turnover(close_weights, intraday_target)
        if close_turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * close_turnover, 0.0), 0.99)
            trades.append(
                portfolio_trade_row(date, "After-close fixed close", intraday_target, close_weights, close_turnover, before_fee - nav, nav)
            )

        open_weights = close_weights
        nav_rows.append(nav)
        weight_rows.append(close_weights.copy())

    return pd.Series(nav_rows, index=dates, name="After-close Fill Strategy"), pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades)


def backtest_portfolio_same_close(
    dates: pd.DatetimeIndex,
    target_weights: pd.DataFrame,
    ret_cc: pd.DataFrame,
    fee_rate: float,
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    nav = 1.0
    assets = list(target_weights.columns)
    prev_weights = pd.Series(0.0, index=assets)
    nav_rows: list[float] = []
    weight_rows: list[pd.Series] = []
    trades: list[dict[str, object]] = []

    for date in dates:
        nav *= 1 + float((prev_weights * ret_cc.loc[date, assets]).sum())
        new_weights = target_weights.loc[date, assets].astype(float)
        turnover = portfolio_turnover(new_weights, prev_weights)
        if turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * turnover, 0.0), 0.99)
            trades.append(
                portfolio_trade_row(date, "Ideal same close", prev_weights, new_weights, turnover, before_fee - nav, nav)
            )
        prev_weights = new_weights
        nav_rows.append(nav)
        weight_rows.append(prev_weights.copy())

    return pd.Series(nav_rows, index=dates, name="Ideal Same-Close Strategy"), pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades)


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


def trade_diagnostics(weight: pd.Series | pd.DataFrame, trade_log: pd.DataFrame, nav: pd.Series) -> dict[str, object]:
    risky_weight = weight.drop(columns=["Cash"], errors="ignore").sum(axis=1) if isinstance(weight, pd.DataFrame) else weight
    exposure_days = int((risky_weight > 0).sum())
    total_days = max(len(risky_weight), 1)
    years = max((risky_weight.index[-1] - risky_weight.index[0]).days / 365.25, 1 / 365.25)
    changes = risky_weight.diff().abs().fillna(0)
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


def build_multi_asset_execution_plan(
    target_weights: pd.Series,
    after_close_fill_rate: float,
    latest_prices: dict[str, float],
    account_value: float,
    current_shares: dict[str, float],
    current_cash: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    current_values = {asset: current_shares.get(asset, 0.0) * latest_prices[asset] for asset in latest_prices}
    effective_value = account_value if account_value > 0 else sum(current_values.values()) + current_cash
    rows: list[dict[str, object]] = []
    total_order_value = 0.0
    target_cash = effective_value

    for asset, latest_price in latest_prices.items():
        target_weight = float(target_weights.get(asset, 0.0))
        target_value = effective_value * target_weight
        target_shares = np.floor(target_value / latest_price) if latest_price > 0 else 0.0
        order_shares = target_shares - current_shares.get(asset, 0.0)
        after_close_order_shares = np.trunc(order_shares * after_close_fill_rate)
        residual_shares = order_shares - after_close_order_shares
        total_order_value += abs(order_shares) * latest_price
        target_cash -= target_shares * latest_price

        for step, shares in [
            ("After-close fixed-price order", after_close_order_shares),
            ("Next-open residual order", residual_shares),
        ]:
            rows.append(
                {
                    "Asset": asset,
                    "Step": step,
                    "Action": "Buy" if shares > 0 else "Sell" if shares < 0 else "Hold",
                    "Shares": shares,
                    "Reference Price": latest_price,
                    "Estimated Value": abs(shares) * latest_price,
                }
            )

    summary = {
        "effective_value": effective_value,
        "target_leverage_weight": float(target_weights.get("KODEX Leverage", 0.0)),
        "target_kodex_weight": float(target_weights.get("KODEX 200", 0.0)),
        "target_cash_weight": float(target_weights.get("Cash", 0.0)),
        "target_cash": target_cash,
        "total_order_value": total_order_value,
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

    st.subheader("Core Signal")
    ma_window = st.slider("KODEX 200 MA", 20, 250, 100, 5)
    vol_window = st.slider("Realized volatility window", 5, 120, 20, 1)
    vol_threshold_pct = st.slider("Realized volatility cap (%)", 10, 120, 50, 5)
    vol_source = st.selectbox("Volatility source", ["KODEX 200", "KODEX Leverage"], index=0)
    use_high_vol_fallback = st.checkbox("Use RV cap fallback", value=True)
    high_vol_kodex_weight_pct = st.slider("KODEX 200 weight when RV cap fails (%)", 0, 100, 50, 5)

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
    current_kodex_shares = st.number_input("Current KODEX 200 shares", min_value=0.0, value=0.0, step=1.0)
    current_cash = st.number_input("Current cash (KRW)", min_value=0.0, value=0.0, step=1_000_000.0)

    st.subheader("Diagnostics")
    run_sensitivity = st.checkbox("Show sensitivity table", value=False)
    recent_years = st.slider("Recent-period comparison years", 1, 5, 3, 1)
    st.caption("These diagnostics do not change the main strategy result.")

vol_threshold = vol_threshold_pct / 100
leverage_weight = leverage_weight_pct / 100
high_vol_kodex_weight = high_vol_kodex_weight_pct / 100

with st.expander("Strategy Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Signal asset | KODEX 200 |
| Trading asset | KODEX Leverage |
| Entry / hold | KODEX 200 close > MA{ma_window} AND {vol_source} RV{vol_window} < {vol_threshold_pct}% |
| High-vol bull fallback | {'On' if use_high_vol_fallback else 'Off'}; if trend passes but RV cap fails, hold KODEX 200 {high_vol_kodex_weight_pct}%, cash {100 - high_vol_kodex_weight_pct}% |
| Exit | Trend filter fails |
| Execution | {execution_model} |
| After-close fill assumption | {after_close_fill_pct}% of required trade at same-day close; residual at next open |
| Main position | KODEX Leverage {leverage_weight_pct}%, cash {100 - leverage_weight_pct}% |
"""
    )

if not run_btn:
    st.info("Adjust the settings, then run the backtest. v1 adds a configurable RV cap fallback.")
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
signal, trend_signal, ma, realized_vol = build_signal(kodex_close, ma_window, vol_price, vol_window, vol_threshold)
target_weights = build_target_weights(
    common_idx,
    signal,
    trend_signal,
    realized_vol,
    leverage_weight,
    use_high_vol_fallback,
    high_vol_kodex_weight,
    vol_threshold,
)

ret_lev_co = safe_divide(kodex_lev["open"] - kodex_lev["close"].shift(1), kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_oc = safe_divide(kodex_lev["close"] - kodex_lev["open"], kodex_lev["open"]).reindex(common_idx).fillna(0)
ret_lev_cc = finite_return(kodex_lev["close"].pct_change()).reindex(common_idx).fillna(0)
ret_kodex_co = safe_divide(kodex_200["open"] - kodex_200["close"].shift(1), kodex_200["close"].shift(1)).reindex(common_idx).fillna(0)
ret_kodex_oc = safe_divide(kodex_200["close"] - kodex_200["open"], kodex_200["open"]).reindex(common_idx).fillna(0)
ret_kodex_cc = finite_return(kodex_200["close"].pct_change()).reindex(common_idx).fillna(0)
ret_co = pd.DataFrame({"KODEX Leverage": ret_lev_co, "KODEX 200": ret_kodex_co, "Cash": 0.0}, index=common_idx)
ret_oc = pd.DataFrame({"KODEX Leverage": ret_lev_oc, "KODEX 200": ret_kodex_oc, "Cash": 0.0}, index=common_idx)
ret_cc = pd.DataFrame({"KODEX Leverage": ret_lev_cc, "KODEX 200": ret_kodex_cc, "Cash": 0.0}, index=common_idx)

progress.progress(75, text="Calculating strategy...")
nav_next_open, weight_next_open, trades_next_open = backtest_portfolio_next_open(common_idx, target_weights, ret_co, ret_oc, fee)
nav_after_close, weight_after_close, trades_after_close = backtest_portfolio_after_close_fill(
    common_idx, target_weights, ret_co, ret_oc, fee, after_close_fill_pct / 100
)
nav_same_close, weight_same_close, trades_same_close = backtest_portfolio_same_close(common_idx, target_weights, ret_cc, fee)

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
latest_trend_signal = bool(trend_signal.reindex(common_idx).iloc[-1])
latest_weights = weight_s.iloc[-1]
latest_close = kodex_close.reindex(common_idx).iloc[-1]
latest_ma = ma.reindex(common_idx).iloc[-1]
latest_vol = realized_vol.reindex(common_idx).iloc[-1]
target_weights_for_plan = target_weights.iloc[-1]
execution_plan, execution_summary = build_multi_asset_execution_plan(
    target_weights_for_plan,
    after_close_fill_pct / 100,
    {
        "KODEX Leverage": float(kodex_lev["close"].reindex(common_idx).ffill().iloc[-1]),
        "KODEX 200": float(kodex_200["close"].reindex(common_idx).ffill().iloc[-1]),
    },
    account_value,
    {
        "KODEX Leverage": current_lev_shares,
        "KODEX 200": current_kodex_shares,
    },
    current_cash,
)
action_label = "Hold" if execution_summary["total_order_value"] <= 0 else "Rebalance"

st.success(
    f"{action_label} | Current state ({current_date}): "
    f"KODEX Leverage {latest_weights['KODEX Leverage']:.0%}, "
    f"KODEX 200 {latest_weights['KODEX 200']:.0%}, Cash {latest_weights['Cash']:.0%}"
)
st.caption(
    f"Latest leverage signal: {'Pass' if latest_signal else 'Wait'} | "
    f"Trend: {'Pass' if latest_trend_signal else 'Wait'} | "
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

    weight_chart = weight_s[["KODEX Leverage", "KODEX 200", "Cash"]].clip(0.0, 1.0)
    st.pyplot(static_area_chart(weight_chart, "Portfolio Weights", height=300), clear_figure=True)

with tab_execution:
    st.subheader("Practical Order Plan")
    st.caption(
        "The plan uses the latest KODEX Leverage and KODEX 200 closes as reference prices. "
        "After-close orders target the fixed closing price; any unfilled portion is planned as next-open residual."
    )
    exec_cols = st.columns(5)
    exec_cols[0].metric("LEV Target", f"{execution_summary['target_leverage_weight']:.0%}")
    exec_cols[1].metric("KODEX200 Target", f"{execution_summary['target_kodex_weight']:.0%}")
    exec_cols[2].metric("Cash Target", f"{execution_summary['target_cash_weight']:.0%}")
    exec_cols[3].metric("Order Value", f"{execution_summary['total_order_value']:,.0f} KRW")
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
                    sig, trend_sig, _, test_rv = build_signal(kodex_close, ma_w, vol_price, vol_w, threshold_pct / 100)
                    test_targets = build_target_weights(
                        common_idx,
                        sig,
                        trend_sig,
                        test_rv,
                        leverage_weight,
                        use_high_vol_fallback,
                        high_vol_kodex_weight,
                        threshold_pct / 100,
                    )
                    if execution_model == "Ideal same-close":
                        test_nav, test_weight, test_trades = backtest_portfolio_same_close(common_idx, test_targets, ret_cc, fee)
                    elif execution_model == "After-close fill + next-open residual":
                        test_nav, test_weight, test_trades = backtest_portfolio_after_close_fill(common_idx, test_targets, ret_co, ret_oc, fee, after_close_fill_pct / 100)
                    else:
                        test_nav, test_weight, test_trades = backtest_portfolio_next_open(common_idx, test_targets, ret_co, ret_oc, fee)
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
                            "Exposure": (test_weight.drop(columns=["Cash"], errors="ignore").sum(axis=1) > 0).mean(),
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
            "Leverage Signal": signal.reindex(common_idx),
            "Trend Signal": trend_signal.reindex(common_idx),
            "KODEX Leverage Weight": weight_s["KODEX Leverage"],
            "KODEX 200 Weight": weight_s["KODEX 200"],
            "Cash Weight": weight_s["Cash"],
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
        pct_cols = [
            "Old Weight",
            "New Weight",
            "Old KODEX Leverage",
            "Old KODEX 200",
            "Old Cash",
            "New KODEX Leverage",
            "New KODEX 200",
            "New Cash",
            "Turnover",
        ]
        for col in pct_cols:
            if col in shown.columns:
                shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        display_cols = [
            "Date",
            "Execution",
            "Before Allocation",
            "After Allocation",
            "Turnover",
            "Fee Cost",
            "NAV",
        ]
        display_cols += [col for col in shown.columns if col not in display_cols + ["Old Weight", "New Weight"]]
        shown = shown[[col for col in display_cols if col in shown.columns]]
        for col in ["Old Weight", "New Weight"]:
            if col in shown.columns:
                shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        shown["Fee Cost"] = shown["Fee Cost"].map(lambda x: f"{x:.4f}")
        shown["NAV"] = shown["NAV"].map(lambda x: f"{x:.4f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "kodex_onoff_v1_trades.csv", "text/csv")

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
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "Date"}).to_csv(index=False).encode("utf-8-sig"), "kodex_onoff_v1_monthly.csv", "text/csv")
