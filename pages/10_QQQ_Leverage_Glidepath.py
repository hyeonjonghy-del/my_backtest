from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

from strategies.qqq_leverage_glidepath import (
    ASSETS,
    GlidepathConfig,
    backtest_next_open,
    latest_target,
)


st.set_page_config(page_title="QQQ Leverage Glidepath", page_icon="📈", layout="wide")
st.title("QQQ / QLD / TQQQ Leverage Glidepath")
st.caption(
    "Completed QQQ close signals are executed at the next regular-session open. "
    "BIL represents the defensive sleeve before SGOV history begins."
)


@st.cache_data(show_spinner=False, ttl=3600)
def load_frame(symbol: str, start: date, end: date) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
    )
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    frame = raw.rename(columns=str.lower)
    required = ["open", "close", "adj close"]
    if frame.empty or any(column not in frame.columns for column in required):
        raise ValueError(f"Unable to load executable {symbol} prices")
    return frame.rename(columns={"adj close": "adjclose"}).loc[:, ["open", "close", "adjclose"]]


with st.sidebar:
    st.header("Backtest settings")
    start_date = st.date_input("Start", value=date(2011, 9, 16))
    end_date = st.date_input("End", value=date.today())
    initial_capital = st.number_input(
        "Initial capital", min_value=1_000_000.0, value=100_000_000.0, step=10_000_000.0
    )
    cost_bps = st.number_input(
        "Cost per traded notional (bps)", min_value=0.0, value=5.0, step=1.0
    )
    run = st.button("Run strategy", type="primary", use_container_width=True)


st.markdown(
    """
| Regime | QQQ | QLD | TQQQ | SGOV sleeve | Nasdaq exposure |
|---|---:|---:|---:|---:|---:|
| Bear | 30% | 0% | 0% | 70% | 0.30x |
| Caution / Turn 1 | 35% | 15% | 0% | 50% | 0.65x |
| Turn 2 | 35% | 25% | 10% | 30% | 1.15x |
| Bull | 33.3% | 33.3% | 33.3% | 0% | 2.00x |
"""
)

if run:
    if start_date >= end_date:
        st.error("End must be later than start.")
        st.stop()
    warmup_start = start_date - timedelta(days=500)
    config = GlidepathConfig(cost_bps_per_traded_notional=float(cost_bps))
    try:
        with st.spinner("Loading prices and running next-open simulation..."):
            frames = {
                symbol: load_frame(symbol, warmup_start, end_date) for symbol in ASSETS
            }
            result, metrics = backtest_next_open(
                frames,
                start=start_date,
                initial_capital=float(initial_capital),
                config=config,
            )
            current = latest_target(frames["QQQ"]["adjclose"], config)
    except Exception as exc:
        st.exception(exc)
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CAGR", f"{metrics['cagr']:.2%}")
    c2.metric("MDD", f"{metrics['max_drawdown']:.2%}")
    c3.metric("Final multiple", f"{metrics['multiple']:.2f}x")
    c4.metric("Regime changes", f"{metrics['regime_changes']:,}")

    st.subheader("Current completed-close signal")
    target = current["target"]
    signal_table = pd.DataFrame(
        {
            "Asset": ["QQQ", "QLD", "TQQQ", "SGOV"],
            "Target": [target[asset] for asset in ("QQQ", "QLD", "TQQQ", "SGOV")],
        }
    )
    left, right = st.columns([1, 2])
    with left:
        st.write(f"Signal date: **{current['signal_date'].date()}**")
        st.write(f"Regime: **{current['regime']}**")
        st.write(f"Effective Nasdaq exposure: **{current['effective_nasdaq_exposure']:.2f}x**")
        st.dataframe(signal_table.style.format({"Target": "{:.1%}"}), hide_index=True)
    with right:
        st.line_chart(result["Wealth"], height=300)

    st.subheader("Allocation and drawdown")
    allocation = result[[f"Weight_{asset}" for asset in ASSETS]].copy()
    allocation["Weight_SGOV_sleeve"] = allocation["Weight_BIL"] + allocation["Weight_SGOV"]
    allocation = allocation[["Weight_QQQ", "Weight_QLD", "Weight_TQQQ", "Weight_SGOV_sleeve"]]
    allocation.columns = ["QQQ", "QLD", "TQQQ", "SGOV sleeve"]
    st.area_chart(allocation, height=280)
    drawdown = result["Wealth"] / result["Wealth"].cummax() - 1.0
    st.line_chart(drawdown, height=240)

    st.subheader("Execution diagnostics")
    st.json(metrics)
    st.download_button(
        "Download daily result CSV",
        result.to_csv().encode("utf-8-sig"),
        file_name="qqq_leverage_glidepath.csv",
        mime="text/csv",
    )

    st.info(
        "The target shown is based on the latest completed QQQ close and is intended "
        "for the next tradable open. Pre-market fills can differ from the official open."
    )
