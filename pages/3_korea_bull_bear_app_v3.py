"""KODEX 200 / KODEX leverage trend + volatility filter backtest v3.

This page intentionally ignores the older v2 bull/bear/TNX logic.
The strategy is simple:
- Buy KODEX Leverage when KODEX 200 is above its moving average and realized volatility is below the threshold.
- Hold cash otherwise.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

warnings.filterwarnings("ignore")

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252

st.set_page_config(page_title="KODEX Trend + Vol Filter v3", page_icon="KR", layout="wide")
st.title("KODEX 200 / 레버리지 추세 + 변동성 필터 v3")
st.caption("기본값: KODEX 200 > 100일선 AND KODEX 200 RV20 < 50% 이면 KODEX 레버리지, 아니면 현금")


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
            "open": pd.to_numeric(raw["시가"], errors="coerce"),
            "high": pd.to_numeric(raw["고가"], errors="coerce"),
            "low": pd.to_numeric(raw["저가"], errors="coerce"),
            "close": pd.to_numeric(raw["종가"], errors="coerce"),
            "volume": pd.to_numeric(raw["거래량"], errors="coerce"),
        }
    )
    df = normalize_index(df).dropna(how="all")
    return df.where(df > 0)


def calc_metrics(nav: pd.Series) -> dict[str, object]:
    nav = nav.replace([np.inf, -np.inf], np.nan).dropna()
    nav = nav[nav > 0]
    ret = finite_return(nav.pct_change()).dropna()
    if len(nav) < 2:
        return {
            "total": 0.0,
            "cagr": 0.0,
            "mdd": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "win_m": 0.0,
            "dd": pd.Series(dtype=float),
        }

    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = ret.mean() / ret.std() * np.sqrt(TRADING_DAYS) if ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "calmar": calmar, "win_m": win_m, "dd": dd}


def backtest_next_open(
    dates: pd.DatetimeIndex,
    signal: pd.Series,
    leverage_weight: float,
    ret_lev_co: pd.Series,
    ret_lev_oc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    prev_weight = 0.0
    nav_rows: list[float] = []
    weight_rows: list[float] = []
    trades: list[dict[str, object]] = []
    executable_weight = signal.shift(1).reindex(dates).fillna(False).astype(float) * leverage_weight

    for i, date in enumerate(dates):
        nav *= 1 + prev_weight * ret_lev_co.loc[date]

        new_weight = float(executable_weight.loc[date])
        turnover = abs(new_weight - prev_weight)
        if i > 0 and turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * turnover, 0.0), 0.99)
            trades.append(
                {
                    "날짜": date.date(),
                    "체결": "다음날 시가",
                    "이전 레버리지 비중": prev_weight,
                    "신규 레버리지 비중": new_weight,
                    "거래회전율": turnover,
                    "비용차감": before_fee - nav,
                    "NAV": nav,
                }
            )

        prev_weight = new_weight
        nav *= 1 + prev_weight * ret_lev_oc.loc[date]
        nav_rows.append(nav)
        weight_rows.append(prev_weight)

    return pd.Series(nav_rows, index=dates, name="전략"), pd.Series(weight_rows, index=dates, name="레버리지 비중"), pd.DataFrame(trades)


def backtest_close_to_close(
    dates: pd.DatetimeIndex,
    signal: pd.Series,
    leverage_weight: float,
    ret_lev_cc: pd.Series,
    fee_rate: float,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    nav = 1.0
    prev_weight = 0.0
    nav_rows: list[float] = []
    weight_rows: list[float] = []
    trades: list[dict[str, object]] = []
    target_weight = signal.reindex(dates).fillna(False).astype(float) * leverage_weight

    for date in dates:
        nav *= 1 + prev_weight * ret_lev_cc.loc[date]
        new_weight = float(target_weight.loc[date])
        turnover = abs(new_weight - prev_weight)
        if turnover > 0:
            before_fee = nav
            nav *= 1 - min(max(fee_rate * turnover, 0.0), 0.99)
            trades.append(
                {
                    "날짜": date.date(),
                    "체결": "당일 종가",
                    "이전 레버리지 비중": prev_weight,
                    "신규 레버리지 비중": new_weight,
                    "거래회전율": turnover,
                    "비용차감": before_fee - nav,
                    "NAV": nav,
                }
            )
        prev_weight = new_weight
        nav_rows.append(nav)
        weight_rows.append(prev_weight)

    return pd.Series(nav_rows, index=dates, name="전략"), pd.Series(weight_rows, index=dates, name="레버리지 비중"), pd.DataFrame(trades)


def plot_yearly_returns(strategy_nav: pd.Series, kodex200_nav: pd.Series, leverage_nav: pd.Series) -> go.Figure:
    yearly = pd.DataFrame(
        {
            "전략": (1 + strategy_nav.pct_change().fillna(0)).groupby(strategy_nav.index.year).prod() - 1,
            "KODEX 200": (1 + kodex200_nav.pct_change().fillna(0)).groupby(kodex200_nav.index.year).prod() - 1,
            "KODEX 레버리지": (1 + leverage_nav.pct_change().fillna(0)).groupby(leverage_nav.index.year).prod() - 1,
        }
    ).dropna(how="all") * 100
    yearly.index = yearly.index.astype(str)

    fig = go.Figure()
    for name, color in [("전략", "#0F766E"), ("KODEX 200", "#2563EB"), ("KODEX 레버리지", "#DC2626")]:
        fig.add_trace(go.Bar(x=yearly.index, y=yearly[name], name=name, marker_color=color, text=[f"{v:.1f}%" for v in yearly[name]], textposition="outside"))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(barmode="group", yaxis_title="수익률 (%)", yaxis_ticksuffix="%", legend=dict(orientation="h", y=1.08, x=1, xanchor="right"), height=380, margin=dict(t=50, b=20))
    return fig


with st.sidebar:
    st.header("전략 설정")
    st.subheader("기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2016, 5, 16))
    with c2:
        end_date = st.date_input("종료", datetime.today().date())

    st.subheader("기본 조건")
    ma_window = st.slider("KODEX 200 이동평균", 20, 250, 100, 5)
    vol_window = st.slider("실현 변동성 기간", 5, 120, 20, 1)
    vol_threshold_pct = st.slider("실현 변동성 상한 (%)", 10, 120, 50, 5)
    vol_source = st.selectbox("변동성 기준", ["KODEX 200", "KODEX 레버리지"], index=0)

    st.subheader("투입 비중")
    leverage_weight_pct = st.slider("신호 통과 시 KODEX 레버리지 비중 (%)", 0, 100, 100, 5)

    st.subheader("체결 및 비용")
    execution_mode = st.radio("체결 방식", ["다음날 시가", "당일 종가"], index=0)
    fee = st.number_input("거래대금당 비용 (%)", value=0.03, step=0.01, min_value=0.0) / 100
    run_btn = st.button("백테스트 실행", type="primary", use_container_width=True)

vol_threshold = vol_threshold_pct / 100
leverage_weight = leverage_weight_pct / 100

with st.expander("현재 전략 규칙", expanded=False):
    st.markdown(
        f"""
| 구분 | 조건 | 보유 |
|---|---|---|
| 진입/보유 | KODEX 200 종가 > MA{ma_window} AND {vol_source} RV{vol_window} < {vol_threshold_pct}% | KODEX 레버리지 {leverage_weight_pct}% + 현금 {100 - leverage_weight_pct}% |
| 대기 | 위 조건 미충족 | 현금 100% |

RV = 최근 N거래일 일간수익률 표준편차 × √252
"""
    )

if not run_btn:
    st.info("왼쪽 설정을 조정한 뒤 백테스트를 실행하세요. 기본값은 MA100 + RV20 < 50% 전략입니다.")
    st.stop()

start_str = start_date.strftime("%Y%m%d")
end_str = end_date.strftime("%Y%m%d")
warmup_days = max(ma_window, vol_window) * 3
extended_start_str = (start_date - timedelta(days=warmup_days)).strftime("%Y%m%d")

progress = st.progress(0, text="데이터를 불러오는 중...")
progress.progress(20, text="KODEX 200 데이터를 불러오는 중...")
kodex_200 = load_krx_ohlcv(KODEX_200, extended_start_str, end_str)
progress.progress(45, text="KODEX 레버리지 데이터를 불러오는 중...")
kodex_lev = load_krx_ohlcv(KODEX_LEVERAGE, extended_start_str, end_str)

if kodex_200.empty or kodex_lev.empty:
    st.error("KODEX ETF 데이터를 불러오지 못했습니다. pykrx 또는 KRX 데이터 연결을 확인하세요.")
    st.stop()

common_idx = kodex_200.index.intersection(kodex_lev.index)
common_idx = common_idx[(common_idx.date >= start_date) & (common_idx.date <= end_date)]
if len(common_idx) < 30:
    st.error("백테스트에 필요한 거래일 데이터가 부족합니다.")
    st.stop()

full_idx = kodex_200.index.intersection(kodex_lev.index)
full_idx = full_idx[full_idx <= common_idx[-1]]

kodex_close = kodex_200["close"].reindex(full_idx).ffill()
lev_close = kodex_lev["close"].reindex(full_idx).ffill()
ma = kodex_close.rolling(ma_window).mean()
vol_price = kodex_close if vol_source == "KODEX 200" else lev_close
realized_vol = finite_return(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
trend_ok = kodex_close > ma
vol_ok = realized_vol < vol_threshold
signal = (trend_ok & vol_ok).rename("매수 신호")

ret_lev_cc = finite_return(kodex_lev["close"].pct_change()).reindex(common_idx).fillna(0)
ret_lev_co = safe_divide(kodex_lev["open"] - kodex_lev["close"].shift(1), kodex_lev["close"].shift(1)).reindex(common_idx).fillna(0)
ret_lev_oc = safe_divide(kodex_lev["close"] - kodex_lev["open"], kodex_lev["open"]).reindex(common_idx).fillna(0)

progress.progress(80, text="백테스트 계산 중...")
if execution_mode == "다음날 시가":
    nav_s, weight_s, trade_log = backtest_next_open(common_idx, signal, leverage_weight, ret_lev_co, ret_lev_oc, fee)
else:
    nav_s, weight_s, trade_log = backtest_close_to_close(common_idx, signal, leverage_weight, ret_lev_cc, fee)

benchmark_200 = kodex_200["close"].reindex(common_idx).ffill()
benchmark_200 = benchmark_200 / benchmark_200.iloc[0]
benchmark_lev = kodex_lev["close"].reindex(common_idx).ffill()
benchmark_lev = benchmark_lev / benchmark_lev.iloc[0]
strategy_metrics = calc_metrics(nav_s)
benchmark_200_metrics = calc_metrics(benchmark_200)
benchmark_lev_metrics = calc_metrics(benchmark_lev)
progress.empty()

current_date = common_idx[-1].date()
latest_signal = bool(signal.reindex(common_idx).iloc[-1])
latest_weight = weight_s.iloc[-1]
latest_close = kodex_close.reindex(common_idx).iloc[-1]
latest_ma = ma.reindex(common_idx).iloc[-1]
latest_vol = realized_vol.reindex(common_idx).iloc[-1]

st.success(f"현재 상태 ({current_date}): {'레버리지 보유' if latest_weight > 0 else '현금 대기'} | KODEX 레버리지 {latest_weight:.0%}, 현금 {1 - latest_weight:.0%}")
st.caption(f"최신 원신호: {'통과' if latest_signal else '대기'} | KODEX 200 {latest_close:,.0f} / MA{ma_window} {latest_ma:,.0f} / {vol_source} RV{vol_window} {latest_vol:.1%} / 상한 {vol_threshold:.0%}")

cols = st.columns(6)
cols[0].metric("총 수익률", f"{strategy_metrics['total']:.1%}", f"KODEX200 {benchmark_200_metrics['total']:.1%}")
cols[1].metric("연 수익률", f"{strategy_metrics['cagr']:.1%}", f"LEV {benchmark_lev_metrics['cagr']:.1%}")
cols[2].metric("MDD", f"{strategy_metrics['mdd']:.1%}", f"LEV {benchmark_lev_metrics['mdd']:.1%}")
cols[3].metric("Sharpe", f"{strategy_metrics['sharpe']:.2f}", f"LEV {benchmark_lev_metrics['sharpe']:.2f}")
cols[4].metric("Calmar", f"{strategy_metrics['calmar']:.2f}")
cols[5].metric("월 승률", f"{strategy_metrics['win_m']:.1%}")

trade_count = len(trade_log)
years = max((nav_s.index[-1] - nav_s.index[0]).days / 365.25, 1 / 365.25)
trade_cols = st.columns(3)
trade_cols[0].metric("총 매매횟수", f"{trade_count:,}회")
trade_cols[1].metric("연평균 매매횟수", f"{trade_count / years:.1f}회/년")
trade_cols[2].metric("최근 매매일", str(trade_log["날짜"].iloc[-1]) if trade_count else "-")

tab_chart, tab_signal, tab_trades, tab_monthly = st.tabs(["성과", "신호", "거래", "월별 수익률"])

with tab_chart:
    st.subheader("누적 수익률")
    nav_chart = pd.DataFrame(
        {
            "전략": nav_s / nav_s.iloc[0] - 1,
            "KODEX 200 B&H": benchmark_200 / benchmark_200.iloc[0] - 1,
            "KODEX 레버리지 B&H": benchmark_lev / benchmark_lev.iloc[0] - 1,
        }
    ) * 100
    st.line_chart(nav_chart, height=360)

    st.subheader("연도별 수익률")
    st.plotly_chart(plot_yearly_returns(nav_s, benchmark_200, benchmark_lev), use_container_width=True)

    st.subheader("낙폭")
    dd_chart = pd.DataFrame(
        {
            "전략 DD": strategy_metrics["dd"],
            "KODEX 200 DD": benchmark_200_metrics["dd"],
            "KODEX 레버리지 DD": benchmark_lev_metrics["dd"],
        }
    ) * 100
    st.line_chart(dd_chart, height=260)

with tab_signal:
    st.subheader("추세와 변동성")
    st.line_chart(pd.DataFrame({"KODEX 200": kodex_close.reindex(common_idx), f"MA{ma_window}": ma.reindex(common_idx)}), height=280)
    st.line_chart(pd.DataFrame({f"{vol_source} RV{vol_window}": realized_vol.reindex(common_idx) * 100, "상한": pd.Series(vol_threshold_pct, index=common_idx)}), height=260)

    st.subheader("최근 신호")
    signal_table = pd.DataFrame(
        {
            "매수 신호": signal.reindex(common_idx),
            "레버리지 비중": weight_s,
            "KODEX 200": kodex_close.reindex(common_idx),
            f"MA{ma_window}": ma.reindex(common_idx),
            f"RV{vol_window}": realized_vol.reindex(common_idx),
        }
    ).tail(40)
    st.dataframe(signal_table, use_container_width=True)

with tab_trades:
    st.subheader("매매 내역")
    if trade_log.empty:
        st.info("매매가 없었습니다.")
    else:
        shown = trade_log.copy()
        for col in ["이전 레버리지 비중", "신규 레버리지 비중", "거래회전율"]:
            shown[col] = shown[col].map(lambda x: f"{x:.1%}")
        shown["비용차감"] = shown["비용차감"].map(lambda x: f"{x:.4f}")
        shown["NAV"] = shown["NAV"].map(lambda x: f"{x:.4f}")
        st.dataframe(shown, use_container_width=True, hide_index=True)
        st.download_button("Trade Log CSV", trade_log.to_csv(index=False).encode("utf-8-sig"), "kodex_trend_vol_v3_trades.csv", "text/csv")

with tab_monthly:
    monthly_strategy = nav_s.resample("M").last().pct_change().dropna()
    monthly_200 = benchmark_200.resample("M").last().pct_change().dropna()
    monthly_lev = benchmark_lev.resample("M").last().pct_change().dropna()
    monthly = pd.DataFrame({"전략": monthly_strategy, "KODEX 200": monthly_200, "KODEX 레버리지": monthly_lev}).dropna()

    pivot_source = monthly_strategy.to_frame("수익률")
    pivot_source["연도"] = pivot_source.index.year
    pivot_source["월"] = pivot_source.index.month
    pivot = pivot_source.pivot(index="연도", columns="월", values="수익률")
    pivot.columns = [f"{month}월" for month in pivot.columns]
    pivot["연간"] = (1 + monthly_strategy).groupby(monthly_strategy.index.year).prod() - 1
    st.dataframe(pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-"), use_container_width=True)
    st.line_chart(monthly, height=260)
    st.download_button("Monthly Returns CSV", monthly.reset_index().rename(columns={"index": "날짜"}).to_csv(index=False).encode("utf-8-sig"), "kodex_trend_vol_v3_monthly.csv", "text/csv")
