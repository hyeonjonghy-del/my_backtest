import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pykrx import stock


COL_OPEN = "\uc2dc\uac00"
COL_HIGH = "\uace0\uac00"
COL_LOW = "\uc800\uac00"
COL_CLOSE = "\uc885\uac00"
COL_MCAP = "\uc2dc\uac00\ucd1d\uc561"


st.set_page_config(
    page_title="ChartDoctor Bluechip Backtest v2",
    page_icon="\U0001f4ca",
    layout="wide",
)

st.title("\U0001f4ca 8. ChartDoctor Bluechip Backtest v2")
st.caption(
    "Practical daily-bar execution: past-only universe, next-day entry after trigger, "
    "and end-of-day mark-to-market NAV."
)


with st.sidebar:
    st.header("Settings")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start", datetime(2015, 1, 1))
    with col2:
        end_date = st.date_input("End", datetime(2024, 12, 31))

    min_cap_100m = st.number_input("Minimum market cap (100M KRW)", value=5000, step=1000)
    initial_capital = st.number_input("Initial capital (10K KRW)", value=3000, step=500) * 10000
    max_stocks = st.slider("Annual universe size", 10, 100, 50)

    st.divider()
    st.header("Rules")
    trigger_pct = st.slider("Trigger: next round - x%", 1, 8, 4) / 100
    buy1_pct = st.slider("First buy: previous round + x%", 1, 8, 4) / 100

    buy1_cap_pct = st.slider("First buy size (NAV %)", 3, 20, 5) / 100
    add_drop_pct = st.slider("Add-on drop trigger (%)", 5, 20, 10) / 100
    max_positions = st.slider("Max simultaneous positions", 3, 30, 10)

    target_pct = st.slider("Target return from average cost (%)", 5, 40, 15) / 100
    stoploss_pct = st.slider("Stop loss after 3rd buy (%)", 3, 30, 5) / 100

    st.info(
        f"Max planned allocation per stock\n"
        f"- 1st: {buy1_cap_pct * 100:.0f}% NAV\n"
        f"- 2nd: {buy1_cap_pct * 2 * 100:.0f}% NAV\n"
        f"- 3rd: {buy1_cap_pct * 2 * 100:.0f}% NAV\n"
        f"- Total: {buy1_cap_pct * 5 * 100:.0f}% NAV"
    )

    st.divider()
    st.markdown("**v2 execution assumptions**")
    st.write("- Universe uses the nearest available date on or before Jan 4.")
    st.write("- A trigger can only create a buy order for later trading days.")
    st.write("- If target and stop both touch in one daily bar, stop is assumed first.")
    st.write("- NAV is recorded after all same-day trading actions.")


def get_round_unit(price: float) -> int:
    if price < 5_000:
        return 1_000
    if price < 50_000:
        return 5_000
    if price < 100_000:
        return 10_000
    if price < 500_000:
        return 50_000
    return 100_000


def get_round_numbers(price: float):
    unit = get_round_unit(price)
    prev_round = int(price // unit) * unit
    if prev_round == 0:
        prev_round = unit
    return prev_round, prev_round + unit


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tickers_for_date(date_str: str, min_cap: int, n: int) -> list[str]:
    base_dt = datetime.strptime(date_str, "%Y%m%d")
    for offset in range(10):
        candidate = (base_dt - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            cap_df = stock.get_market_cap(candidate, market="KOSPI")
            if cap_df is None or cap_df.empty or COL_MCAP not in cap_df.columns:
                continue
            filtered = cap_df[cap_df[COL_MCAP] >= min_cap * 1e8]
            filtered = filtered.sort_values(COL_MCAP, ascending=False)
            tickers = filtered.index.tolist()[:n]
            if tickers:
                return tickers
        except Exception:
            continue
    return []


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    try:
        df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker)
    except Exception:
        return ticker


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_kospi_index(start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_index_ohlcv_by_date(start_str, end_str, "1001")
        df.index = pd.to_datetime(df.index)
        return df[COL_CLOSE]
    except Exception:
        return pd.Series(dtype=float)


def run_portfolio_backtest(ticker_dfs: dict, yearly_universe: dict, ticker_names: dict):
    cash = float(initial_capital)
    states = {}
    prev_round_map = {}
    trigger_date_map = {}
    positions = {}
    trades = []
    nav_series = {}

    all_dates = pd.date_range(start=start_date, end=end_date, freq="B")

    def calc_nav(mark_date):
        mtm_value = 0.0
        for ticker, pos in positions.items():
            df = ticker_dfs.get(ticker)
            if df is not None and mark_date in df.index:
                mtm_value += pos["shares"] * df.loc[mark_date, COL_CLOSE]
            else:
                mtm_value += pos["shares"] * pos["avg_cost"]
        return cash + mtm_value

    def reset_signal(ticker):
        states[ticker] = "IDLE"
        prev_round_map.pop(ticker, None)
        trigger_date_map.pop(ticker, None)

    for date in all_dates:
        year = date.year
        sizing_nav = calc_nav(date)

        for ticker in list(positions.keys()):
            df = ticker_dfs.get(ticker)
            if df is None or date not in df.index:
                continue

            row = df.loc[date]
            high, low = row[COL_HIGH], row[COL_LOW]
            pos = positions[ticker]
            avg = pos["avg_cost"]
            target = avg * (1 + target_pct)
            closed = False

            if pos["tranches"] == 3:
                stop = avg * (1 - stoploss_pct)
                if low <= stop:
                    proceeds = stop * pos["shares"]
                    pnl = (stop - avg) * pos["shares"]
                    cash += proceeds
                    trades.append({
                        "ticker": ticker,
                        "name": ticker_names.get(ticker, ticker),
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "first_buy_price": round(pos["buy1_price"]),
                        "avg_cost": round(avg),
                        "exit_price": round(stop),
                        "return": -stoploss_pct,
                        "pnl": pnl,
                        "exit_reason": "Stop after 3rd buy",
                        "tranches": pos["tranches"],
                    })
                    del positions[ticker]
                    reset_signal(ticker)
                    closed = True

            if not closed and high >= target:
                proceeds = target * pos["shares"]
                pnl = (target - avg) * pos["shares"]
                cash += proceeds
                trades.append({
                    "ticker": ticker,
                    "name": ticker_names.get(ticker, ticker),
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "first_buy_price": round(pos["buy1_price"]),
                    "avg_cost": round(avg),
                    "exit_price": round(target),
                    "return": target_pct,
                    "pnl": pnl,
                    "exit_reason": "Target",
                    "tranches": pos["tranches"],
                })
                del positions[ticker]
                reset_signal(ticker)
                closed = True

            if closed:
                continue

            if pos["tranches"] == 1 and pos.get("buy1_price"):
                buy2_price = pos["buy1_price"] * (1 - add_drop_pct)
                if low <= buy2_price:
                    amount = sizing_nav * buy1_cap_pct * 2
                    shares = int(amount / buy2_price)
                    cost = shares * buy2_price
                    if shares > 0 and cash >= cost:
                        cash -= cost
                        total_cost = pos["avg_cost"] * pos["shares"] + cost
                        pos["shares"] += shares
                        pos["avg_cost"] = total_cost / pos["shares"]
                        pos["tranches"] = 2
                        pos["buy2_price"] = buy2_price

            elif pos["tranches"] == 2 and pos.get("buy2_price"):
                buy3_price = pos["buy2_price"] * (1 - add_drop_pct)
                if low <= buy3_price:
                    amount = sizing_nav * buy1_cap_pct * 2
                    shares = int(amount / buy3_price)
                    cost = shares * buy3_price
                    if shares > 0 and cash >= cost:
                        cash -= cost
                        total_cost = pos["avg_cost"] * pos["shares"] + cost
                        pos["shares"] += shares
                        pos["avg_cost"] = total_cost / pos["shares"]
                        pos["tranches"] = 3

        sizing_nav = calc_nav(date)
        open_count = len(positions)

        for ticker in sorted(yearly_universe.get(year, set())):
            if ticker in positions:
                continue
            if open_count >= max_positions:
                break
            df = ticker_dfs.get(ticker)
            if df is None or date not in df.index:
                continue

            row = df.loc[date]
            high, low, close = row[COL_HIGH], row[COL_LOW], row[COL_CLOSE]
            state = states.get(ticker, "IDLE")
            bought = False

            if state == "TRIGGERED" and trigger_date_map.get(ticker) is not None and trigger_date_map[ticker] < date:
                current_prev_round, _ = get_round_numbers(close)
                if current_prev_round != prev_round_map.get(ticker):
                    reset_signal(ticker)
                else:
                    buy1_price = prev_round_map[ticker] * (1 + buy1_pct)
                    if low <= buy1_price:
                        amount = sizing_nav * buy1_cap_pct
                        shares = int(amount / buy1_price)
                        cost = shares * buy1_price
                        if shares > 0 and cash >= cost:
                            cash -= cost
                            positions[ticker] = {
                                "shares": shares,
                                "avg_cost": buy1_price,
                                "tranches": 1,
                                "entry_date": date,
                                "buy1_price": buy1_price,
                                "buy2_price": None,
                            }
                            states[ticker] = "IN_TRADE"
                            trigger_date_map.pop(ticker, None)
                            open_count += 1
                            bought = True

            if not bought and states.get(ticker, "IDLE") == "IDLE":
                prev_round, next_round = get_round_numbers(close)
                if high >= next_round * (1 - trigger_pct):
                    states[ticker] = "TRIGGERED"
                    prev_round_map[ticker] = prev_round
                    trigger_date_map[ticker] = date

        nav_series[date] = calc_nav(date)

    return trades, pd.Series(nav_series)


st.divider()
run_btn = st.button("Run v2 backtest", type="primary", use_container_width=True)

if run_btn:
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    st.info("Building annual universe...")
    yearly_universe = {}
    all_tickers_set = set()
    years = list(range(start_date.year, end_date.year + 1))

    for year in years:
        tickers = fetch_tickers_for_date(f"{year}0104", min_cap_100m, max_stocks)
        yearly_universe[year] = set(tickers)
        all_tickers_set.update(tickers)

    with st.expander("Annual universe", expanded=False):
        rows = []
        for year in years:
            prev = yearly_universe.get(year - 1, set())
            curr = yearly_universe[year]
            rows.append({
                "year": year,
                "count": len(curr),
                "added": len(curr - prev),
                "removed": len(prev - curr),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    all_tickers = sorted(all_tickers_set)
    st.info(f"Downloading OHLCV for {len(all_tickers)} tickers...")

    progress = st.progress(0, text="Downloading data...")
    ticker_dfs = {}
    ticker_names = {}

    for i, ticker in enumerate(all_tickers):
        name = fetch_ticker_name(ticker)
        ticker_names[ticker] = name
        df = fetch_ohlcv(ticker, start_str, end_str)
        if not df.empty and len(df) >= 60:
            ticker_dfs[ticker] = df
        progress.progress((i + 1) / len(all_tickers), text=f"{name} ({ticker}) [{i + 1}/{len(all_tickers)}]")
        time.sleep(0.02)

    progress.empty()

    with st.spinner("Running portfolio simulation..."):
        trades, strat_nav = run_portfolio_backtest(ticker_dfs, yearly_universe, ticker_names)

    if not trades:
        st.warning("No completed trades matched the conditions.")
        st.stop()

    rdf = pd.DataFrame(trades).sort_values("exit_date").reset_index(drop=True)
    rdf["entry_date"] = pd.to_datetime(rdf["entry_date"])
    rdf["exit_date"] = pd.to_datetime(rdf["exit_date"])

    final_nav = strat_nav.iloc[-1]
    total_return = final_nav / initial_capital - 1
    years_n = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 365.25
    cagr = (final_nav / initial_capital) ** (1 / years_n) - 1 if years_n > 0 else 0

    wins = (rdf["return"] > 0).sum()
    win_rate = wins / len(rdf) * 100
    profit_factor = (
        rdf.loc[rdf["pnl"] > 0, "pnl"].sum()
        / (abs(rdf.loc[rdf["pnl"] < 0, "pnl"].sum()) + 1e-9)
    )

    roll_max = strat_nav.expanding().max()
    strategy_mdd = (strat_nav / roll_max - 1).min() * 100

    st.divider()
    st.subheader("v2 Results")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Final NAV", f"{final_nav / 10000:,.0f} man KRW")
    c2.metric("Total return", f"{total_return * 100:.1f}%")
    c3.metric("CAGR", f"{cagr * 100:.1f}%")
    c4.metric("MDD", f"{strategy_mdd:.1f}%")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Trades", f"{len(rdf):,}")
    c6.metric("Win rate", f"{win_rate:.1f}%")
    c7.metric("Profit factor", f"{profit_factor:.2f}")
    c8.metric("Open positions at end", f"{len(strat_nav):,} NAV points")

    kospi = fetch_kospi_index(start_str, end_str)
    ret_fig = go.Figure()
    ret_fig.add_trace(go.Scatter(
        x=strat_nav.index,
        y=(strat_nav / initial_capital - 1) * 100,
        mode="lines",
        name="Strategy v2",
    ))
    if not kospi.empty:
        ret_fig.add_trace(go.Scatter(
            x=kospi.index,
            y=(kospi / kospi.iloc[0] - 1) * 100,
            mode="lines",
            name="KOSPI",
            line=dict(dash="dot"),
        ))
    ret_fig.add_hline(y=0, line_dash="dash", line_color="gray")
    ret_fig.update_layout(title="Cumulative return", yaxis_ticksuffix="%", height=420)
    st.plotly_chart(ret_fig, use_container_width=True)

    tab1, tab2 = st.tabs(["Trades", "Monthly returns"])
    with tab1:
        display = rdf.copy()
        display["return"] = display["return"].map(lambda x: f"{x * 100:.1f}%")
        display["pnl"] = display["pnl"].map(lambda x: f"{x / 10000:,.1f} man KRW")
        display["entry_date"] = display["entry_date"].dt.strftime("%Y-%m-%d")
        display["exit_date"] = display["exit_date"].dt.strftime("%Y-%m-%d")
        st.dataframe(display, use_container_width=True, hide_index=True, height=600)
        st.download_button(
            "Download CSV",
            rdf.to_csv(index=False, encoding="utf-8-sig"),
            "chartdoctor_bluechip_v2_trades.csv",
            "text/csv",
        )

    with tab2:
        monthly_nav = strat_nav.resample("ME").last()
        monthly_ret = monthly_nav.pct_change().dropna() * 100
        monthly_df = pd.DataFrame({
            "year": monthly_ret.index.year,
            "month": monthly_ret.index.month,
            "return": monthly_ret.values,
        })
        if not monthly_df.empty:
            pivot = monthly_df.pivot(index="year", columns="month", values="return")
            pivot.columns = [f"{month}M" for month in pivot.columns]
            st.dataframe(
                pivot.round(2).style.format("{:.1f}%", na_rep="-").background_gradient(cmap="RdYlGn", axis=None),
                use_container_width=True,
            )
