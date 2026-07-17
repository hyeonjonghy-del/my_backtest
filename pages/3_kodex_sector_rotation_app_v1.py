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
BOND_NAME = "KODEX ?④린梨꾧텒"
BOND_SYMBOL = "153130.KS"
BENCHMARK_NAME = "KODEX 200"
BENCHMARK_SYMBOL = "069500.KS"
SECTORS = {
    "諛섎룄泥?: "091160.KS",
    "?먮룞李?: "091180.KS",
    "???: "091170.KS",
    "利앷텒": "102970.KS",
    "蹂댄뿕": "140700.KS",
    "嫄댁꽕": "117700.KS",
    "?먮꼫吏?뷀븰": "117460.KS",
    "泥좉컯": "117680.KS",
    "湲곌퀎?λ퉬": "102960.KS",
    "?댁넚": "140710.KS",
}
STRATEGY_NAMES = [
    "?꾩옱 ?꾨왂(諛섎룄泥?理쒖냼 40%)",
    "諛섎룄泥??꾩쟾 ?댄깉",
    "諛섎룄泥??곗꽑 + ??낆쥌 ?泥?,
    "?쒖닔 ?낆쥌?쒗솚 Top 2",
]
COLORS = ["#0F766E", "#DC2626", "#F59E0B", "#7C3AED", "#2563EB", "#64748B"]


st.set_page_config(page_title="KODEX ?낆쥌 ?쒗솚 ?꾨왂 ?곌뎄", page_icon="?눖?눟", layout="wide")
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("KODEX ?낆쥌 ?쒗솚 ?꾨왂 ?곌뎄")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True)
st.caption(
    "諛섎룄泥??먭툑???댄깉?????ㅻⅨ ?낆쥌?쇰줈 ?대룞?섎뒗 寃껋씠 ?섏씡瑜좉낵 MDD瑜?媛쒖꽑?덈뒗吏 "
    "10???κ린 ?곗씠?곕줈 鍮꾧탳?⑸땲??"
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
        semi_ok = bool(trend.at[date, "諛섎룄泥?])
        semi_vol = volatility.at[date, "諛섎룄泥?]
        semi_scale = scale_for_target_vol(semi_vol, target_vol)

        current = results[STRATEGY_NAMES[0]]
        semi_weight = semi_scale if semi_ok else bear_semiconductor
        current.at[date, "諛섎룄泥?] = semi_weight
        current.at[date, BOND_NAME] = 1 - semi_weight

        full_exit = results[STRATEGY_NAMES[1]]
        semi_weight = semi_scale if semi_ok else 0.0
        full_exit.at[date, "諛섎룄泥?] = semi_weight
        full_exit.at[date, BOND_NAME] = 1 - semi_weight

        priority = results[STRATEGY_NAMES[2]]
        if semi_ok:
            chosen = "諛섎룄泥?
        else:
            candidates = [name for name in sector_names if name != "諛섎룄泥? and eligible.at[date, name]]
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
    st.subheader("怨좎젙 洹쒖튃")
    fast_window = st.slider("Fast MA", 10, 100, 30, 5)
    slow_window = st.slider("Slow MA", 100, 250, 200, 5)
    momentum_window = st.slider("紐⑤찘? 湲곌컙(嫄곕옒??", 63, 252, 126, 21)
    vol_window = st.slider("蹂?숈꽦 湲곌컙", 10, 60, 20, 5)
    target_vol = st.slider("紐⑺몴 蹂?숈꽦 (%)", 10, 60, 40, 5) / 100
    bear_semiconductor = st.slider("湲곗〈 ?꾨왂 ?쎌꽭 諛섎룄泥?鍮꾩쨷 (%)", 0, 100, 40, 5) / 100
    cost_rate = st.number_input("?몃룄 嫄곕옒鍮꾩슜 (%)", min_value=0.0, value=0.10, step=0.01) / 100


with st.expander("?대쾲 ?곌뎄?먯꽌 怨좎젙???꾨왂", expanded=False):
    st.markdown(
        f"""
| 援щ텇 | 洹쒖튃 |
|---|---|
| 諛섎룄泥?異붿꽭 | 湲곗〈 ?꾨왂怨??숈씪?섍쾶 MA{fast_window} > MA{slow_window} |
| ?泥??낆쥌 ?먭꺽 | MA{fast_window} > MA{slow_window} 諛?{momentum_window}???섏씡瑜?> 0 |
| ?꾩옱 ?꾨왂 | 諛섎룄泥??곸듅 ??蹂?숈꽦 議곗젅, ?쎌꽭 ?쒖뿉??諛섎룄泥?{bear_semiconductor:.0%} ?좎? |
| ?꾩쟾 ?댄깉 | 諛섎룄泥??쎌꽭 ???④린梨꾧텒 100% |
| 諛섎룄泥??곗꽑 | 諛섎룄泥??곸듅 ??諛섎룄泥? ?쎌꽭 ??媛??媛뺥븳 ? ?낆쥌 1媛?|
| ?쒖닔 ?쒗솚 | ?곴꺽 ?낆쥌 以?{momentum_window}??紐⑤찘? ?곸쐞 2媛?|
| ?덉쟾?μ튂 | ?곴꺽 ?낆쥌???놁쑝硫??④린梨꾧텒 100% |
| 泥닿껐 | 留ㅼ씪 醫낃? ?좏샇 ???ㅼ쓬 嫄곕옒???쒓?, ?몃룄 鍮꾩슜 {cost_rate:.2%} |
"""
    )


if not run_btn:
    st.info("議곌굔???뺤씤????Run backtest瑜??꾨Ⅴ?몄슂. 泥??ㅽ뻾? ?щ윭 ?낆쥌 ?곗씠?곕? 遺덈윭? ?쒓컙??嫄몃┫ ???덉뒿?덈떎.")
    st.stop()
if start_date >= end_date or fast_window >= slow_window:
    st.error("?좎쭨? ?대룞?됯퇏 湲곌컙???뺤씤?섏꽭??")
    st.stop()


warmup_days = max(slow_window, momentum_window, vol_window) * 3
warmup_start = datetime.combine(start_date, datetime.min.time()) - timedelta(days=warmup_days)
end_dt = datetime.combine(end_date, datetime.min.time())
symbols = {**SECTORS, BOND_NAME: BOND_SYMBOL, BENCHMARK_NAME: BENCHMARK_SYMBOL}
frames: dict[str, pd.DataFrame] = {}
progress = st.progress(0, text="?낆쥌 ?곗씠?곕? 遺덈윭?ㅻ뒗 以?..")
for i, (name, symbol) in enumerate(symbols.items(), start=1):
    try:
        frames[name] = load_yahoo_chart(symbol, warmup_start, end_dt)
    except Exception as exc:
        st.error(f"{name}({symbol}) ?곗씠?곕? 遺덈윭?ㅼ? 紐삵뻽?듬땲?? {exc}")
        st.stop()
    progress.progress(int(i / len(symbols) * 70), text=f"{name} ?곗씠???뺤씤 以?..")

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
    st.error("怨듯넻 嫄곕옒 ?곗씠?곌? 遺議깊빀?덈떎. ?쒖옉?쇱쓣 ??텛嫄곕굹 ?좎떆 ???ㅼ떆 ?ㅽ뻾?섏꽭??")
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
    progress.progress(70 + int(i / len(STRATEGY_NAMES) * 25), text=f"{name} 怨꾩궛 以?..")

strategy_returns[f"{BENCHMARK_NAME} 100%"] = open_returns.loc[test_dates, BENCHMARK_NAME]
strategy_returns["KODEX 諛섎룄泥?100%"] = open_returns.loc[test_dates, "諛섎룄泥?]
progress.progress(100, text="?꾨즺")
progress.empty()

full_summary = period_summary(strategy_returns, turnovers)
research_names = STRATEGY_NAMES
research_summary = full_summary[full_summary["Strategy"].isin(research_names)].copy()
best_calmar = research_summary.sort_values("Calmar", ascending=False).iloc[0]
baseline = research_summary[research_summary["Strategy"] == STRATEGY_NAMES[0]].iloc[0]
if best_calmar["Strategy"] != STRATEGY_NAMES[0] and best_calmar["MDD"] > baseline["MDD"]:
    st.success(
        f"?꾪뿕議곗젙 ?깃낵 1?? {best_calmar['Strategy']} | CAGR {best_calmar['CAGR']:.1%}, "
        f"MDD {best_calmar['MDD']:.1%}, Calmar {best_calmar['Calmar']:.2f}. "
        "湲곗〈 ?꾨왂蹂대떎 MDD????븯?듬땲??"
    )
else:
    st.warning(
        f"Calmar 1?꾨뒗 {best_calmar['Strategy']}?댁?留? 湲곗〈 ?꾨왂 ?鍮?CAGR怨?MDD瑜??숈떆??媛쒖꽑?덈뒗吏??"
        "?꾨옒 湲곌컙蹂??쒖뿉???뺤씤?댁빞 ?⑸땲??"
    )

best_metrics = calc_metrics(strategy_returns[best_calmar["Strategy"]])
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Best Strategy", best_calmar["Strategy"])
c2.metric("CAGR", f"{best_metrics['cagr']:.1%}")
c3.metric("MDD", f"{best_metrics['mdd']:.1%}")
c4.metric("Sharpe", f"{best_metrics['sharpe']:.2f}")
c5.metric("Calmar", f"{best_metrics['calmar']:.2f}")
c6.metric("Monthly Win", f"{best_metrics['monthly_win']:.1%}")

tab_perf, tab_period, tab_weights, tab_monthly, tab_data = st.tabs(
    ["Performance", "湲곌컙蹂?寃利?, "?좏깮 ?낆쥌 / 鍮꾩쨷", "Monthly", "Data"]
)

with tab_perf:
    nav = pd.DataFrame({name: calc_metrics(ret)["nav"] for name, ret in strategy_returns.items()})
    st.plotly_chart(line_chart(nav, "Cumulative NAV"), use_container_width=True)
    drawdown = pd.DataFrame({name: calc_metrics(ret)["drawdown"] for name, ret in strategy_returns.items()})
    st.plotly_chart(line_chart(drawdown, "Drawdown", percent=True), use_container_width=True)
    st.dataframe(
        full_summary.style.format({
            "Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}", "Volatility": "{:.1%}",
            "Sharpe": "{:.2f}", "Calmar": "{:.2f}", "Monthly Win": "{:.1%}",
            "Annual Turnover": "{:.1%}",
        }),
        use_container_width=True, hide_index=True,
    )

with tab_period:
    split_date = pd.Timestamp("2022-01-01")
    train = period_summary(strategy_returns, turnovers, end=split_date - pd.Timedelta(days=1))
    validation = period_summary(strategy_returns, turnovers, start=split_date)
    st.subheader("?ㅺ퀎 ?뺤씤 援ш컙: ?쒖옉??~ 2021??)
    st.dataframe(train.style.format({"Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}",
                                     "Volatility": "{:.1%}", "Sharpe": "{:.2f}", "Calmar": "{:.2f}",
                                     "Monthly Win": "{:.1%}", "Annual Turnover": "{:.1%}"}),
                 use_container_width=True, hide_index=True)
    st.subheader("誘몃옒 寃利?援ш컙: 2022??~ 醫낅즺??)
    st.dataframe(validation.style.format({"Total": "{:.1%}", "CAGR": "{:.1%}", "MDD": "{:.1%}",
                                          "Volatility": "{:.1%}", "Sharpe": "{:.2f}", "Calmar": "{:.2f}",
                                          "Monthly Win": "{:.1%}", "Annual Turnover": "{:.1%}"}),
                 use_container_width=True, hide_index=True)

with tab_weights:
    selected_strategy = st.selectbox("鍮꾩쨷???뺤씤???꾨왂", STRATEGY_NAMES, index=3)
    latest_weights = weights[selected_strategy].iloc[-1]
    active = latest_weights[latest_weights > 0.001].sort_values(ascending=False)
    st.info("?꾩옱 紐⑺몴 鍮꾩쨷: " + " | ".join(f"{name} {weight:.1%}" for name, weight in active.items()))
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
    selected_monthly = st.selectbox("?붽컙 ?섏씡瑜??꾨왂", STRATEGY_NAMES, index=3)
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

