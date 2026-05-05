"""Larry Williams volatility breakout backtest for Korean ETFs."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

warnings.filterwarnings("ignore")

TRADING_DAYS = 252
ETF_OPTIONS = {
    "KODEX 200": "069500",
    "KODEX 레버리지": "122630",
    "KODEX 코스닥150": "229200",
    "KODEX 코스닥150 레버리지": "233740",
}

st.set_page_config(page_title="Larry Williams Breakout", page_icon="KR", layout="wide")
st.title("Larry Williams 변동성 돌파 전략")
st.caption("국내 KODEX ETF 일봉 + K값 최적화 + 당일 돌파 매수 / 당일 종가 청산")


def normalize_index(obj: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    out = obj.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


@st.cache_data(show_spinner=False, ttl=3600)
def load_krx_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    from pykrx import stock

    df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    out = pd.DataFrame(
        {
            "open": pd.to_numeric(df["시가"], errors="coerce"),
            "high": pd.to_numeric(df["고가"], errors="coerce"),
            "low": pd.to_numeric(df["저가"], errors="coerce"),
            "close": pd.to_numeric(df["종가"], errors="coerce"),
        }
    )
    return normalize_index(out).dropna(how="all")


def calc_metrics(nav: pd.Series) -> dict[str, object]:
    nav = nav.dropna()
    ret = nav.pct_change().dropna()
    if len(nav) < 2:
        return {"total": 0.0, "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "calmar": 0.0, "win": 0.0, "dd": pd.Series(dtype=float)}

    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win = (ret > 0).mean()
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win": win, "dd": dd}


def run_breakout(
    df: pd.DataFrame,
    k: float,
    fee_rate: float,
    ma_window: int | None = None,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    data = df.copy()
    data["prev_range"] = data["high"].shift(1) - data["low"].shift(1)
    data["target"] = data["open"] + data["prev_range"] * k
    data["ma"] = data["close"].rolling(ma_window).mean() if ma_window else np.nan

    valid = data["prev_range"].notna() & data["open"].notna() & data["high"].notna() & data["close"].notna()
    if ma_window:
        valid &= data["close"].shift(1) > data["ma"].shift(1)

    data["entry"] = valid & (data["high"] >= data["target"])
    data["daily_ret"] = 0.0
    gross_ret = data["close"] / data["target"] - 1
    data.loc[data["entry"], "daily_ret"] = gross_ret.loc[data["entry"]] - fee_rate
    data["nav"] = (1 + data["daily_ret"].fillna(0)).cumprod()
    data["state"] = np.where(data["entry"], "매수", "현금")

    trades = data.loc[data["entry"], ["target", "close", "daily_ret"]].copy()
    trades = trades.rename(columns={"target": "매수가", "close": "청산가", "daily_ret": "수익률"})
    trades.insert(0, "날짜", trades.index.date)
    trades["K"] = k
    return data["nav"].rename("전략"), data["state"].rename("상태"), trades.reset_index(drop=True)


def optimize_k(
    df: pd.DataFrame,
    k_values: list[float],
    fee_rate: float,
    metric: str,
    ma_window: int | None,
) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for k in k_values:
        nav, _, trades = run_breakout(df, k, fee_rate, ma_window)
        metrics = calc_metrics(nav)
        rows.append(
            {
                "K": k,
                "총수익률": metrics["total"],
                "연수익률": metrics["cagr"],
                "MDD": metrics["mdd"],
                "Sharpe": metrics["sharpe"],
                "Calmar": metrics["calmar"],
                "승률": metrics["win"],
                "거래횟수": len(trades),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    if metric == "MDD 최소":
        return result.sort_values(["MDD", "연수익률"], ascending=[False, False])
    return result.sort_values(metric, ascending=False)


with st.sidebar:
    st.header("설정")
    st.subheader("종목")
    selected_name = st.selectbox("대상 ETF", list(ETF_OPTIONS.keys()), index=0)
    ticker = ETF_OPTIONS[selected_name]

    st.subheader("기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2016, 1, 4))
    with c2:
        end_date = st.date_input("종료", datetime.today().date())

    st.subheader("K값")
    use_optimized_k = st.checkbox("최근 3년 최적 K 사용", value=True)
    manual_k = st.slider("수동 K", 0.05, 1.50, 0.50, 0.05)
    k_min = st.number_input("최적화 K 최소", min_value=0.05, max_value=1.50, value=0.10, step=0.05)
    k_max = st.number_input("최적화 K 최대", min_value=0.05, max_value=1.50, value=1.00, step=0.05)
    k_step = st.number_input("최적화 K 간격", min_value=0.01, max_value=0.25, value=0.05, step=0.01)
    optimize_metric = st.selectbox("최적화 기준", ["연수익률", "Sharpe", "Calmar", "총수익률", "MDD 최소"], index=1)

    st.subheader("필터")
    use_ma_filter = st.checkbox("이동평균 상승장 필터 사용", value=False)
    ma_window = st.slider("MA", 20, 250, 120, 5) if use_ma_filter else None

    st.subheader("거래비용")
    fee = st.number_input("왕복 거래비용 (%)", value=0.15, step=0.05, min_value=0.0) / 100
    run_btn = st.button("시뮬레이션 실행", type="primary", use_container_width=True)


with st.expander("전략 조건", expanded=False):
    st.markdown(
        f"""
| 항목 | 내용 |
|---|---|
| 매수 기준가 | 당일 시가 + 전일 변동폭(고가-저가) × K |
| 진입 조건 | 당일 고가가 매수 기준가 이상 |
| 청산 조건 | 당일 종가 청산 |
| 대상 종목 | {selected_name} ({ticker}) |
| K 최적화 | 종료일 기준 최근 3년 |
"""
    )

if not run_btn:
    st.info("왼쪽 설정을 확인한 뒤 시뮬레이션을 실행하세요.")
    st.stop()

if k_min > k_max:
    st.error("최적화 K 최소값은 최대값보다 작거나 같아야 합니다.")
    st.stop()

start_str = start_date.strftime("%Y%m%d")
end_str = end_date.strftime("%Y%m%d")
warmup_days = max(int((ma_window or 0) * 2), 10)
extended_start = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text=f"{selected_name} 데이터를 불러오는 중...")
ohlcv = load_krx_ohlcv(ticker, extended_start, end_str)
progress.progress(35, text="데이터 정리 중...")

if ohlcv.empty:
    st.error("일봉 데이터를 불러오지 못했습니다. pykrx 또는 KRX 데이터 연결을 확인하세요.")
    st.stop()

df = ohlcv[(ohlcv.index.date >= start_date) & (ohlcv.index.date <= end_date)].copy()
if len(df) < 30:
    st.error("시뮬레이션에 필요한 일봉 데이터가 부족합니다.")
    st.stop()

opt_start = pd.Timestamp(end_date) - pd.DateOffset(years=3)
opt_df = ohlcv[(ohlcv.index >= opt_start) & (ohlcv.index.date <= end_date)].copy()
k_values = np.round(np.arange(k_min, k_max + k_step / 2, k_step), 4).tolist()

progress.progress(60, text="최근 3년 K값 최적화 중...")
opt_table = optimize_k(opt_df, k_values, fee, optimize_metric, ma_window)
if opt_table.empty:
    st.error("K값 최적화 결과를 만들지 못했습니다.")
    st.stop()

best_k = float(opt_table.iloc[0]["K"])
selected_k = best_k if use_optimized_k else manual_k

progress.progress(80, text="전체 기간 시뮬레이션 계산 중...")
nav_s, state_s, trade_log = run_breakout(df, selected_k, fee, ma_window)
benchmark = df["close"] / df["close"].iloc[0]
strategy_metrics = calc_metrics(nav_s)
benchmark_metrics = calc_metrics(benchmark)
progress.progress(100, text="완료")
progress.empty()

st.success(
    f"선택 종목: {selected_name} ({ticker}) | 적용 K: {selected_k:.2f} "
    f"| 최근 3년 최적 K: {best_k:.2f} ({optimize_metric} 기준)"
)

cols = st.columns(6)
cols[0].metric("총 수익률", f"{strategy_metrics['total']:.1%}", f"BM {benchmark_metrics['total']:.1%}")
cols[1].metric("연 수익률", f"{strategy_metrics['cagr']:.1%}", f"BM {benchmark_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"BM {benchmark_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"BM {benchmark_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}", f"BM {benchmark_metrics['calmar']:.2f}")
cols[5].metric("승률", f"{strategy_metrics['win']:.1%}")

trade_count = len(trade_log)
backtest_years = max((nav_s.index[-1] - nav_s.index[0]).days / 365.25, 1 / 365.25)
trade_cols = st.columns(3)
trade_cols[0].metric("총 매매횟수", f"{trade_count:,}회")
trade_cols[1].metric("연평균 매매횟수", f"{trade_count / backtest_years:.1f}회/년")
trade_cols[2].metric("최근 매매일", str(trade_log["날짜"].iloc[-1]) if trade_count else "-")

tab_chart, tab_opt, tab_trades, tab_monthly = st.tabs(["성과", "K 최적화", "거래", "월별 수익률"])

with tab_chart:
    st.subheader("누적 수익률")
    nav_chart = pd.DataFrame({"전략": nav_s / nav_s.iloc[0] - 1, f"{selected_name} B&H": benchmark / benchmark.iloc[0] - 1}) * 100
    st.line_chart(nav_chart, height=360)

    st.subheader("연도별 수익률 비교")
    yearly_strategy = (1 + nav_s.pct_change().fillna(0)).groupby(nav_s.index.year).prod() - 1
    yearly_benchmark = (1 + benchmark.pct_change().fillna(0)).groupby(benchmark.index.year).prod() - 1
    yearly_returns = pd.DataFrame({"전략": yearly_strategy, f"{selected_name} B&H": yearly_benchmark}).dropna(how="all") * 100
    yearly_returns.index = yearly_returns.index.astype(str)
    fig_yearly = go.Figure()
    fig_yearly.add_trace(go.Bar(x=yearly_returns.index, y=yearly_returns["전략"], name="전략", marker_color="#1f77b4", text=[f"{v:.1f}%" for v in yearly_returns["전략"]], textposition="outside"))
    fig_yearly.add_trace(go.Bar(x=yearly_returns.index, y=yearly_returns[f"{selected_name} B&H"], name=f"{selected_name} B&H", marker_color="#ff7f0e", text=[f"{v:.1f}%" for v in yearly_returns[f"{selected_name} B&H"]], textposition="outside"))
    fig_yearly.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_yearly.update_layout(barmode="group", yaxis_title="수익률(%)", yaxis_ticksuffix="%", legend=dict(orientation="h", y=1.08, x=1, xanchor="right"), height=360, margin=dict(t=50, b=20))
    st.plotly_chart(fig_yearly, use_container_width=True)

    st.subheader("드로다운")
    dd_chart = pd.DataFrame({"전략 DD": strategy_metrics["dd"], f"{selected_name} DD": benchmark_metrics["dd"]}) * 100
    st.line_chart(dd_chart, height=260)

with tab_opt:
    st.subheader("최근 3년 K 최적화 결과")
    shown_opt = opt_table.copy()
    for col in ["총수익률", "연수익률", "MDD", "승률"]:
        shown_opt[col] = shown_opt[col].map(lambda x: f"{x:.1%}")
    shown_opt["Sharpe"] = shown_opt["Sharpe"].map(lambda x: f"{x:.2f}")
    shown_opt["Calmar"] = shown_opt["Calmar"].map(lambda x: f"{x:.2f}")
    st.dataframe(shown_opt, use_container_width=True, hide_index=True)
    fig_k = go.Figure()
    fig_k.add_trace(go.Scatter(x=opt_table["K"], y=opt_table["연수익률"] * 100, mode="lines+markers", name="연수익률"))
    fig_k.add_trace(go.Scatter(x=opt_table["K"], y=opt_table["MDD"] * 100, mode="lines+markers", name="MDD"))
    fig_k.update_layout(height=320, yaxis_title="수익률(%)", yaxis_ticksuffix="%")
    st.plotly_chart(fig_k, use_container_width=True)

with tab_trades:
    st.subheader("돌파 매매 내역")
    if trade_log.empty:
        st.info("매매가 발생하지 않았습니다.")
    else:
        shown = trade_log.copy()
        shown["매수가"] = shown["매수가"].map(lambda x: f"{x:,.0f}")
        shown["청산가"] = shown["청산가"].map(lambda x: f"{x:,.0f}")
        shown["수익률"] = shown["수익률"].map(lambda x: f"{x:.2%}")
        st.dataframe(shown.tail(200), use_container_width=True, hide_index=True)
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "larry_williams_breakout_trades.csv", "text/csv")

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_benchmark = benchmark.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame({"전략": monthly_strategy, selected_name: monthly_benchmark}).dropna()
    pivot_source = monthly_strategy.to_frame("수익률")
    pivot_source["연도"] = pivot_source.index.year
    pivot_source["월"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="연도", columns="월", values="수익률")
    pivot.columns = [f"{month}월" for month in pivot.columns]
    pivot["연간"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    st.subheader(f"월별 전략 vs {selected_name}")
    st.line_chart(monthly, height=260)
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "날짜"}).to_csv(index=False).encode("utf-8-sig"), "larry_williams_breakout_monthly.csv", "text/csv")
