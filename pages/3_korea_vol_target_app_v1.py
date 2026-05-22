"""KODEX 200 / KODEX Leverage volatility-target experiment."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252

st.set_page_config(page_title="KODEX Vol Target Experiment", page_icon="KR", layout="wide")
st.title("KODEX 200 / Leverage Volatility Target Experiment")
st.caption("SOXX/SOXL style volatility-target sizing applied to KODEX 200 and KODEX Leverage.")


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def clean_ret(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


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


def trend_signal(price: pd.Series, fast_ma: pd.Series, slow_ma: pd.Series, rule: str) -> pd.Series:
    if rule == "MA Fast > MA Slow":
        return fast_ma > slow_ma
    if rule == "Close > MA Slow":
        return price > slow_ma
    return (price > slow_ma) & (fast_ma > slow_ma)


def beta_to_weights(target_beta: float, lev_cap: float, lev_multiple: float) -> tuple[float, float]:
    target_beta = max(float(target_beta), 0.0)
    lev_multiple = max(float(lev_multiple), 1.01)
    if target_beta <= 1:
        return min(target_beta, 1.0), 0.0
    lev_w = min((target_beta - 1) / (lev_multiple - 1), lev_cap, 1.0)
    base_w = min(max(target_beta - lev_multiple * lev_w, 0.0), 1 - lev_w)
    if base_w + lev_w > 1:
        scale = 1 / (base_w + lev_w)
        base_w *= scale
        lev_w *= scale
    return base_w, lev_w


def rebalance_weights(weights: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "Daily":
        return weights
    out = weights.copy() * 0.0
    current = pd.Series({"KODEX 200": 0.0, "KODEX Leverage": 0.0})
    last_key = None
    for date, row in weights.iterrows():
        key = date.isocalendar()[:2] if frequency == "Weekly" else (date.year, date.month)
        if key != last_key:
            current = row
            last_key = key
        out.loc[date] = current
    return out


def build_weights(
    price: pd.Series,
    bull: pd.Series,
    vol: pd.Series,
    target_vol: float,
    lev_cap: float,
    max_beta: float,
    lev_multiple: float,
    bear_kodex: float,
    rebalance: str,
) -> pd.DataFrame:
    executable_bull = bull.shift(1).fillna(False)
    desired_beta = (target_vol / vol.shift(1).replace(0, np.nan)).clip(0, max_beta).fillna(0.0)
    rows = []
    for date in price.index:
        if bool(executable_bull.loc[date]):
            base_w, lev_w = beta_to_weights(desired_beta.loc[date], lev_cap, lev_multiple)
        else:
            base_w, lev_w = bear_kodex, 0.0
        rows.append({"KODEX 200": base_w, "KODEX Leverage": lev_w})
    return rebalance_weights(pd.DataFrame(rows, index=price.index).clip(0, 1), rebalance)


def backtest(weights: pd.DataFrame, ret_base: pd.Series, ret_lev: pd.Series, cost: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    return clean_ret(weights["KODEX 200"] * ret_base + weights["KODEX Leverage"] * ret_lev - turnover * cost)


def metrics(daily_ret: pd.Series) -> dict[str, object]:
    daily_ret = clean_ret(daily_ret)
    nav = (1 + daily_ret).cumprod()
    years = len(nav) / TRADING_DAYS
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and len(nav) and nav.iloc[-1] > 0 else -1.0
    dd = nav / nav.cummax() - 1
    return {
        "nav": nav,
        "total": nav.iloc[-1] - 1 if len(nav) else 0.0,
        "cagr": cagr,
        "mdd": dd.min() if len(dd) else 0.0,
        "sharpe": daily_ret.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS) if daily_ret.std() > 0 else 0.0,
        "calmar": cagr / abs(dd.min()) if len(dd) and dd.min() < 0 else 0.0,
        "win_m": (nav.resample("M").last().pct_change().dropna() > 0).mean() if len(nav) else 0.0,
        "dd": dd,
    }


def metric_row(name: str, daily_ret: pd.Series, weights: pd.DataFrame | None = None) -> dict[str, object]:
    m = metrics(daily_ret)
    return {
        "Strategy": name,
        "Total": m["total"],
        "CAGR": m["cagr"],
        "MDD": m["mdd"],
        "Sharpe": m["sharpe"],
        "Calmar": m["calmar"],
        "Monthly Win": m["win_m"],
        "Avg KODEX": np.nan if weights is None else weights["KODEX 200"].mean(),
        "Avg Leverage": np.nan if weights is None else weights["KODEX Leverage"].mean(),
        "Max Leverage": np.nan if weights is None else weights["KODEX Leverage"].max(),
    }


with st.sidebar:
    st.header("Settings")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 5, 16))
    with c2:
        end_date = st.date_input("End", datetime.today().date())

    st.subheader("Trend")
    rule = st.selectbox("Rule", ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow + MA Fast > MA Slow"], index=0)
    fast_window = st.slider("Fast MA", 20, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Target")
    vol_window = st.slider("KODEX 200 volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 10, 80, 35, 5) / 100
    lev_cap = st.slider("KODEX Leverage max weight (%)", 0, 100, 50, 5) / 100
    max_beta = st.slider("Max KODEX-equivalent exposure (%)", 50, 250, 150, 5) / 100
    lev_multiple = st.slider("Leverage ETF multiplier", 1.5, 3.0, 2.0, 0.1)

    st.subheader("Bear / Trading")
    bear_kodex = st.slider("Bear-regime KODEX 200 weight (%)", 0, 100, 20, 5) / 100
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], index=0, horizontal=True)
    cost = st.number_input("Trading cost per turnover (%)", min_value=0.0, value=0.03, step=0.01) / 100
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

with st.expander("Experiment Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Original logic | SOXX/SOXL volatility-target strategy |
| Signal asset | KODEX 200 |
| Leveraged asset | KODEX Leverage |
| Bull trend | {rule} |
| Risk sizing | target volatility / KODEX 200 realized volatility |
| Target volatility | {target_vol:.0%} |
| Leverage cap | {lev_cap:.0%} |
| Bear allocation | KODEX 200 {bear_kodex:.0%}, Cash {1 - bear_kodex:.0%} |
"""
    )

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup = max(slow_window, vol_window, 120) * 3
start_str = (start_date - timedelta(days=warmup)).strftime("%Y%m%d")

progress = st.progress(0, text="Loading KRX ETF data...")
kodex = load_krx_ohlcv(KODEX_200, start_str, end_str)
progress.progress(45, text="Loading KODEX Leverage data...")
lev = load_krx_ohlcv(KODEX_LEVERAGE, start_str, end_str)
if kodex.empty or lev.empty:
    st.error("Could not load KODEX ETF data. Check pykrx or KRX data access.")
    st.stop()

common_idx = kodex.index.intersection(lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 200:
    st.error("Not enough data for the selected backtest period.")
    st.stop()

full_idx = common_idx.union(kodex.index[kodex.index < common_idx[0]]).sort_values()
kodex = kodex.reindex(full_idx).ffill()
lev = lev.reindex(full_idx).ffill()

price = kodex["close"]
fast_ma = price.rolling(fast_window).mean()
slow_ma = price.rolling(slow_window).mean()
vol = clean_ret(price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
bull = trend_signal(price, fast_ma, slow_ma, rule)
weights_full = build_weights(price, bull, vol, target_vol, lev_cap, max_beta, lev_multiple, bear_kodex, rebalance)

weights = weights_full.reindex(common_idx).fillna(0.0)
ret_kodex = clean_ret(kodex["open"].shift(-1) / kodex["open"] - 1).reindex(common_idx).fillna(0.0)
ret_lev = clean_ret(lev["open"].shift(-1) / lev["open"] - 1).reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_kodex, ret_lev, cost)
summary = pd.DataFrame(
    [
        metric_row("KODEX Vol Target", strategy_ret, weights),
        metric_row("KODEX 200 100%", ret_kodex),
        metric_row("KODEX Leverage 100%", ret_lev),
        metric_row("KODEX 80% + Leverage 20%", 0.8 * ret_kodex + 0.2 * ret_lev),
        metric_row("KODEX 70% + Leverage 30%", 0.7 * ret_kodex + 0.3 * ret_lev),
    ]
)
progress.empty()

latest_vol = vol.reindex(common_idx).ffill().iloc[-1]
latest_bull = bool(bull.reindex(common_idx).ffill().iloc[-1])
next_base, next_lev = beta_to_weights(min(target_vol / latest_vol, max_beta), lev_cap, lev_multiple) if latest_bull and latest_vol > 0 else (bear_kodex, 0.0)
st.success(
    f"Next-open target from close signal ({common_idx[-1].date()}): "
    f"{'Bull' if latest_bull else 'Bear'} | KODEX 200 {next_base:.1%}, "
    f"KODEX Leverage {next_lev:.1%}, Cash {1 - next_base - next_lev:.1%} | "
    f"KODEX 200 {vol_window}D volatility {latest_vol:.1%}"
)

m = metrics(strategy_ret)
cols = st.columns(6)
cols[0].metric("Total", f"{m['total']:.1%}")
cols[1].metric("CAGR", f"{m['cagr']:.1%}")
cols[2].metric("MDD", f"{m['mdd']:.1%}")
cols[3].metric("Sharpe", f"{m['sharpe']:.2f}")
cols[4].metric("Calmar", f"{m['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{m['win_m']:.1%}")

tabs = st.tabs(["Performance", "Signal / Weights", "Comparison", "Monthly"])
with tabs[0]:
    nav = pd.DataFrame(
        {
            "Strategy": m["nav"],
            "KODEX 200": metrics(ret_kodex)["nav"],
            "KODEX Leverage": metrics(ret_lev)["nav"],
            "80/20": metrics(0.8 * ret_kodex + 0.2 * ret_lev)["nav"],
        }
    )
    st.line_chart(nav)
    st.line_chart(pd.DataFrame({"Strategy DD": m["dd"], "KODEX 200 DD": metrics(ret_kodex)["dd"], "Leverage DD": metrics(ret_lev)["dd"]}))
with tabs[1]:
    st.line_chart(pd.DataFrame({"KODEX 200": price.reindex(common_idx), f"MA{fast_window}": fast_ma.reindex(common_idx), f"MA{slow_window}": slow_ma.reindex(common_idx)}))
    weight_df = weights.copy()
    weight_df["Cash"] = (1 - weight_df.sum(axis=1)).clip(0, 1)
    st.area_chart(weight_df)
    st.dataframe(
        pd.DataFrame(
            {
                "Bull": bull.reindex(common_idx),
                f"Vol{vol_window}": vol.reindex(common_idx),
                "KODEX Weight": weights["KODEX 200"],
                "Leverage Weight": weights["KODEX Leverage"],
            }
        ).tail(40),
        use_container_width=True,
    )
with tabs[2]:
    shown = summary.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg KODEX", "Avg Leverage", "Max Leverage"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown[col] = shown[col].map(lambda x: f"{x:.2f}")
    st.dataframe(shown, use_container_width=True, hide_index=True)
with tabs[3]:
    monthly = m["nav"].resample("M").last().pct_change().dropna()
    table = monthly.to_frame("Return")
    table["Year"] = table.index.year
    table["Month"] = table.index.month
    pivot = table.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly).groupby(monthly.index.year).prod() - 1
    st.dataframe(pivot.applymap(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
