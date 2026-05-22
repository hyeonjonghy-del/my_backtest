"""SOXX / SOXL ON/OFF experiment."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st

TRADING_DAYS = 252
SOXX = "SOXX"
SOXL = "SOXL"

st.set_page_config(page_title="SOXX/SOXL ON-OFF Experiment", page_icon="US", layout="wide")
st.title("SOXX / SOXL ON-OFF Experiment")
st.caption("KODEX ON/OFF + high-volatility fallback logic applied to SOXX and SOXL.")


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def clean_ret(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


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


def build_signal(close: pd.Series, ma_window: int, vol_price: pd.Series, vol_window: int, vol_cap: float) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    ma = close.rolling(ma_window).mean()
    rv = clean_ret(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    trend = close > ma
    leverage_signal = trend & (rv < vol_cap)
    return leverage_signal, trend, ma, rv


def target_weights(
    dates: pd.DatetimeIndex,
    leverage_signal: pd.Series,
    trend: pd.Series,
    rv: pd.Series,
    leverage_weight: float,
    use_fallback: bool,
    fallback_weight: float,
    vol_cap: float,
) -> pd.DataFrame:
    leverage_signal = leverage_signal.reindex(dates).fillna(False)
    trend = trend.reindex(dates).fillna(False)
    rv = rv.reindex(dates)
    high_vol_bull = trend & (~leverage_signal) & (rv >= vol_cap)
    soxl = leverage_signal.astype(float) * leverage_weight
    soxx = high_vol_bull.astype(float) * fallback_weight if use_fallback else pd.Series(0.0, index=dates)
    cash = (1 - soxx - soxl).clip(lower=0.0)
    return pd.DataFrame({"SOXX": soxx.clip(0, 1), "SOXL": soxl.clip(0, 1), "Cash": cash.clip(0, 1)}, index=dates)


def backtest(weights: pd.DataFrame, ret_soxx: pd.Series, ret_soxl: pd.Series, cost: float) -> pd.Series:
    executable = weights.shift(1).fillna(0.0)
    turnover = executable[["SOXX", "SOXL"]].diff().abs().sum(axis=1).fillna(executable[["SOXX", "SOXL"]].abs().sum(axis=1))
    return clean_ret(executable["SOXX"] * ret_soxx + executable["SOXL"] * ret_soxl - turnover * cost)


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
        "win_m": (nav.resample("ME").last().pct_change().dropna() > 0).mean() if len(nav) else 0.0,
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
        "Avg SOXX": np.nan if weights is None else weights["SOXX"].mean(),
        "Avg SOXL": np.nan if weights is None else weights["SOXL"].mean(),
        "Max SOXL": np.nan if weights is None else weights["SOXL"].max(),
    }


with st.sidebar:
    st.header("Settings")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime(2016, 5, 12))
    with c2:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Core Signal")
    ma_window = st.slider("SOXX MA", 20, 250, 100, 5)
    vol_window = st.slider("Realized volatility window", 5, 120, 20, 1)
    vol_cap = st.slider("Realized volatility cap (%)", 10, 150, 60, 5) / 100
    vol_source = st.selectbox("Volatility source", ["SOXX", "SOXL"], index=0)
    use_fallback = st.checkbox("Use high-vol bull fallback", value=True)
    fallback_weight = st.slider("SOXX weight when RV cap fails (%)", 0, 100, 50, 5) / 100

    st.subheader("Position / Cost")
    leverage_weight = st.slider("SOXL weight when signal passes (%)", 0, 100, 100, 5) / 100
    cost = st.number_input("Trading cost per turnover (%)", value=0.25, step=0.01, min_value=0.0) / 100
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

with st.expander("Experiment Rules", expanded=False):
    st.markdown(
        f"""
| Item | Rule |
|---|---|
| Original logic | KODEX ON/OFF + high-volatility fallback |
| Signal asset | SOXX |
| Leveraged asset | SOXL |
| Entry / hold | SOXX close > MA{ma_window} AND {vol_source} RV{vol_window} < {vol_cap:.0%} |
| High-vol bull fallback | {'On' if use_fallback else 'Off'}; hold SOXX {fallback_weight:.0%} when trend passes but RV cap fails |
| Exit | Trend filter fails |
| Execution | Next regular-session open |
"""
    )

if not run_btn:
    st.info("Check the settings in the sidebar, then run the backtest.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

progress = st.progress(0, text="Loading SOXX/SOXL data...")
warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=max(ma_window, vol_window, 120) * 3)
end_dt = datetime.combine(end_date, datetime.min.time())
try:
    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)
    progress.progress(45, text="Loading SOXL data...")
    soxl = load_yahoo_chart(SOXL, warmup_start, end_dt)
except Exception as exc:
    st.error(f"Could not load Yahoo Finance data: {exc}")
    st.stop()

common_idx = soxx.index.intersection(soxl.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 200:
    st.error("Not enough data for the selected backtest period.")
    st.stop()

full_idx = common_idx.union(soxx.index[soxx.index < common_idx[0]]).sort_values()
soxx = soxx.reindex(full_idx).ffill()
soxl = soxl.reindex(full_idx).ffill()

soxx_close = soxx["adjclose"]
soxl_close = soxl["adjclose"]
vol_price = soxx_close if vol_source == "SOXX" else soxl_close
signal, trend, ma, rv = build_signal(soxx_close, ma_window, vol_price, vol_window, vol_cap)
weights = target_weights(common_idx, signal, trend, rv, leverage_weight, use_fallback, fallback_weight, vol_cap)

soxx_adjopen = (soxx["open"] * (soxx["adjclose"] / soxx["close"]).replace([np.inf, -np.inf], np.nan).ffill()).ffill()
soxl_adjopen = (soxl["open"] * (soxl["adjclose"] / soxl["close"]).replace([np.inf, -np.inf], np.nan).ffill()).ffill()
ret_soxx = clean_ret(soxx_adjopen.shift(-1) / soxx_adjopen - 1).reindex(common_idx).fillna(0.0)
ret_soxl = clean_ret(soxl_adjopen.shift(-1) / soxl_adjopen - 1).reindex(common_idx).fillna(0.0)
strategy_ret = backtest(weights, ret_soxx, ret_soxl, cost)
summary = pd.DataFrame(
    [
        metric_row("SOXX ON/OFF", strategy_ret, weights),
        metric_row("SOXX 100%", ret_soxx),
        metric_row("SOXL 100%", ret_soxl),
        metric_row("SOXX 80% + SOXL 20%", 0.8 * ret_soxx + 0.2 * ret_soxl),
        metric_row("SOXX 70% + SOXL 30%", 0.7 * ret_soxx + 0.3 * ret_soxl),
    ]
)
progress.empty()

target = weights.iloc[-1]
latest_vol = rv.reindex(common_idx).iloc[-1]
latest_trend = bool(trend.reindex(common_idx).iloc[-1])
latest_signal = bool(signal.reindex(common_idx).iloc[-1])
st.success(
    f"Next-open target from close signal ({common_idx[-1].date()}): "
    f"SOXX {target['SOXX']:.0%}, SOXL {target['SOXL']:.0%}, Cash {target['Cash']:.0%}"
)
st.caption(
    f"Signal: {'Pass' if latest_signal else 'Wait'} | Trend: {'Pass' if latest_trend else 'Wait'} | "
    f"{vol_source} RV{vol_window} {latest_vol:.1%} / cap {vol_cap:.0%}"
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
            "SOXX": metrics(ret_soxx)["nav"],
            "SOXL": metrics(ret_soxl)["nav"],
            "80/20": metrics(0.8 * ret_soxx + 0.2 * ret_soxl)["nav"],
        }
    )
    st.line_chart(nav)
    st.line_chart(pd.DataFrame({"Strategy DD": m["dd"], "SOXX DD": metrics(ret_soxx)["dd"], "SOXL DD": metrics(ret_soxl)["dd"]}))
with tabs[1]:
    st.line_chart(pd.DataFrame({"SOXX": soxx_close.reindex(common_idx), f"MA{ma_window}": ma.reindex(common_idx)}))
    st.line_chart(pd.DataFrame({f"{vol_source} RV{vol_window}": rv.reindex(common_idx), "Vol Cap": pd.Series(vol_cap, index=common_idx)}))
    st.area_chart(weights[["SOXX", "SOXL", "Cash"]])
    st.dataframe(
        pd.DataFrame(
            {
                "SOXL Signal": signal.reindex(common_idx),
                "Trend Signal": trend.reindex(common_idx),
                f"RV{vol_window}": rv.reindex(common_idx),
                "SOXX Weight": weights["SOXX"],
                "SOXL Weight": weights["SOXL"],
                "Cash Weight": weights["Cash"],
            }
        ).tail(40),
        use_container_width=True,
    )
with tabs[2]:
    shown = summary.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg SOXL", "Max SOXL"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown[col] = shown[col].map(lambda x: f"{x:.2f}")
    st.dataframe(shown, use_container_width=True, hide_index=True)
with tabs[3]:
    monthly = m["nav"].resample("ME").last().pct_change().dropna()
    table = monthly.to_frame("Return")
    table["Year"] = table.index.year
    table["Month"] = table.index.month
    pivot = table.pivot(index="Year", columns="Month", values="Return")
    pivot.columns = [f"{month}M" for month in pivot.columns]
    pivot["Yearly"] = (1 + monthly).groupby(monthly.index.year).prod() - 1
    st.dataframe(pivot.applymap(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
