"""QQQ-only holdings-based trend and volatility-target backtest v3.

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

st.set_page_config(page_title="QQQ Holdings Backtest V3", page_icon="📈", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("QQQ-Only Holdings Strategy V3")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)

st.caption(
    "QQQ 한 종목과 현금만 사용합니다. 종가로 신호를 계산하고 다음 거래일 시가에 "
    "정수 주식으로 리밸런싱하며, 거래 후 남는 금액은 현금으로 유지합니다."
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


def target_weight(
    regime: str,
    is_turnaround: bool,
    current_vol: float,
    target_vol: float,
    weak_risk_multiplier: float,
    strong_cash_sweep: float,
    weak_cash_sweep: float,
    turnaround_qqq: float,
    bear_qqq: float,
) -> float:
    if is_turnaround:
        return turnaround_qqq
    if regime == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return bear_qqq

    risk_weight = min(target_vol / current_vol, 1.0)
    if regime == "Weak Bull":
        risk_weight = min(risk_weight * weak_risk_multiplier, 1.0)
        cash_sweep = weak_cash_sweep
    else:
        cash_sweep = strong_cash_sweep
    return float(np.clip(risk_weight + (1 - risk_weight) * cash_sweep, 0, 1))


def build_strategy_weights(
    price: pd.Series,
    regime: pd.Series,
    turnaround: pd.Series,
    vol: pd.Series,
    target_vol: float,
    weak_risk_multiplier: float,
    strong_cash_sweep: float,
    weak_cash_sweep: float,
    turnaround_qqq: float,
    bear_qqq: float,
    rebalance: str,
) -> pd.DataFrame:
    weights = pd.DataFrame(index=price.index, columns=[SYMBOL], dtype=float)
    vol_lag = vol.shift(1)
    weights[SYMBOL] = [
        target_weight(
            str(regime.loc[date]),
            bool(turnaround.loc[date]),
            float(vol_lag.loc[date]),
            target_vol,
            weak_risk_multiplier,
            strong_cash_sweep,
            weak_cash_sweep,
            turnaround_qqq,
            bear_qqq,
        )
        for date in price.index
    ]
    return rebalance_weights(weights.clip(0, 1), rebalance)


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

    st.subheader("Trend Filter")
    trend_rule = st.selectbox(
        "Rule",
        ["MA Fast > MA Slow", "Close > MA Slow", "Close > MA Slow + MA Fast > MA Slow"],
    )
    fast_window = st.slider("Fast MA", 20, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)

    st.subheader("Volatility Target")
    vol_window = st.slider("Volatility window", 10, 80, 20, 5)
    target_vol = st.slider("Target volatility (%)", 5, 50, 20, 5) / 100
    weak_risk_multiplier = st.slider("Weak Bull risk multiplier (%)", 20, 100, 75, 5) / 100

    st.subheader("Regime")
    strong_spread = st.slider("Strong Bull MA spread (%)", 0, 20, 5, 1) / 100
    weak_vol_cutoff = st.slider("Weak Bull if volatility above (%)", 10, 80, 35, 5) / 100
    strong_cash_sweep = st.slider("Strong Bull cash sweep to QQQ (%)", 0, 100, 50, 5) / 100
    weak_cash_sweep = st.slider("Weak Bull cash sweep to QQQ (%)", 0, 100, 20, 5) / 100
    bear_qqq = st.slider("Bear-regime QQQ weight (%)", 0, 100, 30, 5) / 100

    st.subheader("Turnaround")
    turnaround_dd = st.slider("Drawdown trigger (%)", 10, 50, 10, 5) / 100
    turnaround_qqq = st.slider("Turnaround QQQ weight (%)", 0, 100, 100, 5) / 100
    turnaround_exit_fast = st.slider("Exit fast MA", 3, 20, 10, 1)
    turnaround_exit_slow = st.slider("Exit slow MA", 10, 80, 60, 5)
    turnaround_exit_confirm = st.slider("Exit confirmation days", 1, 5, 2, 1)

    st.subheader("Trading")
    rebalance = st.radio("Rebalance", ["Daily", "Weekly", "Monthly"], horizontal=True)
    cost_rate = st.number_input("One-way trading cost (%)", min_value=0.0, value=0.25, step=0.01) / 100
    initial_capital = st.number_input("Initial capital ($)", min_value=1000.0, value=10000.0, step=1000.0)

    st.subheader("Execution")
    account_state = render_account_controls((SYMBOL,), "qqq_only", preferred_profile="default")
    execution_source = account_state["source"]
    snapshot = account_state["snapshot"]
    current_shares = float(account_state["shares"][SYMBOL])
    current_cash = float(account_state["cash"])
    account_value = float(account_state["account_value"])


with st.expander("Default Strategy", expanded=False):
    st.markdown(
        f"""
| 구간 | QQQ 목표 비중 |
|---|---:|
| Strong Bull | 변동성 목표 비중 + 남은 현금의 {strong_cash_sweep:.0%} |
| Weak Bull | 변동성 목표 × {weak_risk_multiplier:.0%} + 남은 현금의 {weak_cash_sweep:.0%} |
| Turnaround | {turnaround_qqq:.0%} |
| Bear | {bear_qqq:.0%} |

나머지는 현금이며 QQQ 목표 비중은 항상 0~100%로 제한됩니다.
"""
    )

if not run_btn:
    st.info("사이드바 설정을 확인한 뒤 Run backtest를 누르세요.")
    st.stop()
if execution_source == KIWOOM_SOURCE and not snapshot:
    st.error("키움 실계좌 정보를 먼저 불러오세요.")
    st.stop()
if start_date >= end_date:
    st.error("Start는 End보다 빨라야 합니다.")
    st.stop()

progress = st.progress(0, text="Loading QQQ data...")
try:
    warmup_days = max(slow_window, vol_window, turnaround_exit_slow) * 3
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
vol = close_ret.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
trend = build_trend_signal(full_price, fast_ma, slow_ma, trend_rule)
regime = build_regime_signal(trend, fast_ma, slow_ma, vol, strong_spread, weak_vol_cutoff)
regime_close = build_regime_signal(trend, fast_ma, slow_ma, vol, strong_spread, weak_vol_cutoff, False)
turnaround = build_turnaround_signal(
    full_price, fast_ma, slow_ma, turnaround_dd, turnaround_exit_fast, turnaround_exit_slow, turnaround_exit_confirm
)
turnaround_close = build_turnaround_signal(
    full_price,
    fast_ma,
    slow_ma,
    turnaround_dd,
    turnaround_exit_fast,
    turnaround_exit_slow,
    turnaround_exit_confirm,
    False,
)

index = qqq.index[(qqq.index.date >= start_date) & (qqq.index.date <= end_date)]
if len(index) < 200:
    st.error("선택한 기간에 유효한 데이터가 부족합니다.")
    st.stop()

weights_full = build_strategy_weights(
    full_price,
    regime,
    turnaround,
    vol,
    target_vol,
    weak_risk_multiplier,
    strong_cash_sweep,
    weak_cash_sweep,
    turnaround_qqq,
    bear_qqq,
    rebalance,
)
weights = weights_full.reindex(index).fillna(0.0)
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

latest_date = index[-1]
latest_regime = "Turnaround" if bool(turnaround_close.loc[latest_date]) else str(regime_close.loc[latest_date])
next_weight = target_weight(
    str(regime_close.loc[latest_date]),
    bool(turnaround_close.loc[latest_date]),
    float(vol.loc[latest_date]),
    target_vol,
    weak_risk_multiplier,
    strong_cash_sweep,
    weak_cash_sweep,
    turnaround_qqq,
    bear_qqq,
)
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
    nav = pd.DataFrame({"QQQ Holdings Strategy V3": metrics["nav"], "QQQ Buy & Hold": benchmark_metrics["nav"]})
    st.plotly_chart(line_chart(nav, "Cumulative NAV"), use_container_width=True)
    drawdown = pd.DataFrame({"Strategy": metrics["dd"], "QQQ": benchmark_metrics["dd"]})
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
        {"QQQ": full_price.reindex(index), f"MA{fast_window}": fast_ma.reindex(index), f"MA{slow_window}": slow_ma.reindex(index)}
    )
    st.plotly_chart(line_chart(signal_data, "QQQ Trend"), use_container_width=True)
    recent = pd.DataFrame(
        {
            "Regime": regime.reindex(index).where(~turnaround.reindex(index), "Turnaround"),
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
            {"Strategy": "QQQ Holdings Strategy V3", **{k: metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
            {"Strategy": "QQQ Buy & Hold", **{k: benchmark_metrics[k] for k in ["total", "cagr", "mdd", "sharpe", "calmar"]}},
        ]
    )
    st.dataframe(comparison, use_container_width=True, hide_index=True)

