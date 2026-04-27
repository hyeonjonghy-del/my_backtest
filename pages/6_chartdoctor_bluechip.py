import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from pykrx import stock
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="차트박사 우량주 백테스트", page_icon="📊", layout="wide")

st.title("📊 6. 차트박사 절대수익 우량주 매매법 백테스트")
st.caption("라운드넘버존 진입 · 역피라미딩 분할매수 · 연간 유니버스 리밸런싱 · NAV 기반 동적 포지션")

# ────────────────────────────────────────────────────────────
# 사이드바
# ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 기본 설정")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("시작일", datetime(2015, 1, 1))
    with col2:
        end_date = st.date_input("종료일", datetime(2024, 12, 31))

    min_cap_억   = st.number_input("최소 시총 (억원)", value=5000, step=1000)
    initial_capital = st.number_input("초기 자본 (만원)", value=3000, step=500) * 10000
    max_stocks   = st.slider("연간 종목 수 (상위 N개)", 10, 100, 50)

    st.divider()
    st.header("📐 매매 기준")

    st.markdown("**① 라운드넘버존 진입**")
    trigger_pct = st.slider("트리거: 다음 라운드 - x%", 1, 8, 4) / 100
    buy1_pct    = st.slider("1차 매수: 이전 라운드 + x%", 1, 8, 4) / 100

    st.markdown("**② 자금 관리 (NAV 기준 비중)**")
    buy1_cap_pct = st.slider("1차 매수 비중 (현재 NAV %)", 5, 20, 10) / 100
    add_drop_pct = st.slider("추가매수 트리거 하락폭 (%)", 5, 20, 10) / 100
    st.caption("2차 = 1차 금액 ×2 / 3차 = 2차 금액 동일")
    st.info(
        f"💰 총 투입 비중 (NAV 기준)\n"
        f"- 1차: {buy1_cap_pct*100:.0f}%\n"
        f"- 2차: {buy1_cap_pct*2*100:.0f}%\n"
        f"- 3차: {buy1_cap_pct*2*100:.0f}%\n"
        f"- **합계: {buy1_cap_pct*5*100:.0f}%**"
    )

    st.markdown("**③ 청산 조건**")
    target_pct   = st.slider("목표 수익률: 평균단가 + x%", 5, 40, 15) / 100
    stoploss_pct = st.slider("손절 기준: 평균단가 - x% (3차 이후만)", 3, 30, 5) / 100
    st.caption("⚠️ 손절은 3차 매수 완료 이후에만 발동")

    rr_ratio     = target_pct / stoploss_pct
    breakeven_wr = stoploss_pct / (target_pct + stoploss_pct) * 100
    color = "🟢" if rr_ratio >= 2.0 else ("🟡" if rr_ratio >= 1.5 else "🔴")
    st.markdown(
        f"**손익비 분석** {color}\n"
        f"| 항목 | 값 |\n|---|---|\n"
        f"| 손익비 (R/R) | **{rr_ratio:.1f} : 1** |\n"
        f"| 손익분기 승률 | **{breakeven_wr:.0f}%** |"
    )
    if rr_ratio >= 2.0:
        st.success(f"✅ 승률 {breakeven_wr:.0f}%만 넘으면 수익")
    elif rr_ratio >= 1.5:
        st.warning(f"⚠️ 승률 {breakeven_wr:.0f}% 이상 필요")
    else:
        st.error("❌ 손익비 불리")

    st.divider()
    st.markdown("**라운드넘버 단위 (자동)**")
    st.code(
        "~  5,000원  → 1,000원 단위\n"
        "~ 50,000원  → 5,000원 단위\n"
        "~100,000원  → 10,000원 단위\n"
        "~500,000원  → 50,000원 단위\n"
        " 500,000원~ → 100,000원 단위",
        language=None
    )

# ────────────────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────────────────
def get_round_unit(price: float) -> int:
    if price < 5_000:     return 1_000
    elif price < 50_000:  return 5_000
    elif price < 100_000: return 10_000
    elif price < 500_000: return 50_000
    else:                 return 100_000

def get_round_numbers(price: float):
    unit   = get_round_unit(price)
    prev_r = int(price // unit) * unit
    if prev_r == 0:
        prev_r = unit
    return prev_r, prev_r + unit

# ────────────────────────────────────────────────────────────
# 데이터 수집
# ────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tickers_for_date(date_str: str, min_cap: int, n: int) -> list:
    base_dt = datetime.strptime(date_str, "%Y%m%d")
    for offset in range(0, 10):
        for sign in [1, -1]:
            cand = (base_dt + timedelta(days=offset * sign)).strftime("%Y%m%d")
            try:
                cap_df = stock.get_market_cap(cand, market="KOSPI")
                if cap_df is None or cap_df.empty or "시가총액" not in cap_df.columns:
                    continue
                filtered = cap_df[cap_df["시가총액"] >= min_cap * 1e8]
                filtered = filtered.sort_values("시가총액", ascending=False)
                tickers  = filtered.index.tolist()[:n]
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
    except:
        return pd.DataFrame()

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker)
    except:
        return ticker

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_kospi_index(start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_index_ohlcv_by_date(start_str, end_str, "1001")
        df.index = pd.to_datetime(df.index)
        return df["종가"]
    except:
        return pd.Series(dtype=float)

# ────────────────────────────────────────────────────────────
# 포트폴리오 레벨 백테스트 (날짜 기준, NAV 동적 포지션)
# ────────────────────────────────────────────────────────────
def run_portfolio_backtest(ticker_dfs: dict, yearly_universe: dict, ticker_names: dict):
    """
    날짜 순으로 전체 종목을 함께 처리.
    포지션 사이즈 = 그 날의 current_nav × 비중
    → 시작 기간이 달라도 같은 연도의 수익률이 동일하게 계산됨
    """
    current_nav = float(initial_capital)

    # 종목별 상태
    states     = {}   # ticker -> "IDLE" | "TRIGGERED" | "IN_TRADE"
    prev_r_map = {}   # ticker -> setup_prev_r
    positions  = {}   # ticker -> {shares, avg_cost, tranches, buy1_price, buy2_price, entry_date}

    all_trades = []
    nav_series = {}

    all_dates = pd.date_range(start=start_date, end=end_date, freq="B")

    for date in all_dates:
        year     = date.year
        day_pnl  = 0.0

        # ── 1. 보유 포지션 처리 ─────────────────────────────
        for ticker in list(positions.keys()):
            df = ticker_dfs.get(ticker)
            if df is None or date not in df.index:
                continue

            row = df.loc[date]
            h, l = row["고가"], row["저가"]
            pos  = positions[ticker]
            avg  = pos["avg_cost"]
            tgt  = avg * (1 + target_pct)

            # 손절: 3차 이후만
            closed = False
            if pos["tranches"] == 3:
                stop = avg * (1 - stoploss_pct)
                if l <= stop:
                    pnl = (stop - avg) * pos["shares"]
                    day_pnl += pnl
                    all_trades.append({
                        "종목코드": ticker,
                        "종목명":   ticker_names.get(ticker, ticker),
                        "진입일":   pos["entry_date"],
                        "청산일":   date,
                        "1차매수가": round(pos["buy1_price"]),
                        "평균단가": round(avg),
                        "청산가":   round(stop),
                        "수익률":   -stoploss_pct,
                        "손익(원)": pnl,
                        "청산사유": "손절(3차후)",
                        "매수횟수": pos["tranches"],
                    })
                    del positions[ticker]
                    states[ticker] = "IDLE"
                    closed = True

            # 목표수익
            if not closed and h >= tgt:
                pnl = (tgt - avg) * pos["shares"]
                day_pnl += pnl
                all_trades.append({
                    "종목코드": ticker,
                    "종목명":   ticker_names.get(ticker, ticker),
                    "진입일":   pos["entry_date"],
                    "청산일":   date,
                    "1차매수가": round(pos["buy1_price"]),
                    "평균단가": round(avg),
                    "청산가":   round(tgt),
                    "수익률":   target_pct,
                    "손익(원)": pnl,
                    "청산사유": "목표수익",
                    "매수횟수": pos["tranches"],
                })
                del positions[ticker]
                states[ticker] = "IDLE"
                closed = True

            if closed:
                continue

            # 2차 추가매수
            if pos["tranches"] == 1 and pos.get("buy1_price"):
                b2t = pos["buy1_price"] * (1 - add_drop_pct)
                if l <= b2t:
                    buy2_amount = current_nav * buy1_cap_pct * 2
                    ns = int(buy2_amount / b2t)
                    if ns > 0:
                        total = pos["avg_cost"] * pos["shares"] + b2t * ns
                        pos["shares"]    += ns
                        pos["avg_cost"]   = total / pos["shares"]
                        pos["tranches"]   = 2
                        pos["buy2_price"] = b2t

            # 3차 추가매수
            elif pos["tranches"] == 2 and pos.get("buy2_price"):
                b3t = pos["buy2_price"] * (1 - add_drop_pct)
                if l <= b3t:
                    buy3_amount = current_nav * buy1_cap_pct * 2
                    ns = int(buy3_amount / b3t)
                    if ns > 0:
                        total = pos["avg_cost"] * pos["shares"] + b3t * ns
                        pos["shares"]  += ns
                        pos["avg_cost"] = total / pos["shares"]
                        pos["tranches"] = 3

        # ── NAV 업데이트 (청산 손익 반영) ───────────────────
        current_nav += day_pnl
        nav_series[date] = current_nav

        # ── 2. 신규 진입 탐색 ───────────────────────────────
        for ticker in yearly_universe.get(year, set()):
            if ticker in positions:
                continue
            df = ticker_dfs.get(ticker)
            if df is None or date not in df.index:
                continue

            row   = df.loc[date]
            h, l, c = row["고가"], row["저가"], row["종가"]
            state = states.get(ticker, "IDLE")

            if state == "IDLE":
                pr, nr = get_round_numbers(c)
                if h >= nr * (1 - trigger_pct):
                    states[ticker]    = "TRIGGERED"
                    prev_r_map[ticker] = pr

            if states.get(ticker) == "TRIGGERED":
                cur_pr, _ = get_round_numbers(c)
                if cur_pr != prev_r_map.get(ticker):
                    states[ticker] = "IDLE"
                else:
                    b1 = prev_r_map[ticker] * (1 + buy1_pct)
                    if l <= b1:
                        buy1_amount = current_nav * buy1_cap_pct
                        ns = int(buy1_amount / b1)
                        if ns > 0:
                            positions[ticker] = {
                                "shares":     ns,
                                "avg_cost":   b1,
                                "tranches":   1,
                                "entry_date": date,
                                "buy1_price": b1,
                                "buy2_price": None,
                            }
                            states[ticker] = "IN_TRADE"

    return all_trades, pd.Series(nav_series)

# ────────────────────────────────────────────────────────────
# 실행
# ────────────────────────────────────────────────────────────
st.divider()
run_btn = st.button("🚀 백테스트 실행", type="primary", use_container_width=True)

if run_btn:
    start_str = start_date.strftime("%Y%m%d")
    end_str   = end_date.strftime("%Y%m%d")

    # ── 연간 유니버스 구축 ────────────────────────────────────
    st.info("📅 연간 유니버스 구축 중...")
    yearly_universe = {}
    all_tickers_set = set()
    years = list(range(start_date.year, end_date.year + 1))

    for yr in years:
        tickers_yr = fetch_tickers_for_date(f"{yr}0104", min_cap_억, max_stocks)
        yearly_universe[yr] = set(tickers_yr)
        all_tickers_set.update(tickers_yr)

    with st.expander("📋 연간 유니버스 현황", expanded=False):
        univ_rows = []
        for yr in years:
            prev = yearly_universe.get(yr - 1, set())
            curr = yearly_universe[yr]
            univ_rows.append({
                "연도": yr, "종목수": len(curr),
                "신규편입": len(curr - prev), "제외됨": len(prev - curr),
            })
        st.dataframe(pd.DataFrame(univ_rows), use_container_width=True, hide_index=True)

    all_tickers = sorted(all_tickers_set)
    st.info(f"✅ 전체 분석 대상: {len(all_tickers)}개 종목 (연간 합집합)")

    # ── OHLCV 수집 ──────────────────────────────────────────
    prog_bar  = st.progress(0, text="데이터 수집 중...")
    ticker_dfs    = {}
    ticker_names  = {}

    for i, ticker in enumerate(all_tickers):
        name = fetch_ticker_name(ticker)
        ticker_names[ticker] = name
        df = fetch_ohlcv(ticker, start_str, end_str)
        if not df.empty and len(df) >= 60:
            ticker_dfs[ticker] = df
        prog_bar.progress((i + 1) / len(all_tickers),
                          text=f"데이터 수집: {name}({ticker}) [{i+1}/{len(all_tickers)}]")
        time.sleep(0.02)

    prog_bar.empty()
    st.info(f"📦 데이터 수집 완료: {len(ticker_dfs)}개 종목")

    # ── 포트폴리오 백테스트 실행 ─────────────────────────────
    with st.spinner("🔄 포트폴리오 시뮬레이션 실행 중... (날짜 기준 NAV 동적 포지션)"):
        all_trades, strat_nav = run_portfolio_backtest(
            ticker_dfs, yearly_universe, ticker_names
        )

    if not all_trades:
        st.warning("⚠️ 조건을 충족하는 거래가 없습니다.")
        st.stop()

    # ── 결과 집계 ─────────────────────────────────────────────
    rdf = pd.DataFrame(all_trades)
    rdf = rdf.sort_values("청산일").reset_index(drop=True)
    rdf["진입일"] = pd.to_datetime(rdf["진입일"])
    rdf["청산일"] = pd.to_datetime(rdf["청산일"])

    total     = len(rdf)
    wins      = (rdf["수익률"] > 0).sum()
    win_rate  = wins / total * 100
    avg_ret   = rdf["수익률"].mean() * 100
    total_pnl = rdf["손익(원)"].sum()
    avg_hold  = (rdf["청산일"] - rdf["진입일"]).dt.days.mean()
    stop_mask = rdf["청산사유"].str.contains("손절")
    stop_cnt  = stop_mask.sum()
    pf_num    = rdf.loc[rdf["손익(원)"] > 0, "손익(원)"].sum()
    pf_den    = abs(rdf.loc[rdf["손익(원)"] < 0, "손익(원)"].sum()) + 1e-9
    profit_factor = pf_num / pf_den

    total_return  = total_pnl / initial_capital
    years_n = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 365.25
    cagr    = (1 + total_return) ** (1 / years_n) - 1 if years_n > 0 else 0
    final_capital = initial_capital + total_pnl

    # NAV 파생 지표
    strat_ret    = (strat_nav / initial_capital - 1) * 100
    roll_max     = strat_nav.expanding().max()
    strat_mdd    = (strat_nav / roll_max - 1) * 100
    strategy_mdd = strat_mdd.min()

    kospi_s = fetch_kospi_index(start_str, end_str)
    if not kospi_s.empty:
        kospi_ret     = (kospi_s / kospi_s.iloc[0] - 1) * 100
        kospi_mdd_raw = kospi_s / kospi_s.expanding().max() - 1
        kospi_mdd_val = kospi_mdd_raw.min() * 100
        kospi_total   = float(kospi_ret.iloc[-1])
        kospi_cagr    = ((1 + kospi_total / 100) ** (1 / years_n) - 1) * 100
    else:
        kospi_ret = pd.Series(dtype=float)
        kospi_mdd_raw = pd.Series(dtype=float)
        kospi_mdd_val = kospi_total = kospi_cagr = 0.0

    # 월별·연도별 수익률
    monthly_nav  = strat_nav.resample("ME").last()
    monthly_ret  = monthly_nav.pct_change().dropna() * 100
    yearly_nav   = strat_nav.resample("YE").last()
    yearly_ret   = yearly_nav.pct_change().dropna() * 100

    mret_df = pd.DataFrame({
        "year": monthly_ret.index.year,
        "month": monthly_ret.index.month,
        "ret": monthly_ret.values,
    })
    if not mret_df.empty:
        monthly_pivot = mret_df.pivot(index="year", columns="month", values="ret")
        month_names = ["1월","2월","3월","4월","5월","6월",
                       "7월","8월","9월","10월","11월","12월"]
        monthly_pivot.columns = [month_names[m-1] for m in monthly_pivot.columns]
    else:
        monthly_pivot = pd.DataFrame()

    # ════════════════════════════════════════════════════════
    # 결과 요약
    # ════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📈 백테스트 결과 요약")

    st.markdown("**📊 기간 전체 성과**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("초기 자본",   f"{initial_capital/10000:,.0f}만원")
    c2.metric("최종 자산",   f"{final_capital/10000:,.0f}만원",
              delta=f"+{total_pnl/10000:,.0f}만원")
    c3.metric("전체 수익률", f"{total_return*100:.1f}%")
    c4.metric("연환산 CAGR", f"{cagr*100:.1f}%")

    st.divider()
    st.markdown("**🔁 거래 단위 성과**")
    c5, c6, c7, c8, c9 = st.columns(5)
    c5.metric("총 거래 수",      f"{total:,}건")
    c6.metric("승률",            f"{win_rate:.1f}%")
    c7.metric("거래당 평균수익", f"{avg_ret:.1f}%")
    c8.metric("Profit Factor",   f"{profit_factor:.2f}")
    c9.metric("평균 보유기간",   f"{avg_hold:.0f}일")

    st.divider()
    st.markdown("**🎯 청산 현황**")
    c10, c11, c12 = st.columns(3)
    c10.metric("목표수익 청산", f'{(rdf["청산사유"]=="목표수익").sum():,}건')
    c11.metric("손절 청산",     f"{stop_cnt:,}건")
    c12.metric("손절 비율",     f"{stop_cnt/total*100:.1f}%")

    st.divider()
    st.markdown("**📊 코스피 대비 성과**")
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("전략 전체 수익률", f"{total_return*100:.1f}%",
               delta=f"코스피 대비 {total_return*100 - kospi_total:+.1f}%p")
    cc2.metric("전략 CAGR",        f"{cagr*100:.1f}%",
               delta=f"코스피 대비 {cagr*100 - kospi_cagr:+.1f}%p")
    cc3.metric("전략 MDD",         f"{strategy_mdd:.1f}%",
               delta=f"코스피 대비 {strategy_mdd - kospi_mdd_val:+.1f}%p",
               delta_color="inverse")
    cc4.metric("코스피 MDD",       f"{kospi_mdd_val:.1f}%")

    # ════════════════════════════════════════════════════════
    # 탭
    # ════════════════════════════════════════════════════════
    tab1, tab2, tab3 = st.tabs(["📊 성과 분석", "📋 거래 로그", "📅 기간별 수익률"])

    # ── TAB 1: 성과 분석 ─────────────────────────────────────
    with tab1:
        fig_ret = go.Figure()
        fig_ret.add_trace(go.Scatter(
            x=strat_ret.index, y=strat_ret.values, mode="lines", name="전략",
            line=dict(color="#2196F3", width=2),
            hovertemplate="%{x|%Y-%m-%d}<br>전략: %{y:.1f}%<extra></extra>"
        ))
        if not kospi_ret.empty:
            fig_ret.add_trace(go.Scatter(
                x=kospi_ret.index, y=kospi_ret.values, mode="lines", name="KOSPI",
                line=dict(color="#FF9800", width=2, dash="dot"),
                hovertemplate="%{x|%Y-%m-%d}<br>KOSPI: %{y:.1f}%<extra></extra>"
            ))
        fig_ret.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
        fig_ret.update_layout(
            title="📈 누적 수익률 비교 (전략 vs KOSPI)",
            yaxis_ticksuffix="%",
            legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
            height=400, margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig_ret, use_container_width=True)

        fig_mdd = go.Figure()
        fig_mdd.add_trace(go.Scatter(
            x=strat_mdd.index, y=strat_mdd.values, mode="lines", name="전략 MDD",
            fill="tozeroy", fillcolor="rgba(33,150,243,0.15)",
            line=dict(color="#2196F3", width=2),
        ))
        if not kospi_mdd_raw.empty:
            fig_mdd.add_trace(go.Scatter(
                x=kospi_mdd_raw.index, y=(kospi_mdd_raw*100).values,
                mode="lines", name="KOSPI MDD",
                fill="tozeroy", fillcolor="rgba(255,152,0,0.10)",
                line=dict(color="#FF9800", width=2, dash="dot"),
            ))
        fig_mdd.update_layout(
            title="📉 최대낙폭(MDD) 비교",
            yaxis_ticksuffix="%",
            legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
            height=320, margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig_mdd, use_container_width=True)

        col_a, col_b = st.columns(2)
        with col_a:
            fig_dist = px.histogram(rdf, x="수익률", nbins=30,
                                    title="📊 수익률 분포",
                                    color_discrete_sequence=["#4CAF50"])
            fig_dist.update_xaxes(tickformat=".0%")
            fig_dist.add_vline(x=0, line_dash="dash", line_color="red")
            fig_dist.update_layout(height=320, margin=dict(t=40, b=20))
            st.plotly_chart(fig_dist, use_container_width=True)

        with col_b:
            reason_cnt = rdf["청산사유"].value_counts()
            fig_pie = px.pie(
                values=reason_cnt.values, names=reason_cnt.index,
                title="🎯 청산 사유 비율",
                color_discrete_map={"목표수익":"#4CAF50","손절(3차후)":"#F44336"},
                hole=0.4
            )
            fig_pie.update_layout(height=320, margin=dict(t=40, b=20))
            st.plotly_chart(fig_pie, use_container_width=True)

        st.subheader("🏆 종목별 성과 Top 15")
        by_stock = (
            rdf.groupby(["종목코드","종목명"])
            .agg(거래수=("수익률","count"),
                 승률=("수익률", lambda x: (x>0).mean()*100),
                 평균수익률=("수익률","mean"),
                 총손익=("손익(원)","sum"))
            .reset_index().sort_values("총손익", ascending=False).head(15)
        )
        by_stock["평균수익률"] = by_stock["평균수익률"].apply(lambda x: f"{x*100:.1f}%")
        by_stock["승률"]       = by_stock["승률"].apply(lambda x: f"{x:.0f}%")
        by_stock["총손익"]     = by_stock["총손익"].apply(lambda x: f"{x/10000:,.0f}만원")
        st.dataframe(by_stock, use_container_width=True, hide_index=True)

        tranche_cnt = rdf["매수횟수"].value_counts().sort_index()
        fig_bar = px.bar(
            x=[f"{i}차 매수" for i in tranche_cnt.index],
            y=tranche_cnt.values, title="📦 분할매수 횟수 분포",
            color_discrete_sequence=["#9C27B0"], text_auto=True
        )
        fig_bar.update_layout(height=280, margin=dict(t=40, b=20))
        st.plotly_chart(fig_bar, use_container_width=True)

        with st.expander("⚙️ 파라미터"):
            st.json({
                "기간": f"{start_date} ~ {end_date}",
                "최소시총(억)": min_cap_억,
                "연간종목수": max_stocks,
                "초기자본(만원)": initial_capital // 10000,
                "1차매수비중(%)": buy1_cap_pct * 100,
                "추가매수하락(%)": add_drop_pct * 100,
                "목표수익률(%)": target_pct * 100,
                "손절기준(%)": stoploss_pct * 100,
                "포지션사이징": "NAV 동적 (날짜 기준 포트폴리오 시뮬)",
                "리밸런싱": "연간 (매년 1월)",
            })

    # ── TAB 2: 거래 로그 ─────────────────────────────────────
    with tab2:
        st.subheader("📋 전체 거래 이력")
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            filter_reason = st.multiselect(
                "청산 사유", rdf["청산사유"].unique().tolist(),
                default=rdf["청산사유"].unique().tolist()
            )
        with fc2:
            filter_tranche = st.multiselect(
                "매수 횟수", sorted(rdf["매수횟수"].unique().tolist()),
                default=sorted(rdf["매수횟수"].unique().tolist())
            )
        with fc3:
            top_names = ["전체"] + rdf["종목명"].value_counts().head(20).index.tolist()
            filter_name = st.selectbox("종목 검색", top_names)

        filtered = rdf[rdf["청산사유"].isin(filter_reason) & rdf["매수횟수"].isin(filter_tranche)]
        if filter_name != "전체":
            filtered = filtered[filtered["종목명"] == filter_name]

        display = filtered[[
            "종목명","종목코드","진입일","청산일",
            "1차매수가","평균단가","청산가","수익률","손익(원)","청산사유","매수횟수"
        ]].copy()
        display["수익률"]   = display["수익률"].apply(lambda x: f"{x*100:.1f}%")
        display["손익(원)"] = display["손익(원)"].apply(lambda x: f"{x/10000:,.1f}만원")
        display["진입일"]   = display["진입일"].dt.strftime("%Y-%m-%d")
        display["청산일"]   = display["청산일"].dt.strftime("%Y-%m-%d")

        st.caption(f"총 {len(filtered):,}건 표시")
        st.dataframe(display, use_container_width=True, hide_index=True, height=600)
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("⬇️ CSV 다운로드", csv, "chartdoctor_trades.csv", "text/csv")

    # ── TAB 3: 기간별 수익률 ────────────────────────────────
    with tab3:
        st.subheader("📅 연도별 수익률")
        if not yearly_ret.empty:
            yr_labels = [str(y) for y in yearly_ret.index.year]
            yr_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in yearly_ret.values]

            fig_yr = go.Figure()
            fig_yr.add_trace(go.Bar(
                x=yr_labels, y=yearly_ret.values, marker_color=yr_colors,
                text=[f"{v:.1f}%" for v in yearly_ret.values],
                textposition="outside", name="전략"
            ))
            if not kospi_s.empty:
                kospi_yr = kospi_s.resample("YE").last().pct_change().dropna() * 100
                fig_yr.add_trace(go.Scatter(
                    x=[str(y) for y in kospi_yr.index.year],
                    y=kospi_yr.values, mode="lines+markers+text", name="KOSPI",
                    line=dict(color="#FF9800", width=2, dash="dot"), marker=dict(size=8),
                    text=[f"{v:.1f}%" for v in kospi_yr.values],
                    textposition="top center",
                ))
            fig_yr.add_hline(y=0, line_color="gray", line_dash="dash")
            fig_yr.update_layout(
                title="연도별 수익률 (전략 vs KOSPI)", yaxis_ticksuffix="%",
                legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
                height=420, margin=dict(t=50, b=20),
            )
            st.plotly_chart(fig_yr, use_container_width=True)

            if not kospi_s.empty:
                kospi_yr_map = {y: v for y, v in zip(kospi_yr.index.year, kospi_yr.values)}
            else:
                kospi_yr_map = {}

            yearly_table = []
            for yr_idx, val in zip(yearly_ret.index.year, yearly_ret.values):
                kv = kospi_yr_map.get(yr_idx)
                yearly_table.append({
                    "연도": yr_idx,
                    "전략": f"{val:.1f}%",
                    "코스피": f"{kv:.1f}%" if kv is not None else "-",
                    "초과수익": f"{val-kv:+.1f}%p" if kv is not None else "-",
                })
            st.dataframe(pd.DataFrame(yearly_table), use_container_width=True, hide_index=True)

        st.divider()

        st.subheader("🗓️ 월별 수익률 히트맵")
        if not monthly_pivot.empty:
            z_vals    = monthly_pivot.values.tolist()
            text_vals = [
                [f"{v:.1f}%" if not np.isnan(v) else "" for v in row]
                for row in monthly_pivot.values
            ]
            fig_heat = go.Figure(go.Heatmap(
                z=z_vals,
                x=monthly_pivot.columns.tolist(),
                y=[str(y) for y in monthly_pivot.index.tolist()],
                text=text_vals, texttemplate="%{text}",
                colorscale=[
                    [0.0,"#B71C1C"],[0.4,"#FFCDD2"],
                    [0.5,"#FFFFFF"],
                    [0.6,"#C8E6C9"],[1.0,"#1B5E20"],
                ],
                zmid=0, colorbar=dict(title="수익률(%)"),
                hovertemplate="%{y}년 %{x}<br>%{text}<extra></extra>",
            ))
            fig_heat.update_layout(
                title="월별 수익률 히트맵",
                height=max(300, len(monthly_pivot) * 42 + 120),
                margin=dict(t=50, b=20), xaxis=dict(side="top"),
            )
            st.plotly_chart(fig_heat, use_container_width=True)

            st.subheader("📊 월별 평균 수익률")
            monthly_avg = monthly_pivot.mean()
            fig_mavg = px.bar(
                x=monthly_avg.index, y=monthly_avg.values,
                color=[v >= 0 for v in monthly_avg.values],
                color_discrete_map={True:"#4CAF50", False:"#F44336"},
                text=[f"{v:.2f}%" for v in monthly_avg.values],
            )
            fig_mavg.update_traces(textposition="outside", showlegend=False)
            fig_mavg.update_layout(height=300, margin=dict(t=40,b=20), yaxis_ticksuffix="%")
            st.plotly_chart(fig_mavg, use_container_width=True)

            with st.expander("📋 월별 수익률 상세"):
                tbl = monthly_pivot.copy().round(2)
                tbl.index.name = "연도"
                st.dataframe(
                    tbl.style.format("{:.1f}%", na_rep="-")
                       .background_gradient(cmap="RdYlGn", axis=None),
                    use_container_width=True
                )
