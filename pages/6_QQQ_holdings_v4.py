"""QQQ-only core and asymmetric-recovery holdings backtest v4.

Derived from the repository's page 6 holdings strategy.
This version trades QQQ only, holds residual capital as cash, uses whole shares,
and applies close-derived signals at the next regular-session open.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from kiwoom_account import KIWOOM_SOURCE, render_account_controls, render_account_summary


TRADING_DAYS = 252
SYMBOL = "QQQ"

st.set_page_config(page_title="QQQ Holdings Backtest V4", page_icon="📈", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("QQQ Core + Asymmetric Recovery Strategy V4")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

st.caption(
    "QQQ 핵심 보유분을 유지하고 하락 시 천천히 축소하되 반등 시 70%→90%→100%로 빠르게 복귀합니다. "
    "종가 신호를 다음 거래일 시가에 정수 주식으로 실행하고 잔여 금액은 현금으로 유지합니다."
)


def normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


@st.cache_data(show_spinner=False, ttl=3600)
def load_yahoo_chart(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    period1 = int(datetime.combine(start_dt.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(
        datetime.combine((end_dt + timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc).timestamp()
    )
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)

    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    frame = pd.DataFrame(
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
    return normalize_index(frame).dropna(subset=["adjclose"])


def build_trend_signal(price: pd.Series, fast_ma: pd.Series, slow_ma: pd.Series, rule: str) -> pd.Series:
    if rule == "MA Fast > MA Slow":
        return fast_ma > slow_ma
    if rule == "Close > MA Slow":
        return price > slow_ma
    return (price > slow_ma) & (fast_ma > slow_ma)


def build_regime_signal(
    trend_signal: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    vol: pd.Series,
    strong_spread: float,
    weak_vol_cutoff: float,
    lag_for_execution: bool = True,
) -> pd.Series:
    lag = 1 if lag_for_execution else 0
    bull = trend_signal.shift(lag).fillna(False)
    ma_spread = (fast_ma / slow_ma - 1).shift(lag).replace([np.inf, -np.inf], np.nan)
    signal_vol = vol.shift(lag)
    strong = bull & (ma_spread >= strong_spread) & (signal_vol <= weak_vol_cutoff)
    regime = pd.Series("Bear", index=trend_signal.index, dtype="object")
    regime.loc[bull & ~strong] = "Weak Bull"
    regime.loc[strong] = "Strong Bull"
    return regime


def build_turnaround_signal(
    price: pd.Series,
    fast_ma: pd.Series,
    slow_ma: pd.Series,
    drawdown_trigger: float,
    exit_fast_window: int,
    exit_slow_window: int,
    exit_confirm_days: int,
    lag_for_execution: bool = True,
) -> pd.Series:
    price_dd = price / price.cummax() - 1
    golden_cross = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
    exit_signal = price.rolling(exit_fast_window).mean() < price.rolling(exit_slow_window).mean()
    active = pd.Series(False, index=price.index)
    armed = False
    in_turnaround = False
    exit_count = 0

    for date in price.index:
        if pd.notna(price_dd.loc[date]) and price_dd.loc[date] <= -drawdown_trigger:
            armed = True
        if bool(golden_cross.loc[date]) and armed:
            in_turnaround = True
            armed = False
            exit_count = 0
        if in_turnaround:
            active.loc[date] = True
            exit_count = exit_count + 1 if bool(exit_signal.loc[date]) else 0
            if exit_count >= exit_confirm_days:
                active.loc[date] = False
                in_turnaround = False
                exit_count = 0

    return active.shift(1).fillna(False) if lag_for_execution else active.fillna(False)


def rebalance_weights(weights: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "Daily":
        return weights
    out = weights.copy() * 0.0
    current = pd.Series({SYMBOL: 0.0})
    last_key = None
    for date, row in weights.iterrows():
        key = date.isocalendar()[:2] if frequency == "Weekly" else (date.year, date.month)
        if key != last_key:
            current = row
            last_key = key
        out.loc[date] = current
    return out


def build_asymmetric_weights(
    price: pd.Series,
    ma20: pd.Series,
    ma30: pd.Series,
    ma50: pd.Series,
    ma200: pd.Series,
    vol: pd.Series,
    drawdown: pd.Series,
    core_weight: float,
    panic_weight: float,
    weak_bull_weight: float,
    panic_volatility: float,
    strong_spread: float,
    recovery_trigger: float,
    rebalance: str,
    lag_for_execution: bool = True,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build core/panic weights with fast staged recovery and no look-ahead."""
    lag = 1 if lag_for_execution else 0
    signal_price = price.shift(lag)
    signal_ma20 = ma20.shift(lag)
    signal_ma50 = ma50.shift(lag)
    signal_ma200 = ma200.shift(lag)
    signal_vol = vol.shift(lag)
    signal_dd = drawdown.shift(lag)
    ma20_up = (ma20 > ma20.shift(5)).shift(lag).eq(True)
    above20_2d = ((price > ma20) & (price.shift(1) > ma20.shift(1))).shift(lag).eq(True)
    long_bull = (ma30 > ma200).shift(lag).eq(True)
    spread = (ma30 / ma200 - 1).shift(lag).replace([np.inf, -np.inf], np.nan)
    strong_bull = long_bull & (spread >= strong_spread) & (signal_vol <= panic_volatility)

    raw = pd.DataFrame(core_weight, index=price.index, columns=[SYMBOL])
    state = pd.Series("Bear Core", index=price.index, dtype="object")
    raw.loc[long_bull, SYMBOL] = weak_bull_weight
    state.loc[long_bull] = "Weak Bull"
    raw.loc[strong_bull, SYMBOL] = 1.0
    state.loc[strong_bull] = "Strong Bull"

    recovery_armed = False
    recovery_stage = 0
    for date in price.index:
        if pd.notna(signal_dd.loc[date]) and signal_dd.loc[date] <= -recovery_trigger:
            recovery_armed = True

        is_panic = (
            not bool(long_bull.loc[date])
            and pd.notna(signal_vol.loc[date])
            and signal_vol.loc[date] >= panic_volatility
            and signal_price.loc[date] < signal_ma200.loc[date]
        )
        if is_panic:
            raw.loc[date, SYMBOL] = panic_weight
            state.loc[date] = "Panic"

        if recovery_armed:
            if bool(above20_2d.loc[date]) and bool(ma20_up.loc[date]):
                recovery_stage = max(recovery_stage, 1)
            if recovery_stage >= 1 and signal_price.loc[date] > signal_ma50.loc[date]:
                recovery_stage = max(recovery_stage, 2)
            if recovery_stage >= 2 and (
                signal_price.loc[date] > signal_ma200.loc[date]
                or signal_ma20.loc[date] > signal_ma50.loc[date]
            ):
                recovery_stage = 3

            if recovery_stage == 1:
                raw.loc[date, SYMBOL] = max(float(raw.loc[date, SYMBOL]), 0.70)
                state.loc[date] = "Recovery 70"
            elif recovery_stage == 2:
                raw.loc[date, SYMBOL] = max(float(raw.loc[date, SYMBOL]), 0.90)
                state.loc[date] = "Recovery 90"
            elif recovery_stage == 3:
                raw.loc[date, SYMBOL] = 1.0
                state.loc[date] = "Recovery 100"
                recovery_armed = False
                recovery_stage = 0

            if (
                recovery_stage > 0
                and signal_price.loc[date] < signal_ma20.loc[date]
                and not bool(ma20_up.loc[date])
            ):
                recovery_stage = 0

    weights = rebalance_weights(raw.clip(0, 1), rebalance)
    applied_state = state.where(weights[SYMBOL].eq(raw[SYMBOL]), "Rebalance Hold")
    return weights, applied_state


def holdings_backtest(
    target_weights: pd.DataFrame,
    open_prices: pd.Series,
    close_prices: pd.Series,
    cost_rate: float,
    initial_capital: float,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    shares = 0.0
    cash = float(initial_capital)
    previous_close_nav = float(initial_capital)
    prior_target: float | None = None
    daily_ret = pd.Series(0.0, index=target_weights.index)
    actual_weight = pd.Series(0.0, index=target_weights.index)
    turnover = pd.Series(0.0, index=target_weights.index)
    share_history = pd.Series(0.0, index=target_weights.index)
    cash_history = pd.Series(0.0, index=target_weights.index)

    for number, date in enumerate(target_weights.index):
        price = float(open_prices.loc[date])
        nav_before = cash + shares * price
        target = float(target_weights.loc[date, SYMBOL])
        should_rebalance = prior_target is None or not np.isclose(target, prior_target, atol=1e-12, rtol=0)

        if should_rebalance and nav_before > 0 and price > 0:
            target_shares = float(np.floor(nav_before * target / price))

            def remaining_cash(candidate: float) -> float:
                traded_value = abs(candidate - shares) * price
                return nav_before - candidate * price - traded_value * cost_rate

            while target_shares > 0 and remaining_cash(target_shares) < -1e-9:
                target_shares -= 1
            traded_value = abs(target_shares - shares) * price
            trading_cost = traded_value * cost_rate
            shares = target_shares
            cash = max(nav_before - shares * price - trading_cost, 0.0)
            turnover.loc[date] = traded_value / nav_before
            prior_target = target

        close_price = float(close_prices.loc[date])
        marked_nav = cash + shares * close_price
        actual_weight.loc[date] = shares * close_price / marked_nav if marked_nav > 0 else 0.0
        share_history.loc[date] = shares
        cash_history.loc[date] = cash

        daily_ret.loc[date] = marked_nav / previous_close_nav - 1 if previous_close_nav > 0 else 0.0
        previous_close_nav = marked_nav

    return daily_ret, actual_weight, turnover, share_history, cash_history


def calc_metrics(daily_ret: pd.Series) -> dict[str, object]:
    daily_ret = daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nav = (1 + daily_ret).cumprod()
    years = len(nav) / TRADING_DAYS
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    dd = nav / nav.cummax() - 1
    mdd = float(dd.min())
    std = daily_ret.std()
    sharpe = float(daily_ret.mean() / std * np.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    monthly = nav.resample("ME").last().pct_change().dropna()
    return {
        "nav": nav,
        "dd": dd,
        "total": float(nav.iloc[-1] - 1),
        "cagr": float(cagr),
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else 0.0,
        "monthly_win": float((monthly > 0).mean()) if len(monthly) else 0.0,
    }


def line_chart(data: pd.DataFrame, title: str, percent: bool = False) -> go.Figure:
    fig = go.Figure()
    colors = ["#0F766E", "#2563EB", "#B91C1C", "#F59E0B"]
    for number, column in enumerate(data.columns):
        fig.add_trace(
            go.Scatter(x=data.index, y=data[column], mode="lines", name=str(column), line=dict(color=colors[number % 4]))
        )
    fig.update_layout(
        title=title,
        height=340,
        margin=dict(l=10, r=10, t=45, b=15),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(gridcolor="#E5E7EB", tickformat=".1%" if percent else None),
    )
    return fig


with st.sidebar:
    st.header("Settings")
    left, right = st.columns(2)
    with left:
        start_date = st.date_input("Start", datetime(2010, 1, 1))
    with right:
        end_date = st.date_input("End", datetime.today())

    st.subheader("Long-Term Regime")
    fast_window = st.slider("Fast MA", 20, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)
    strong_spread = st.slider("Strong Bull MA spread (%)", 0, 20, 5, 1) / 100

    st.subheader("Core / Tactical Allocation")
    core_weight = st.slider("Bear core QQQ (%)", 0, 100, 60, 5) / 100
    panic_weight = st.slider("Panic QQQ (%)", 0, 100, 45, 5) / 100
    weak_bull_weight = st.slider("Weak Bull QQQ (%)", 0, 100, 90, 5) / 100

    st.subheader("Panic Filter")
    vol_window = st.slider("Volatility window", 10, 80, 20, 5)
    panic_volatility = st.slider("Panic volatility threshold (%)", 10, 80, 35, 5) / 100

    st.subheader("Fast Recovery")
    recovery_trigger = st.slider("Arm recovery after drawdown (%)", 5, 40, 10, 5) / 100
    recovery_fast = st.slider("Recovery fast MA", 10, 40, 20, 5)
    recovery_mid = st.slider("Recovery mid MA", 30, 100, 50, 5)

    st.subheader("Trading")
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.25, step=0.01) / 100
    initial_capital = st.number_input("Initial capital ($)", min_value=1000.0, value=10000.0, step=1000.0)

    st.subheader("Execution")
    account_state = render_account_controls((SYMBOL,), "qqq_v4", preferred_profile="default")
    execution_source = account_state["source"]
    snapshot = account_state["snapshot"]
    current_shares = float(account_state["shares"][SYMBOL])
    current_cash = float(account_state["cash"])
    account_value = float(account_state["account_value"])


with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| State | QQQ target |
|---|---:|
| Strong Bull | 100% |
| Weak Bull | {weak_bull_weight:.0%} |
| Bear core | {core_weight:.0%} |
| High-volatility panic | {panic_weight:.0%} |
| Recovery stage 1 | at least 70% |
| Recovery stage 2 | at least 90% |
| Recovery complete | 100% |

Recovery is armed after a {recovery_trigger:.0%} QQQ drawdown. Stage 1 requires two closes above
MA{recovery_fast} with a rising MA; stage 2 requires a close above MA{recovery_mid}; full recovery
requires a close above MA{slow_window} or MA{recovery_fast} above MA{recovery_mid}.
"""
    )

if panic_weight > core_weight or core_weight > weak_bull_weight:
    st.error("Allocation must satisfy Panic <= Bear core <= Weak Bull.")
    st.stop()
if not run_btn:
    st.info("Check the sidebar settings, then click Run backtest.")
    st.stop()
if execution_source == KIWOOM_SOURCE and not snapshot:
    st.error("Load the Kiwoom account information first.")
    st.stop()
if start_date >= end_date:
    st.error("Start must be earlier than End.")
    st.stop()

progress = st.progress(0, text="Loading QQQ data...")
try:
    warmup_days = max(slow_window, vol_window, recovery_mid) * 3
    warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=warmup_days)
    qqq = load_yahoo_chart(SYMBOL, warmup_start, datetime.combine(end_date, datetime.min.time()))
except Exception as exc:
    st.error(f"Could not load Yahoo Finance data: {exc}")
    st.stop()

full_price = qqq["adjclose"].ffill()
adj_factor = (qqq["adjclose"] / qqq["close"]).replace([np.inf, -np.inf], np.nan).ffill()
adjusted_open = (qqq["open"] * adj_factor).replace([np.inf, -np.inf], np.nan).ffill()
close_ret = full_price.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
fast_ma = full_price.rolling(fast_window).mean()
slow_ma = full_price.rolling(slow_window).mean()
recovery_fast_ma = full_price.rolling(recovery_fast).mean()
recovery_mid_ma = full_price.rolling(recovery_mid).mean()
vol = close_ret.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
drawdown = full_price / full_price.cummax() - 1

index = qqq.index[(qqq.index.date >= start_date) & (qqq.index.date <= end_date)]
if len(index) < 200:
    st.error("선택한 기간에 유효한 데이터가 부족합니다.")
    st.stop()

weights_full, state_full = build_asymmetric_weights(
    full_price,
    recovery_fast_ma,
    fast_ma,
    recovery_mid_ma,
    slow_ma,
    vol,
    drawdown,
    core_weight,
    panic_weight,
    weak_bull_weight,
    panic_volatility,
    strong_spread,
    recovery_trigger,
    rebalance,
)
close_weights_full, close_state_full = build_asymmetric_weights(
    full_price,
    recovery_fast_ma,
    fast_ma,
    recovery_mid_ma,
    slow_ma,
    vol,
    drawdown,
    core_weight,
    panic_weight,
    weak_bull_weight,
    panic_volatility,
    strong_spread,
    recovery_trigger,
    rebalance,
    lag_for_execution=False,
)
weights = weights_full.reindex(index).fillna(0.0)
state = state_full.reindex(index).fillna("Warmup")
strategy_ret, actual_weight, turnover, shares, cash = holdings_backtest(
    weights,
    adjusted_open.reindex(index).ffill(),
    qqq["adjclose"].reindex(index).ffill(),
    cost_rate,
    initial_capital,
)
benchmark_ret = qqq["adjclose"].pct_change().reindex(index).fillna(0.0)
metrics = calc_metrics(strategy_ret)
benchmark_metrics = calc_metrics(benchmark_ret)

# Fixed V3 reference using the original page defaults for an apples-to-apples comparison.
v3_trend = fast_ma > slow_ma
v3_regime = build_regime_signal(v3_trend, fast_ma, slow_ma, vol, 0.05, 0.35)
v3_turnaround = build_turnaround_signal(full_price, fast_ma, slow_ma, 0.10, 10, 60, 2)
v3_risk = (0.20 / vol.shift(1)).clip(0, 1).fillna(0.0)
v3_weak_risk = (v3_risk * 0.75).clip(0, 1)
v3_weight = pd.Series(0.30, index=full_price.index)
v3_weight.loc[v3_regime == "Weak Bull"] = (
    v3_weak_risk + (1 - v3_weak_risk) * 0.20
).loc[v3_regime == "Weak Bull"]
v3_weight.loc[v3_regime == "Strong Bull"] = (
    v3_risk + (1 - v3_risk) * 0.50
).loc[v3_regime == "Strong Bull"]
v3_weight.loc[v3_turnaround] = 1.0
v3_weights = rebalance_weights(v3_weight.to_frame(SYMBOL), rebalance).reindex(index).fillna(0.0)
v3_ret, _, _, _, _ = holdings_backtest(
    v3_weights,
    adjusted_open.reindex(index).ffill(),
    qqq["adjclose"].reindex(index).ffill(),
    cost_rate,
    initial_capital,
)
v3_metrics = calc_metrics(v3_ret)

latest_date = index[-1]
latest_regime = str(close_state_full.loc[latest_date])
next_weight = float(close_weights_full.loc[latest_date, SYMBOL])
latest_price = float(full_price.loc[latest_date])
if execution_source == KIWOOM_SOURCE:
    account_value = current_cash + current_shares * latest_price
effective_value = account_value if account_value > 0 else current_cash + current_shares * latest_price
target_shares = np.floor(effective_value * next_weight / latest_price) if latest_price > 0 else 0
order_shares = target_shares - current_shares
target_cash = effective_value - target_shares * latest_price

progress.progress(100, text="Done")
progress.empty()

st.success(
    f"{latest_date.date()} 종가 신호: {latest_regime} | 다음 시가 목표 QQQ {next_weight:.1%}, 현금 {1-next_weight:.1%}"
)
columns = st.columns(6)
columns[0].metric("Total", f"{metrics['total']:.1%}")
columns[1].metric("CAGR", f"{metrics['cagr']:.1%}")
columns[2].metric("MDD", f"{metrics['mdd']:.1%}")
columns[3].metric("Sharpe", f"{metrics['sharpe']:.2f}")
columns[4].metric("Calmar", f"{metrics['calmar']:.2f}")
columns[5].metric("Monthly Win", f"{metrics['monthly_win']:.1%}")

performance_tab, execution_tab, signal_tab, table_tab = st.tabs(
    ["Performance", "Execution", "Signal / Holdings", "Comparison"]
)

with performance_tab:
    nav = pd.DataFrame(
        {
            "QQQ Core Recovery V4": metrics["nav"],
            "QQQ Holdings V3": v3_metrics["nav"],
            "QQQ Buy & Hold": benchmark_metrics["nav"],
        }
    )
    st.plotly_chart(line_chart(nav, "Cumulative NAV"), use_container_width=True)
    drawdown = pd.DataFrame({"V4": metrics["dd"], "V3": v3_metrics["dd"], "QQQ": benchmark_metrics["dd"]})
    st.plotly_chart(line_chart(drawdown, "Drawdown", percent=True), use_container_width=True)

with execution_tab:
    render_account_summary(account_state, account_value)
    plan = pd.DataFrame(
        [
            {
                "Symbol": SYMBOL,
                "Latest Price": latest_price,
                "Target Weight": next_weight,
                "Target Shares": target_shares,
                "Current Shares": current_shares,
                "Order": "Buy" if order_shares > 0 else "Sell" if order_shares < 0 else "Hold",
                "Order Shares": order_shares,
                "Target Cash": target_cash,
            }
        ]
    )
    st.dataframe(plan, use_container_width=True, hide_index=True)
    st.caption("최신 종가를 주문 수량 추정에 사용합니다. 실제 주문 전 다음 시가와 체결 결과를 확인하세요.")

with signal_tab:
    signal_data = pd.DataFrame(
        {
            "QQQ": full_price.reindex(index),
            f"MA{recovery_fast}": recovery_fast_ma.reindex(index),
            f"MA{recovery_mid}": recovery_mid_ma.reindex(index),
            f"MA{slow_window}": slow_ma.reindex(index),
        }
    )
    st.plotly_chart(line_chart(signal_data, "QQQ Trend"), use_container_width=True)
    recent = pd.DataFrame(
        {
            "State": state,
            "Target QQQ": weights[SYMBOL],
            "Actual QQQ": actual_weight,
            "Cash Weight": (1 - actual_weight).clip(0, 1),
            "QQQ Shares": shares,
            "Cash ($)": cash,
            "Turnover": turnover,
        }
    ).tail(30)
    st.dataframe(recent, use_container_width=True)

with table_tab:
    comparison = pd.DataFrame(
        [
            {"Strategy": "QQQ Core Recovery V4", **{k: metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
            {"Strategy": "QQQ Holdings V3", **{k: v3_metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
            {"Strategy": "QQQ Buy & Hold", **{k: benchmark_metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
        ]
    )
    st.dataframe(comparison, use_container_width=True, hide_index=True)

