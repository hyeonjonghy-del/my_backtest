"""KODEX legacy-sector rotation research backtest."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


TRADING_DAYS = 252
BOND_NAME = "KODEX 단기채권"
BOND_SYMBOL = "153130.KS"
BENCHMARK_NAME = "KODEX 200"
BENCHMARK_SYMBOL = "069500.KS"
SECTORS = {
    "반도체": "091160.KS",
    "자동차": "091180.KS",
    "은행": "091170.KS",
    "증권": "102970.KS",
    "보험": "140700.KS",
    "건설": "117700.KS",
    "에너지화학": "117460.KS",
    "철강": "117680.KS",
    "기계장비": "102960.KS",
    "운송": "140710.KS",
}
STRATEGY_NAMES = [
    "현재 전략(반도체 최소 40%)",
    "반도체 완전 이탈",
    "반도체 우선 + 타업종 대체",
    "순수 업종순환 Top 2",
]
COLORS = ["#0F766E", "#DC2626", "#F59E0B", "#7C3AED", "#2563EB", "#64748B"]


st.set_page_config(page_title="KODEX 업종 순환 전략 연구", page_icon="🇰🇷", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("KODEX 업종 순환 전략 연구")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)
st.caption(
    "반도체 자금이 이탈할 때 다른 업종으로 이동하는 것이 수익률과 MDD를 개선했는지 "
    "10년 장기 데이터로 비교합니다."
)


def normalize_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


@st.cache_data(show_spinner=False, ttl=3600)
def load_yahoo_chart(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    period1 = int(datetime.combine(start_dt.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine((end_dt + timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    frame = pd.DataFrame(
        {
            "open": quote["open"],
            "close": quote["close"],
            "adjclose": adjclose,
        },
        index=index,
    )
    return normalize_index(frame).dropna(subset=["adjclose"])


def adjusted_open(frame: pd.DataFrame) -> pd.Series:
    factor = (frame["adjclose"] / frame["close"]).replace([np.inf, -np.inf], np.nan).ffill()
    return (frame["open"] * factor).replace([np.inf, -np.inf], np.nan).ffill()


def calc_metrics(daily_ret: pd.Series) -> dict[str, object]:
    ret = daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nav = (1 + ret).cumprod()
    if nav.empty:
        return {"nav": nav, "drawdown": nav, "total": np.nan, "cagr": np.nan, "mdd": np.nan,
                "volatility": np.nan, "sharpe": np.nan, "calmar": np.nan, "monthly_win": np.nan}
    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    drawdown = nav / nav.cummax() - 1
    std = ret.std()
    mdd = drawdown.min()
    sharpe = ret.mean() / std * np.sqrt(TRADING_DAYS) if std > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    monthly_win = ((1 + ret).resample("ME").prod().dropna() > 1).mean()
    return {
        "nav": nav,
        "drawdown": drawdown,
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "volatility": std * np.sqrt(TRADING_DAYS),
        "sharpe": sharpe,
        "calmar": calmar,
        "monthly_win": monthly_win,
    }


def metric_row(name: str, ret: pd.Series, turnover: pd.Series | None = None) -> dict[str, object]:
    metrics = calc_metrics(ret)
    return {
        "Strategy": name,
        "Total": metrics["total"],
        "CAGR": metrics["cagr"],
        "MDD": metrics["mdd"],
        "Volatility": metrics["volatility"],
        "Sharpe": metrics["sharpe"],
        "Calmar": metrics["calmar"],
        "Monthly Win": metrics["monthly_win"],
        "Annual Turnover": np.nan if turnover is None else turnover.mean() * TRADING_DAYS,
    }


def scale_for_target_vol(volatility: float, target_vol: float) -> float:
    if not np.isfinite(volatility) or volatility <= 0:
        return 0.0
    return float(np.clip(target_vol / volatility, 0.0, 1.0))


def build_strategy_weights(
    prices: pd.DataFrame,
    fast_window: int,
    slow_window: int,
    momentum_window: int,
    vol_window: int,
    target_vol: float,
    bear_semiconductor: float,
) -> dict[str, pd.DataFrame]:
    sector_names = list(SECTORS)
    fast_ma = prices[sector_names].rolling(fast_window).mean()
    slow_ma = prices[sector_names].rolling(slow_window).mean()
    trend = fast_ma > slow_ma
    momentum = prices[sector_names].pct_change(momentum_window)
    volatility = prices[sector_names].pct_change().rolling(vol_window).std() * np.sqrt(TRADING_DAYS)

    # Today's close signal is used at the following open.
    trend = trend.shift(1).fillna(False)
    momentum = momentum.shift(1)
    eligible = trend & (momentum > 0)
    volatility = volatility.shift(1)

    columns = sector_names + [BOND_NAME]
    results = {name: pd.DataFrame(0.0, index=prices.index, columns=columns) for name in STRATEGY_NAMES}

    for date in prices.index:
        # The first three variants preserve the original semiconductor MA rule.
        semi_ok = bool(trend.at[date, "반도체"])
        semi_vol = volatility.at[date, "반도체"]
        semi_scale = scale_for_target_vol(semi_vol, target_vol)

        current = results[STRATEGY_NAMES[0]]
        semi_weight = semi_scale if semi_ok else bear_semiconductor
        current.at[date, "반도체"] = semi_weight
        current.at[date, BOND_NAME] = 1 - semi_weight

        full_exit = results[STRATEGY_NAMES[1]]
        semi_weight = semi_scale if semi_ok else 0.0
        full_exit.at[date, "반도체"] = semi_weight
        full_exit.at[date, BOND_NAME] = 1 - semi_weight

        priority = results[STRATEGY_NAMES[2]]
        if semi_ok:
            chosen = "반도체"
        else:
            candidates = [name for name in sector_names if name != "반도체" and eligible.at[date, name]]
            chosen = max(candidates, key=lambda name: momentum.at[date, name]) if candidates else None
        if chosen is None:
            priority.at[date, BOND_NAME] = 1.0
        else:
            risk_weight = scale_for_target_vol(volatility.at[date, chosen], target_vol)
            priority.at[date, chosen] = risk_weight
            priority.at[date, BOND_NAME] = 1 - risk_weight

        rotation = results[STRATEGY_NAMES[3]]
        candidates = [name for name in sector_names if eligible.at[date, name]]
        ranked = sorted(candidates, key=lambda name: momentum.at[date, name], reverse=True)[:2]
        if not ranked:
            rotation.at[date, BOND_NAME] = 1.0
        else:
            average_vol = float(volatility.loc[date, ranked].mean())
            risk_sleeve = scale_for_target_vol(average_vol, target_vol)
            for name in ranked:
                rotation.at[date, name] = risk_sleeve / len(ranked)
            rotation.at[date, BOND_NAME] = 1 - risk_sleeve

    return results


def backtest_drift_aware(target_weights: pd.DataFrame, asset_returns: pd.DataFrame, cost_rate: float):
    current = target_weights.iloc[0].to_numpy(dtype=float)
    returns: list[float] = []
    turnovers: list[float] = []
    for (_, desired_row), (_, return_row) in zip(target_weights.iterrows(), asset_returns.iterrows()):
        desired = desired_row.to_numpy(dtype=float)
        asset_ret = return_row.to_numpy(dtype=float)
        turnover = np.abs(desired - current).sum() / 2
        gross_growth = float(np.dot(desired, 1 + asset_ret))
        net_growth = (1 - turnover * cost_rate) * gross_growth
        returns.append(net_growth - 1)
        turnovers.append(turnover)
        current = desired * (1 + asset_ret) / gross_growth if gross_growth > 0 else desired
    return (
        pd.Series(returns, index=target_weights.index),
        pd.Series(turnovers, index=target_weights.index),
    )


def period_summary(returns: dict[str, pd.Series], turnovers: dict[str, pd.Series], start=None, end=None):
    rows = []
    for name, ret in returns.items():
        selected = ret
        selected_turnover = turnovers.get(name)
        if start is not None:
            selected = selected[selected.index >= pd.Timestamp(start)]
            if selected_turnover is not None:
                selected_turnover = selected_turnover[selected_turnover.index >= pd.Timestamp(start)]
        if end is not None:
            selected = selected[selected.index <= pd.Timestamp(end)]
            if selected_turnover is not None:
                selected_turnover = selected_turnover[selected_turnover.index <= pd.Timestamp(end)]
        rows.append(metric_row(name, selected, selected_turnover))
    return pd.DataFrame(rows)


def line_chart(data: pd.DataFrame, title: str, percent: bool = False):
    fig = go.Figure()
    for i, column in enumerate(data.columns):
        fig.add_trace(go.Scatter(
            x=data.index, y=data[column], mode="lines", name=str(column),
            line=dict(color=COLORS[i % len(COLORS)], width=2.4 if i == 0 else 1.7),
        ))
    fig.update_layout(
        title=title, height=410, hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=10, r=10, t=48, b=20), legend=dict(orientation="h", y=1.08),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="#E5E7EB", tickformat=".1%" if percent else None),
    )
    return fig


def monthly_table(ret: pd.Series) -> pd.DataFrame:
    monthly = (1 + ret).resample("ME").prod() - 1
    frame = monthly.to_frame("return")
    frame["Year"] = frame.index.year
    frame["Month"] = frame.index.month
    table = frame.pivot(index="Year", columns="Month", values="return")
    table.columns = [datetime(2000, int(month), 1).strftime("%b") for month in table.columns]
    table["Annual"] = (1 + table).prod(axis=1, skipna=True) - 1
    return table


with st.sidebar:
    st.header("Settings")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start", datetime.today() - timedelta(days=3653))
    with c2:
        end_date = st.date_input("End", datetime.today())
    st.subheader("고정 규칙")
    fast_window = st.slider("Fast MA", 10, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)
    momentum_window = st.slider("모멘텀 기간(거래일)", 63, 252, 126, 21)
    vol_window = st.slider("변동성 기간", 10, 60, 20, 5)
    target_vol = st.slider("목표 변동성 (%)", 10, 60, 40, 5) / 100
    bear_semiconductor = st.slider("기존 전략 약세 반도체 비중 (%)", 0, 100, 40, 5) / 100
    cost_rate = st.number_input("편도 거래비용 (%)", min_value=0.0, value=0.10, step=0.01) / 100


with st.expander("이번 연구에서 고정한 전략", expanded=False):
    st.markdown(
        f"""
| 구분 | 규칙 |
|---|---|
| 반도체 추세 | 기존 전략과 동일하게 MA{fast_window} > MA{slow_window} |
| 대체 업종 자격 | MA{fast_window} > MA{slow_window} 및 {momentum_window}일 수익률 > 0 |
| 현재 전략 | 반도체 상승 시 변동성 조절, 약세 시에도 반도체 {bear_semiconductor:.0%} 유지 |
| 완전 이탈 | 반도체 약세 시 단기채권 100% |
| 반도체 우선 | 반도체 상승 시 반도체, 약세 시 가장 강한 타 업종 1개 |
| 순수 순환 | 적격 업종 중 {momentum_window}일 모멘텀 상위 2개 |
| 안전장치 | 적격 업종이 없으면 단기채권 100% |
| 체결 | 매일 종가 신호 → 다음 거래일 시가, 편도 비용 {cost_rate:.2%} |
"""
    )


if not run_btn:
    st.info("조건을 확인한 뒤 Run backtest를 누르세요. 첫 실행은 여러 업종 데이터를 불러와 시간이 걸릴 수 있습니다.")
    st.stop()
if start_date >= end_date or fast_window >= slow_window:
    st.error("날짜와 이동평균 기간을 확인하세요.")
    st.stop()


warmup_days = max(slow_window, momentum_window, vol_window) * 3
warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=warmup_days)
end_dt = datetime.combine(end_date, datetime.min.time())
symbols = {**SECTORS, BOND_NAME: BOND_SYMBOL, BENCHMARK_NAME: BENCHMARK_SYMBOL}
frames: dict[str, pd.DataFrame] = {}
progress = st.progress(0, text="업종 데이터를 불러오는 중...")
for i, (name, symbol) in enumerate(symbols.items(), start=1):
    try:
        frames[name] = load_yahoo_chart(symbol, warmup_start, end_dt)
    except Exception as exc:
        st.error(f"{name}({symbol}) 데이터를 불러오지 못했습니다: {exc}")
        st.stop()
    progress.progress(int(i / len(symbols) * 70), text=f"{name} 데이터 확인 중...")

all_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in frames.values()))))
all_dates = all_dates[(all_dates >= pd.Timestamp(warmup_start)) & (all_dates <= pd.Timestamp(end_dt))]
adjusted_prices = pd.DataFrame(index=all_dates)
adjusted_opens = pd.DataFrame(index=all_dates)
for name, frame in frames.items():
    adjusted_prices[name] = frame["adjclose"].reindex(all_dates).ffill()
    adjusted_opens[name] = adjusted_open(frame).reindex(all_dates).ffill()

valid = adjusted_prices.dropna().index
test_dates = valid[(valid.date >= start_date) & (valid.date <= end_date)]
if len(test_dates) < 500:
    st.error("공통 거래 데이터가 부족합니다. 시작일을 늦추거나 잠시 후 다시 실행하세요.")
    st.stop()

weights_full = build_strategy_weights(
    adjusted_prices.loc[valid], fast_window, slow_window, momentum_window,
    vol_window, target_vol, bear_semiconductor,
)
open_returns = (adjusted_opens.loc[valid].shift(-1) / adjusted_opens.loc[valid] - 1).fillna(0.0)
asset_columns = list(SECTORS) + [BOND_NAME]
asset_returns = open_returns.loc[test_dates, asset_columns]

strategy_returns: dict[str, pd.Series] = {}
turnovers: dict[str, pd.Series] = {}
weights: dict[str, pd.DataFrame] = {}
for i, name in enumerate(STRATEGY_NAMES, start=1):
    weights[name] = weights_full[name].reindex(test_dates).ffill().fillna(0.0)
    strategy_returns[name], turnovers[name] = backtest_drift_aware(weights[name], asset_returns, cost_rate)
    progress.progress(70 + int(i / len(STRATEGY_NAMES) * 25), text=f"{name} 계산 중...")

strategy_returns[f"{BENCHMARK_NAME} 100%"] = open_returns.loc[test_dates, BENCHMARK_NAME]
strategy_returns["KODEX 반도체 100%"] = open_returns.loc[test_dates, "반도체"]
progress.progress(100, text="완료")
progress.empty()

full_summary = period_summary(strategy_returns, turnovers)
research_names = STRATEGY_NAMES
research_summary = full_summary[full_summary["Strategy"].isin(research_names)].copy()
split_date = pd.Timestamp("2022-01-01")
train = period_summary(strategy_returns, turnovers, end=split_date - pd.Timedelta(days=1))
validation = period_summary(strategy_returns, turnovers, start=split_date)
validation_research = validation[validation["Strategy"].isin(research_names)].copy()
full_by_name = research_summary.set_index("Strategy")
validation_by_name = validation_research.set_index("Strategy")

short_names = {
    STRATEGY_NAMES[0]: "반도체 40%",
    STRATEGY_NAMES[1]: "반도체 완전 이탈",
    STRATEGY_NAMES[2]: "타업종 1개 대체",
    STRATEGY_NAMES[3]: "업종순환 Top 2",
}
full_winner = research_summary.sort_values("Calmar", ascending=False).iloc[0]["Strategy"]
validation_winner = validation_research.sort_values("Calmar", ascending=False).iloc[0]["Strategy"]
baseline_validation = validation_by_name.loc[STRATEGY_NAMES[0]]
exit_validation = validation_by_name.loc[STRATEGY_NAMES[1]]
exit_improved = (
    exit_validation["CAGR"] > baseline_validation["CAGR"]
    and exit_validation["MDD"] > baseline_validation["MDD"]
)

st.markdown("### 한눈에 보는 결론")
if full_winner == STRATEGY_NAMES[0]:
    st.success("✅ **현재 전략 유지** — 전체 기간의 위험조정 성과는 반도체 최소 40% 전략이 가장 좋습니다.")
else:
    st.success(f"✅ **전체 기간 1위** — {short_names[full_winner]} 전략입니다.")
if exit_improved:
    st.info(
        "🔎 **추가 검증 후보: 반도체 완전 이탈** — 2022년 이후에는 현재 전략보다 "
        "수익률이 높고 MDD도 낮았습니다. 다만 이전 기간까지 항상 우월하지는 않았습니다."
    )

rejected = []
for name in STRATEGY_NAMES[2:]:
    row = validation_by_name.loc[name]
    if row["Calmar"] < baseline_validation["Calmar"] or row["Annual Turnover"] > 5:
        rejected.append(short_names[name])
if rejected:
    st.error(
        "❌ **현재 방식으로 채택 제외** — " + ", ".join(rejected)
        + ". 성과가 낮고 매매 회전율이 지나치게 높습니다."
    )

c1, c2, c3, c4 = st.columns(4)
c1.metric("전체 기간 1위", short_names[full_winner])
c2.metric("2022년 이후 1위", short_names[validation_winner])
c3.metric("현재 권고", "반도체 40% 유지" if full_winner == STRATEGY_NAMES[0] else short_names[full_winner])
c4.metric("개선 후보", "완전 이탈" if exit_improved else "없음")

simple_rows = []
for name in research_names:
    full_row = full_by_name.loc[name]
    recent_row = validation_by_name.loc[name]
    if name == full_winner:
        verdict = "유지/추천"
    elif name == STRATEGY_NAMES[1] and exit_improved:
        verdict = "추가 검증"
    elif short_names[name] in rejected:
        verdict = "탈락"
    else:
        verdict = "보류"
    if name == STRATEGY_NAMES[0]:
        comment = "전체 기간 기준점"
    elif name == STRATEGY_NAMES[1]:
        comment = "최근 구간은 우수, 과거 구간은 열세"
    else:
        comment = "회전율이 높고 MDD 개선 실패"
    simple_rows.append({
        "판정": verdict,
        "전략": short_names[name],
        "전체 CAGR": full_row["CAGR"],
        "전체 MDD": full_row["MDD"],
        "2022+ CAGR": recent_row["CAGR"],
        "2022+ MDD": recent_row["MDD"],
        "연간 회전율": recent_row["Annual Turnover"],
        "한줄 평가": comment,
    })
st.dataframe(
    pd.DataFrame(simple_rows).style.format({
        "전체 CAGR": "{:.1%}", "전체 MDD": "{:.1%}",
        "2022+ CAGR": "{:.1%}", "2022+ MDD": "{:.1%}", "연간 회전율": "{:.0%}",
    }),
    use_container_width=True, hide_index=True,
)

tab_perf, tab_period, tab_weights, tab_monthly, tab_data = st.tabs(
    ["Performance", "기간별 검증", "선택 업종 / 비중", "Monthly", "Data"]
)

with tab_perf:
    nav = pd.DataFrame({name: calc_metrics(ret)["nav"] for name, ret in strategy_returns.items()})
    st.plotly_chart(line_chart(nav, "Cumulative NAV"), use_container_width=True)
    drawdown = pd.DataFrame({name: calc_metrics(ret)["drawdown"] for name, ret in strategy_returns.items()})
    st.plotly_chart(line_chart(drawdown, "Drawdown", percent=True), use_container_width=True)
    with st.expander("전체 세부 지표 보기", expanded=False):
        st.dataframe(
            full_summary.style.format({
                "Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}", "Volatility": "{:.1%}",
                "Sharpe": "{:.2f}", "Calmar": "{:.2f}", "Monthly Win": "{:.1%}",
                "Annual Turnover": "{:.1%}",
            }),
            use_container_width=True, hide_index=True,
        )

with tab_period:
    st.info("위의 '한눈에 보는 결론'만 확인해도 됩니다. 아래 표는 숫자를 자세히 검토할 때만 펼치세요.")
    with st.expander("시작일 ~ 2021년 세부 지표", expanded=False):
        st.dataframe(train.style.format({"Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}",
                                         "Volatility": "{:.1%}", "Sharpe": "{:.2f}", "Calmar": "{:.2f}",
                                         "Monthly Win": "{:.1%}", "Annual Turnover": "{:.1%}"}),
                     use_container_width=True, hide_index=True)
    with st.expander("2022년 이후 세부 지표", expanded=False):
        st.dataframe(validation.style.format({"Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}",
                                              "Volatility": "{:.1%}", "Sharpe": "{:.2f}", "Calmar": "{:.2f}",
                                              "Monthly Win": "{:.1%}", "Annual Turnover": "{:.1%}"}),
                     use_container_width=True, hide_index=True)

with tab_weights:
    selected_strategy = st.selectbox("비중을 확인할 전략", STRATEGY_NAMES, index=3)
    latest_weights = weights[selected_strategy].iloc[-1]
    active = latest_weights[latest_weights > 0.001].sort_values(ascending=False)
    st.info("현재 목표 비중: " + " | ".join(f"{name} {weight:.1%}" for name, weight in active.items()))
    weight_chart = weights[selected_strategy].loc[:, (weights[selected_strategy] > 0.001).any()]
    fig = go.Figure()
    for i, column in enumerate(weight_chart.columns):
        fig.add_trace(go.Scatter(x=weight_chart.index, y=weight_chart[column], name=column,
                                 stackgroup="one", line=dict(width=0.6, color=COLORS[i % len(COLORS)])))
    fig.update_layout(title="Portfolio Weights", height=410, hovermode="x unified", plot_bgcolor="white",
                      paper_bgcolor="white", yaxis=dict(tickformat=".0%", range=[0, 1]),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(weights[selected_strategy].tail(120).style.format("{:.1%}"), use_container_width=True)

with tab_monthly:
    selected_monthly = st.selectbox("월간 수익률 전략", STRATEGY_NAMES, index=3)
    monthly = monthly_table(strategy_returns[selected_monthly])
    st.dataframe(
        monthly.style.format("{:.1%}").background_gradient(cmap="RdYlGn", axis=None)
        .set_properties(subset=["Annual"], **{"font-weight": "bold", "border-left": "2px solid #9CA3AF"}),
        use_container_width=True,
    )

with tab_data:
    daily_export = pd.DataFrame({f"{name}_return": ret for name, ret in strategy_returns.items()})
    d1, d2 = st.columns(2)
    d1.download_button("Download daily backtest CSV", daily_export.to_csv(index_label="date").encode("utf-8-sig"),
                       file_name="kodex_sector_rotation_daily.csv", mime="text/csv", use_container_width=True)
    d2.download_button("Download summary CSV", full_summary.to_csv(index=False).encode("utf-8-sig"),
                       file_name="kodex_sector_rotation_summary.csv", mime="text/csv", use_container_width=True)
