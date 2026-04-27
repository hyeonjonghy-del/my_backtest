import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from pykrx import stock
from datetime import datetime
import time

st.set_page_config(page_title="차트박사 우량주 백테스트", page_icon="📊", layout="wide")

st.title("📊 6. 차트박사 절대수익 우량주 매매법 백테스트")
st.caption("라운드넘버존 기반 분할매수(3차) · +15% 전량매도 전략")

# ────────────────────────────────────────────────────────────
# 사이드바 파라미터
# ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 기본 설정")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("시작일", datetime(2021, 1, 1))
    with col2:
        end_date = st.date_input("종료일", datetime(2024, 12, 31))

    min_cap_억 = st.number_input("최소 시총 (억원)", value=5000, step=1000)
    initial_capital = st.number_input("초기 자본 (만원)", value=3000, step=500) * 10000
    position_size_pct = st.slider("종목당 투자 비중 (%)", 5, 30, 10) / 100
    max_stocks = st.slider("분석 종목 수 (상위 N개)", 10, 100, 50)

    st.divider()
    st.header("📐 매매 기준")

    st.markdown("**진입 조건**")
    trigger_pct  = st.slider("트리거: 다음 라운드 - x%",  1, 8, 4) / 100
    buy1_pct     = st.slider("1차 매수: 이전 라운드 + x%", 1, 8, 4) / 100
    buy2_pct     = st.slider("2차 매수: 이전 라운드 + x%", 0, 5, 2) / 100
    buy3_pct     = st.slider("3차 매수: 이전 라운드 + x%", -3, 3, 0) / 100

    st.markdown("**청산 조건**")
    target_pct   = st.slider("목표 수익률 (%)", 5, 30, 15) / 100
    stoploss_pct = st.slider("손절 기준 (%)", -30, -3, -10) / 100

    st.markdown("**라운드넘버 단위**")
    st.caption("주가별 자동 설정 (아래는 참고용)")
    st.code(
        "~ 5,000원    → 1,000원 단위\n"
        "~ 50,000원   → 5,000원 단위\n"
        "~ 100,000원  → 10,000원 단위\n"
        "~ 500,000원  → 50,000원 단위\n"
        "500,000원 ~  → 100,000원 단위",
        language=None
    )

# ────────────────────────────────────────────────────────────
# 라운드넘버 유틸
# ────────────────────────────────────────────────────────────
def get_round_unit(price: float) -> int:
    if price < 5_000:     return 1_000
    elif price < 50_000:  return 5_000
    elif price < 100_000: return 10_000
    elif price < 500_000: return 50_000
    else:                 return 100_000

def get_round_numbers(price: float):
    unit = get_round_unit(price)
    prev_r = int(price // unit) * unit
    if prev_r == 0:
        prev_r = unit
    next_r = prev_r + unit
    return prev_r, next_r

# ────────────────────────────────────────────────────────────
# 데이터 수집
# ────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tickers(date_str: str, min_cap: int) -> list:
    """시총 기준으로 KOSPI 종목 필터링"""
    try:
        cap_df = stock.get_market_cap(date_str, market="KOSPI")
        filtered = cap_df[cap_df["시가총액"] >= min_cap * 1e8]
        # 시총 내림차순 정렬
        filtered = filtered.sort_values("시가총액", ascending=False)
        return filtered.index.tolist()
    except Exception as e:
        st.error(f"종목 조회 실패: {e}")
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

# ────────────────────────────────────────────────────────────
# 단일 종목 백테스트
# ────────────────────────────────────────────────────────────
def backtest_one(ticker: str, df: pd.DataFrame, capital_per_stock: float) -> list:
    """
    상태머신:
    IDLE → TRIGGERED(트리거 발동) → IN_TRADE(1~3차 매수) → 청산 → IDLE
    """
    tranche_cap = capital_per_stock / 3
    trades = []

    # 상태
    state = "IDLE"
    setup_prev_r = None
    setup_next_r = None

    # 포지션
    pos_shares   = 0
    pos_avg_cost = 0.0
    pos_tranches = 0
    pos_entry_date = None
    pos_buy_prices = []   # [buy1, buy2, buy3]

    for date, row in df.iterrows():
        h, l, c = row["고가"], row["저가"], row["종가"]
        if c <= 0 or h <= 0 or l <= 0:
            continue

        # ── 포지션 보유 중: 청산 & 추가매수 ──────────────────
        if state == "IN_TRADE":
            avg  = pos_avg_cost
            stop = avg * (1 + stoploss_pct)
            tgt  = avg * (1 + target_pct)

            # 1) 손절 (저가가 손절가 하회)
            if l <= stop:
                pnl = (stop - avg) * pos_shares
                trades.append({
                    "종목코드": ticker,
                    "진입일":   pos_entry_date,
                    "청산일":   date,
                    "평균단가": round(avg),
                    "청산가":   round(stop),
                    "수익률":   (stop / avg) - 1,
                    "손익(원)": pnl,
                    "청산사유": "손절",
                    "매수횟수": pos_tranches,
                })
                state = "IDLE"; pos_shares = 0; pos_tranches = 0
                continue

            # 2) 목표 수익 (고가가 목표가 상회)
            if h >= tgt:
                pnl = (tgt - avg) * pos_shares
                trades.append({
                    "종목코드": ticker,
                    "진입일":   pos_entry_date,
                    "청산일":   date,
                    "평균단가": round(avg),
                    "청산가":   round(tgt),
                    "수익률":   (tgt / avg) - 1,
                    "손익(원)": pnl,
                    "청산사유": "목표수익",
                    "매수횟수": pos_tranches,
                })
                state = "IDLE"; pos_shares = 0; pos_tranches = 0
                continue

            # 3) 추가 매수 (2차, 3차)
            for nth in range(pos_tranches + 1, 4):
                bp = pos_buy_prices[nth - 1]
                if l <= bp:
                    new_shares = int(tranche_cap / bp)
                    if new_shares > 0:
                        total_cost = pos_avg_cost * pos_shares + bp * new_shares
                        pos_shares += new_shares
                        pos_avg_cost = total_cost / pos_shares
                        pos_tranches = nth
                    break  # 하루에 1트랑셰만

        # ── IDLE: 트리거 탐색 ────────────────────────────────
        elif state == "IDLE":
            pr, nr = get_round_numbers(c)
            trigger_price = nr * (1 - trigger_pct)
            if h >= trigger_price:
                state = "TRIGGERED"
                setup_prev_r = pr
                setup_next_r = nr

        # ── TRIGGERED: 1차 매수 대기 ─────────────────────────
        if state == "TRIGGERED":
            # 가격이 크게 튀어 라운드넘버가 바뀌면 리셋
            cur_pr, _ = get_round_numbers(c)
            if cur_pr != setup_prev_r:
                state = "IDLE"
                continue

            buy1 = setup_prev_r * (1 + buy1_pct)
            buy2 = setup_prev_r * (1 + buy2_pct)
            buy3 = setup_prev_r * (1 + buy3_pct)

            if l <= buy1:
                shares = int(tranche_cap / buy1)
                if shares > 0:
                    state          = "IN_TRADE"
                    pos_shares     = shares
                    pos_avg_cost   = buy1
                    pos_tranches   = 1
                    pos_entry_date = date
                    pos_buy_prices = [buy1, buy2, buy3]

    return trades

# ────────────────────────────────────────────────────────────
# 실행 버튼
# ────────────────────────────────────────────────────────────
st.divider()
run_btn = st.button("🚀 백테스트 실행", type="primary", use_container_width=True)

if run_btn:
    start_str = start_date.strftime("%Y%m%d")
    end_str   = end_date.strftime("%Y%m%d")

    with st.spinner("📡 KOSPI 종목 필터링 중..."):
        tickers = fetch_tickers(start_str, min_cap_억)

    if not tickers:
        st.error("종목을 불러오지 못했습니다.")
        st.stop()

    tickers = tickers[:max_stocks]
    st.info(f"✅ 분석 대상 {len(tickers)}개 종목 (시총 {min_cap_억:,}억 이상 KOSPI)")

    capital_per_stock = initial_capital * position_size_pct
    all_trades = []

    prog_bar  = st.progress(0, text="백테스트 진행 중...")
    prog_text = st.empty()

    for i, ticker in enumerate(tickers):
        name = fetch_ticker_name(ticker)
        prog_text.text(f"분석 중: {name}({ticker})  [{i+1}/{len(tickers)}]")

        df = fetch_ohlcv(ticker, start_str, end_str)
        if df.empty or len(df) < 60:
            prog_bar.progress((i + 1) / len(tickers))
            continue

        trades = backtest_one(ticker, df, capital_per_stock)

        # 종목명 추가
        for t in trades:
            t["종목명"] = name

        all_trades.extend(trades)
        prog_bar.progress((i + 1) / len(tickers))
        time.sleep(0.03)

    prog_bar.empty()
    prog_text.empty()

    if not all_trades:
        st.warning("⚠️ 조건을 충족하는 거래가 없습니다. 파라미터를 완화해보세요.")
        st.stop()

    # ── 결과 DataFrame ────────────────────────────────────────
    rdf = pd.DataFrame(all_trades)
    rdf = rdf.sort_values("청산일").reset_index(drop=True)

    wins       = (rdf["수익률"] > 0).sum()
    total      = len(rdf)
    win_rate   = wins / total * 100
    avg_ret    = rdf["수익률"].mean() * 100
    total_pnl  = rdf["손익(원)"].sum()
    avg_hold   = (pd.to_datetime(rdf["청산일"]) - pd.to_datetime(rdf["진입일"])).dt.days.mean()
    profit_factor = (
        rdf.loc[rdf["손익(원)"] > 0, "손익(원)"].sum() /
        abs(rdf.loc[rdf["손익(원)"] < 0, "손익(원)"].sum() + 1e-9)
    )

    # ── 핵심 지표 ─────────────────────────────────────────────
    st.divider()
    st.subheader("📈 백테스트 결과 요약")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("총 거래 수",     f"{total:,}건")
    c2.metric("승률",           f"{win_rate:.1f}%")
    c3.metric("평균 수익률",    f"{avg_ret:.1f}%")
    c4.metric("총 손익",        f"{total_pnl/10000:,.0f}만원")
    c5.metric("Profit Factor",  f"{profit_factor:.2f}")

    c6, c7, c8 = st.columns(3)
    c6.metric("평균 보유기간",  f"{avg_hold:.0f}일")
    c7.metric("목표수익 청산",  f'{(rdf["청산사유"]=="목표수익").sum():,}건')
    c8.metric("손절 청산",      f'{(rdf["청산사유"]=="손절").sum():,}건')

    # ── 누적 수익 곡선 ────────────────────────────────────────
    rdf["누적손익"] = rdf["손익(원)"].cumsum() + initial_capital

    fig_equity = go.Figure()
    fig_equity.add_trace(go.Scatter(
        x=rdf["청산일"],
        y=rdf["누적손익"] / 10000,
        mode="lines",
        fill="tozeroy",
        fillcolor="rgba(33,150,243,0.1)",
        line=dict(color="#2196F3", width=2),
        name="누적 자산",
        hovertemplate="%{x}<br>%{y:,.0f}만원<extra></extra>"
    ))
    fig_equity.add_hline(
        y=initial_capital / 10000,
        line_dash="dash", line_color="gray",
        annotation_text="원금"
    )
    fig_equity.update_layout(
        title="📉 누적 자산 변화",
        xaxis_title="날짜",
        yaxis_title="자산 (만원)",
        height=350,
        margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig_equity, use_container_width=True)

    # ── 차트 2열 ─────────────────────────────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        fig_dist = px.histogram(
            rdf, x="수익률", nbins=30,
            title="📊 수익률 분포",
            color_discrete_sequence=["#4CAF50"]
        )
        fig_dist.update_xaxes(tickformat=".0%")
        fig_dist.add_vline(x=0, line_dash="dash", line_color="red")
        fig_dist.update_layout(height=320, margin=dict(t=40, b=20))
        st.plotly_chart(fig_dist, use_container_width=True)

    with col_b:
        reason_cnt = rdf["청산사유"].value_counts()
        fig_pie = px.pie(
            values=reason_cnt.values,
            names=reason_cnt.index,
            title="🎯 청산 사유 비율",
            color_discrete_map={"목표수익": "#4CAF50", "손절": "#F44336"},
            hole=0.4
        )
        fig_pie.update_layout(height=320, margin=dict(t=40, b=20))
        st.plotly_chart(fig_pie, use_container_width=True)

    # ── 종목별 성과 ───────────────────────────────────────────
    st.subheader("🏆 종목별 성과 Top 15")
    by_stock = (
        rdf.groupby(["종목코드", "종목명"])
        .agg(
            거래수=("수익률", "count"),
            승률=("수익률", lambda x: (x > 0).mean() * 100),
            평균수익률=("수익률", "mean"),
            총손익=("손익(원)", "sum"),
        )
        .reset_index()
        .sort_values("총손익", ascending=False)
        .head(15)
    )
    by_stock["평균수익률"] = by_stock["평균수익률"].apply(lambda x: f"{x*100:.1f}%")
    by_stock["승률"]       = by_stock["승률"].apply(lambda x: f"{x:.0f}%")
    by_stock["총손익"]     = by_stock["총손익"].apply(lambda x: f"{x/10000:,.0f}만원")
    st.dataframe(by_stock, use_container_width=True, hide_index=True)

    # ── 분할매수 현황 ─────────────────────────────────────────
    st.subheader("📦 분할매수 횟수 분포")
    tranche_cnt = rdf["매수횟수"].value_counts().sort_index()
    fig_bar = px.bar(
        x=[f"{i}차 매수" for i in tranche_cnt.index],
        y=tranche_cnt.values,
        title="진입 시 분할매수 횟수",
        color_discrete_sequence=["#9C27B0"],
        text_auto=True
    )
    fig_bar.update_layout(height=280, margin=dict(t=40, b=20))
    st.plotly_chart(fig_bar, use_container_width=True)

    # ── 거래 내역 테이블 ──────────────────────────────────────
    st.subheader("📋 전체 거래 내역")
    display = rdf[[
        "종목명", "종목코드", "진입일", "청산일",
        "평균단가", "청산가", "수익률", "손익(원)", "청산사유", "매수횟수"
    ]].copy()
    display["수익률"]  = display["수익률"].apply(lambda x: f"{x*100:.1f}%")
    display["손익(원)"] = display["손익(원)"].apply(lambda x: f"{x/10000:,.1f}만원")

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "수익률": st.column_config.TextColumn("수익률"),
        }
    )

    # ── 파라미터 요약 저장 ────────────────────────────────────
    with st.expander("⚙️ 이번 실행 파라미터 보기"):
        st.json({
            "기간": f"{start_date} ~ {end_date}",
            "최소시총(억)": min_cap_억,
            "분석종목수": max_stocks,
            "초기자본(만원)": initial_capital // 10000,
            "종목당비중(%)": position_size_pct * 100,
            "트리거(다음라운드-%)": trigger_pct * 100,
            "1차매수(이전라운드+%)": buy1_pct * 100,
            "2차매수(이전라운드+%)": buy2_pct * 100,
            "3차매수(이전라운드+%)": buy3_pct * 100,
            "목표수익률(%)": target_pct * 100,
            "손절기준(%)": stoploss_pct * 100,
        })
