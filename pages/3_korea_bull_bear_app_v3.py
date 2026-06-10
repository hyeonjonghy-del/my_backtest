"""KODEX 200 / KODEX Leverage ON/OFF strategy v3.

Directional realized-volatility experiment.

Compared with v1, this version separates total, upside, and downside realized
volatility. The default is intentionally conservative: downside volatility is
the main risk filter, while total volatility remains a guardrail. KODEX 200
fallback exposure is allowed only when downside risk is still acceptable.
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
    "cash": "#9CA3AF",
    "total": "#7C3AED",
    "upside": "#059669",
    "downside": "#B91C1C",
    "threshold": "#F59E0B",
}

st.set_page_config(page_title="KODEX ON/OFF v3", page_icon="KR", layout="wide")
st.title("KODEX 200 / Leverage ON-OFF Strategy v3")
st.caption(
    "Directional RV experiment: separate upside volatility from downside volatility. "
    "Fallback exposure is now allowed only when downside risk remains acceptable."
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
    years = max(len(nav) / TRADING_DAYS, 1 / TRADING_DAYS)
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("M").last().pct_change().dropna() > 0).mean()
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win_m": win_m, "dd": dd}


def metrics_frame(rows: list[tuple[str, pd.Series]]) -> pd.DataFrame:
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


def format_metrics(df: pd.DataFrame) -> pd.DataFrame:
    shown = df.copy()
    for col in ["Total", "CAGR", "MDD", "Monthly Win"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.1%}")
    for col in ["Sharpe", "Calmar"]:
        shown[col] = shown[col].map(lambda x: "-" if pd.isna(x) else f"{x:.2f}")
    return shown


def downsample(data: pd.DataFrame, max_points: int = 900) -> pd.DataFrame:
    clean = data.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if len(clean) <= max_points:
        return clean
    return clean.iloc[:: int(np.ceil(len(clean) / max_points))].copy()


def plot_lines(data: pd.DataFrame, title: str, ylabel: str = "", percent_axis: bool = False, height: float = 3.6) -> None:
    clean = downsample(data)
    fig, ax = plt.subplots(figsize=(11, height), dpi=120)
    palette = [COLORS["strategy"], COLORS["kodex200"], COLORS["leverage"], COLORS["total"], COLORS["upside"], COLORS["downside"], COLORS["threshold"]]
    for i, col in enumerate(clean.columns):
        s = clean[col].dropna()
        ax.plot(s.index, s.values, label=str(col), color=palette[i % len(palette)], linewidth=2 if i == 0 else 1.5)
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


def plot_weight_stack(weights: pd.DataFrame) -> None:
    clean = downsample(weights[["KODEX Leverage", "KODEX 200", "Cash"]].clip(0.0, 1.0) * 100)
    fig, ax = plt.subplots(figsize=(11, 3.0), dpi=120)
    ax.stackplot(
        clean.index,
        clean["KODEX Leverage"],
        clean["KODEX 200"],
        clean["Cash"],
        labels=["KODEX Leverage", "KODEX 200", "Cash"],
        colors=[COLORS["leverage"], COLORS["kodex200"], COLORS["cash"]],
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
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    st.pyplot(fig, clear_figure=True)


def build_volatility_profile(price: pd.Series, window: int) -> pd.DataFrame:
    ret = finite_return(price.pct_change())
    total = ret.rolling(window).std() * np.sqrt(TRADING_DAYS)
    upside = ret.where(ret > 0, 0.0).rolling(window).std() * np.sqrt(TRADING_DAYS)
    downside = ret.where(ret < 0, 0.0).rolling(window).std() * np.sqrt(TRADING_DAYS)
    downside_share = safe_divide(downside, total).clip(0.0, 1.0)
    return pd.DataFrame({"Total RV": total, "Upside RV": upside, "Downside RV": downside, "Downside Share": downside_share})


def volatility_signal(profile: pd.DataFrame, mode: str, total_cap: float, downside_cap: float, share_cap: float) -> tuple[pd.Series, pd.Series, str]:
    total_ok = profile["Total RV"] < total_cap
    downside_ok = profile["Downside RV"] < downside_cap
    share_ok = profile["Downside Share"] < share_cap

    if mode == "Total RV":
        return total_ok.rename("Volatility Signal"), profile["Total RV"].rename("Effective RV"), f"Total RV < {total_cap:.0%}"
    if mode == "Downside RV":
        return downside_ok.rename("Volatility Signal"), profile["Downside RV"].rename("Effective RV"), f"Downside RV < {downside_cap:.0%}"
    if mode == "Directional Balance":
        sig = downside_ok & share_ok
        return sig.rename("Volatility Signal"), profile["Downside RV"].rename("Effective RV"), f"Downside RV < {downside_cap:.0%} and downside share < {share_cap:.0%}"

    sig = downside_ok & (total_ok | share_ok)
    return sig.rename("Volatility Signal"), profile["Downside RV"].rename("Effective RV"), f"Downside RV < {downside_cap:.0%} and (Total RV < {total_cap:.0%} or downside share < {share_cap:.0%})"


def build_strategy(
    kodex_close: pd.Series,
    lev_close: pd.Series,
    vol_price: pd.Series,
    ma_window: int,
    vol_window: int,
    vol_mode: str,
    total_cap: float,
    downside_cap: float,
    share_cap: float,
    leverage_weight: float,
    fallback_on: bool,
    fallback_kodex_weight: float,
    fallback_requires_downside_ok: bool,
    fee_rate: float,
) -> dict[str, object]:
    ma = kodex_close.rolling(ma_window).mean()
    trend = (kodex_close > ma).rename("Trend Signal")
    profile = build_volatility_profile(vol_price, vol_window)
    vol_ok, effective_rv, rule = volatility_signal(profile, vol_mode, total_cap, downside_cap, share_cap)
    downside_ok = profile["Downside RV"] < downside_cap
    share_ok = profile["Downside Share"] < share_cap
    fallback_risk_ok = (downside_ok & share_ok) if fallback_requires_downside_ok else effective_rv.notna()

    leverage_signal = (trend & vol_ok).rename("Leverage Signal")
    fallback_signal = trend & (~leverage_signal) & fallback_risk_ok

    weights = pd.DataFrame(index=kodex_close.index)
    weights["KODEX Leverage"] = leverage_signal.astype(float) * leverage_weight
    weights["KODEX 200"] = fallback_signal.astype(float) * fallback_kodex_weight if fallback_on else 0.0
    weights["Cash"] = (1.0 - weights["KODEX Leverage"] - weights["KODEX 200"]).clip(lower=0.0)

    rets = pd.DataFrame(
        {
            "KODEX Leverage": finite_return(lev_close.pct_change()),
            "KODEX 200": finite_return(kodex_close.pct_change()),
            "Cash": 0.0,
        },
        index=kodex_close.index,
    )
    executable = weights.shift(1).fillna(0.0)
    raw_ret = (executable * rets).sum(axis=1)
    turnover = weights.drop(columns=["Cash"]).diff().abs().sum(axis=1).fillna(0.0)
    strategy_ret = raw_ret - turnover.shift(1).fillna(0.0) * fee_rate
    nav = (1 + strategy_ret).cumprod().rename("Strategy")

    return {
        "ma": ma,
        "trend": trend,
        "profile": profile,
        "vol_signal": vol_ok,
        "effective_rv": effective_rv,
        "rule": rule,
        "downside_ok": downside_ok.rename("Downside Risk OK"),
        "fallback_signal": fallback_signal.rename("Fallback Signal"),
        "leverage_signal": leverage_signal,
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

    st.subheader("Core Signal")
    ma_window = st.slider("KODEX 200 MA", 20, 250, 100, 5)
    vol_window = st.slider("RV window", 5, 120, 20, 1)
    vol_mode = st.selectbox("Volatility filter mode", ["Downside + Total Guard", "Downside RV", "Directional Balance", "Total RV"], index=0)
    total_cap_pct = st.slider("Total RV cap (%)", 10, 120, 50, 5)
    downside_cap_pct = st.slider("Downside RV cap (%)", 5, 80, 35, 5)
    downside_share_cap_pct = st.slider("Downside share cap (%)", 30, 100, 70, 5)
    vol_source = st.selectbox("Volatility source", ["KODEX 200", "KODEX Leverage"], index=0)

    st.subheader("Position / Risk")
    leverage_weight_pct = st.slider("KODEX Leverage weight when signal passes (%)", 0, 100, 100, 5)
    fallback_on = st.checkbox("Use KODEX 200 fallback", value=True)
    fallback_weight_pct = st.slider("KODEX 200 fallback weight (%)", 0, 100, 30, 5)
    fallback_requires_downside_ok = st.checkbox("Fallback only when downside risk is OK", value=True)
    fee_pct = st.number_input("Trading cost per turnover (%)", min_value=0.0, value=0.03, step=0.01)
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

if not run_btn:
    st.info("Adjust settings, then run the backtest. The default is conservative: Downside + Total Guard with downside-risk-gated fallback.")
    st.stop()

if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

end_str = end_date.strftime("%Y%m%d")
warmup_days = max(ma_window, vol_window, 120) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="Loading data...")
progress.progress(25, text="Loading KODEX 200 data...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(55, text="Loading KODEX Leverage data...")
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
kodex_close_full = kodex_200["close"].reindex(full_idx).ffill()
lev_close_full = kodex_lev["close"].reindex(full_idx).ffill()
vol_price = kodex_close_full if vol_source == "KODEX 200" else lev_close_full

result = build_strategy(
    kodex_close_full,
    lev_close_full,
    vol_price,
    ma_window,
    vol_window,
    vol_mode,
    total_cap_pct / 100,
    downside_cap_pct / 100,
    downside_share_cap_pct / 100,
    leverage_weight_pct / 100,
    fallback_on,
    fallback_weight_pct / 100,
    fallback_requires_downside_ok,
    fee_pct / 100,
)

progress.progress(90, text="Rendering results...")
nav = result["nav"].reindex(common_idx).dropna()
weights = result["weights"].reindex(common_idx).fillna(0.0)
benchmark_200 = kodex_close_full.reindex(common_idx).ffill()
benchmark_200 = benchmark_200 / benchmark_200.iloc[0]
benchmark_lev = lev_close_full.reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]
progress.empty()

strategy_metrics = calc_metrics(nav)
benchmark_200_metrics = calc_metrics(benchmark_200)
benchmark_lev_metrics = calc_metrics(benchmark_lev)
profile = result["profile"].reindex(common_idx)

latest_date = common_idx[-1]
latest_weights = weights.iloc[-1]
latest_total = profile["Total RV"].iloc[-1]
latest_upside = profile["Upside RV"].iloc[-1]
latest_downside = profile["Downside RV"].iloc[-1]
latest_share = profile["Downside Share"].iloc[-1]
latest_signal = bool(result["leverage_signal"].reindex(common_idx).iloc[-1])
latest_trend = bool(result["trend"].reindex(common_idx).iloc[-1])
latest_vol_signal = bool(result["vol_signal"].reindex(common_idx).iloc[-1])
latest_fallback = bool(result["fallback_signal"].reindex(common_idx).iloc[-1])

st.success(
    f"Current state ({latest_date.date()}): KODEX Leverage {latest_weights['KODEX Leverage']:.0%}, "
    f"KODEX 200 {latest_weights['KODEX 200']:.0%}, Cash {latest_weights['Cash']:.0%}"
)
st.caption(
    f"Leverage: {'Pass' if latest_signal else 'Wait'} | "
    f"Trend: {'Pass' if latest_trend else 'Wait'} | "
    f"Volatility: {'Pass' if latest_vol_signal else 'Wait'} | "
    f"Fallback: {'On' if latest_fallback else 'Off'} | "
    f"Rule: {result['rule']} | "
    f"Total RV {latest_total:.1%}, Upside RV {latest_upside:.1%}, Downside RV {latest_downside:.1%}, Downside share {latest_share:.0%}"
)

cols = st.columns(6)
cols[0].metric("Total Return", f"{strategy_metrics['total']:.1%}", f"KODEX200 {benchmark_200_metrics['total']:.1%}")
cols[1].metric("CAGR", f"{strategy_metrics['cagr']:.1%}", f"LEV {benchmark_lev_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"LEV {benchmark_lev_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"LEV {benchmark_lev_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("Monthly Win", f"{strategy_metrics['win_m']:.1%}")

tab_perf, tab_signal, tab_table = st.tabs(["Performance", "Signal", "Data"])

with tab_perf:
    nav_chart = pd.DataFrame({"Strategy": nav / nav.iloc[0], "KODEX 200 B&H": benchmark_200, "KODEX Leverage B&H": benchmark_lev})
    plot_lines(nav_chart, "Cumulative NAV", "NAV")

    dd_chart = pd.DataFrame({"Strategy DD": strategy_metrics["dd"], "KODEX 200 DD": benchmark_200_metrics["dd"], "KODEX Leverage DD": benchmark_lev_metrics["dd"]}) * 100
    plot_lines(dd_chart, "Drawdown", "%", percent_axis=True, height=3.0)
    plot_weight_stack(weights)

    exposure = weights.drop(columns=["Cash"]).sum(axis=1)
    diag = pd.DataFrame(
        {
            "Metric": ["Exposure Ratio", "Leverage Days", "Fallback Days", "Cash Days", "Turnover Sum"],
            "Value": [
                f"{(exposure > 0).mean():.1%}",
                f"{int((weights['KODEX Leverage'] > 0).sum()):,}",
                f"{int((weights['KODEX 200'] > 0).sum()):,}",
                f"{int((weights['Cash'] >= 0.999).sum()):,}",
                f"{result['turnover'].reindex(common_idx).sum():.1f}",
            ],
        }
    )
    st.dataframe(diag, use_container_width=True, hide_index=True)

    st.subheader("Metric Table")
    st.dataframe(format_metrics(metrics_frame([("Strategy", nav), ("KODEX 200", benchmark_200), ("KODEX Leverage", benchmark_lev)])), use_container_width=True, hide_index=True)

with tab_signal:
    trend_chart = pd.DataFrame({"KODEX 200": kodex_close_full.reindex(common_idx), f"MA{ma_window}": result["ma"].reindex(common_idx)})
    plot_lines(trend_chart, "Trend Filter", "Price", height=3.0)

    vol_chart = pd.DataFrame(
        {
            "Total RV": profile["Total RV"] * 100,
            "Upside RV": profile["Upside RV"] * 100,
            "Downside RV": profile["Downside RV"] * 100,
            "Total Cap": pd.Series(total_cap_pct, index=common_idx),
            "Downside Cap": pd.Series(downside_cap_pct, index=common_idx),
        }
    )
    plot_lines(vol_chart, "Annualized Directional Realized Volatility", "%", percent_axis=True, height=3.2)

    share_chart = pd.DataFrame({"Downside Share": profile["Downside Share"] * 100, "Share Cap": pd.Series(downside_share_cap_pct, index=common_idx)})
    plot_lines(share_chart, "Downside Share of Total RV", "%", percent_axis=True, height=2.8)

with tab_table:
    recent = pd.DataFrame(
        {
            "Leverage Signal": result["leverage_signal"].reindex(common_idx),
            "Fallback Signal": result["fallback_signal"].reindex(common_idx),
            "Trend Signal": result["trend"].reindex(common_idx),
            "Volatility Signal": result["vol_signal"].reindex(common_idx),
            "Downside Risk OK": result["downside_ok"].reindex(common_idx),
            "KODEX Leverage Weight": weights["KODEX Leverage"],
            "KODEX 200 Weight": weights["KODEX 200"],
            "Cash Weight": weights["Cash"],
            "KODEX 200": kodex_close_full.reindex(common_idx),
            f"MA{ma_window}": result["ma"].reindex(common_idx),
            f"Total RV{vol_window}": profile["Total RV"],
            f"Upside RV{vol_window}": profile["Upside RV"],
            f"Downside RV{vol_window}": profile["Downside RV"],
            "Downside Share": profile["Downside Share"],
        }
    )
    st.dataframe(recent.tail(60), use_container_width=True)
    st.download_button("Signal CSV", recent.to_csv(index=True).encode("utf-8-sig"), "kodex_onoff_v3_directional_rv_signal.csv", "text/csv")
