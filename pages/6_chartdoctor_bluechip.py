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
st.caption("라운드넘버존 진입 · 하락 시 역피라미딩 분할매수 · 평균단가 +15% 전량매도 전략")

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
    max_stocks = st.slider("분석 종목 수 (상위 N개)", 10, 100, 50)

    st.divider()
    st.header("📐 매매 기준")

    st.markdown("**① 라운드넘버존 진입**")
    trigger_pct = st.slider("트리거: 다음 라운드 - x%", 1, 8, 4) / 100
    buy1_pct    = st.slider("1차 매수: 이전 라운드 + x%", 1, 8, 4) / 100

    st.markdown("**② 자금 관리**")
    buy1_cap_pct = st.slider("1차 매수 비중 (전체 자본 %)", 5, 20, 10) / 100
    add_drop_pct = st.slider("추가매수 트리거 하락폭 (%)", 5, 20, 10) / 100
    st.caption("2차 = 1차 금액 ×2 / 3차 = 2차 금액 동일")

    buy2_cap = buy1_cap_pct * 2
    buy3_cap = buy1_cap_pct * 2
    st.info(
        f"💰 총 투입 비중\n"
        f"- 1차: {buy1_cap_pct*100:.0f}%\n"
        f"- 2차: {buy2_cap*100:.0f}%\n"
        f"- 3차: {buy3_cap*100:.0f}%\n"
        f"- **합계: {(buy1_cap_pct+buy2_cap+buy3_cap)*100:.0f}%**"
    )

    st.markdown("**③ 청산 조건**")
    target_pct   = st.slider("목표 수익률: 평균단가 + x%", 5, 40, 15) / 100
    stoploss_pct = st.slider("손절 기준: 평균단가 - x% (3차 이후만)", 3, 30, 10) / 100
    st.caption("⚠️ 손절은 3차 매수 완료 이후에만 발동")

    # ── 손익비 계산기 ──────────────────────────────────────
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
        st.error("❌ 손익비 불리 — 목표↑ 또는 손절↓ 권장")

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
    """시총 기준으로 KOSPI 종목 필터링 (공휴일/주말이면 가장 가까운 거래일로 재시도)"""
    from datetime import datetime, timedelta

    base_dt = datetime.strptime(date_str, "%Y%m%d")

    # 최대 10일 앞뒤로 유효 거래일 탐색
    for offset in range(0, 10):
        for sign in [1, -1]:
            candidate = (base_dt + timedelta(days=offset * sign)).strftime("%Y%m%d")
            try:
                cap_df = stock.get_market_cap(candidate, market="KOSPI")
                if cap_df is None or cap_df.empty:
                    continue
                if "시가총액" not in cap_df.columns:
                    continue
                filtered = cap_df[cap_df["시가총액"] >= min_cap * 1e8]
                filtered = filtered.sort_values("시가총액", ascending=False)
                tickers = filtered.index.tolist()
                if tickers:
                    return tickers
            except Exception:
                continue

    st.error("유효한 거래일을 찾지 못했습니다. 시작일을 조정해 주세요.")
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
def backtest_one(ticker: str, df: pd.DataFrame) -> list:
    """
    ■ 진입: 라운드넘버존 (트리거 → 1차 매수)
    ■ 추가매수:
        2차 = 1차 매수가 × (1 - add_drop_pct), 금액 = 1차 × 2
        3차 = 2차 매수가 × (1 - add_drop_pct), 금액 = 2차 동일
    ■ 매도: 평균단가 × (1 + target_pct) 전량
    ■ 손절: 3차 매수 완료 이후에만 → 평균단가 × (1 - stoploss_pct)
    """
    buy1_amount = initial_capital * buy1_cap_pct
    buy2_amount = buy1_amount * 2
    buy3_amount = buy2_amount

    trades  = []
    state   = "IDLE"
    setup_prev_r = None

    pos_shares     = 0
    pos_avg_cost   = 0.0
    pos_tranches   = 0
    pos_entry_date = None
    pos_buy1_price = None
    pos_buy2_price = None

    for date, row in df.iterrows():
        h, l, c = row["고가"], row["저가"], row["종가"]
        if c <= 0 or h <= 0 or l <= 0:
            continue

        # ── IN_TRADE ─────────────────────────────────────────
        if state == "IN_TRADE":
            avg = pos_avg_cost
            tgt = avg * (1 + target_pct)

            # 손절: 3차 매수 완료 이후에만 발동
            if pos_tranches == 3:
                stop = avg * (1 - stoploss_pct)
                if l <= stop:
                    pnl = (stop - avg) * pos_shares
                    trades.append({
                        "종목코드": ticker,
                        "진입일":   pos_entry_date,
                        "청산일":   date,
                        "1차매수가": round(pos_buy1_price),
                        "평균단가": round(avg),
                        "청산가":   round(stop),
                        "수익률":   -stoploss_pct,
                        "손익(원)": pnl,
                        "청산사유": "손절(3차후)",
                        "매수횟수": pos_tranches,
                    })
                    state = "IDLE"; pos_shares = 0; pos_tranches = 0
                    pos_buy1_price = None; pos_buy2_price = None
                    continue

            # 목표수익
            if h >= tgt:
                pnl = (tgt - avg) * pos_shares
                trades.append({
                    "종목코드": ticker,
                    "진입일":   pos_entry_date,
                    "청산일":   date,
                    "1차매수가": round(pos_buy1_price),
                    "평균단가": round(avg),
                    "청산가":   round(tgt),
                    "수익률":   target_pct,
                    "손익(원)": pnl,
                    "청산사유": "목표수익",
                    "매수횟수": pos_tranches,
                })
                state = "IDLE"; pos_shares = 0; pos_tranches = 0
                pos_buy1_price = None; pos_buy2_price = None
                continue

            # 2차 매수: 1차 매수가 × (1 - add_drop_pct)
            if pos_tranches == 1 and pos_buy1_price:
                buy2_trigger = pos_buy1_price * (1 - add_drop_pct)
                if l <= buy2_trigger:
                    ns = int(buy2_amount / buy2_trigger)
                    if ns > 0:
                        total = pos_avg_cost * pos_shares + buy2_trigger * ns
                        pos_shares  += ns
                        pos_avg_cost = total / pos_shares
                        pos_tranches = 2
                        pos_buy2_price = buy2_trigger

            # 3차 매수: 2차 매수가 × (1 - add_drop_pct)
            elif pos_tranches == 2 and pos_buy2_price:
                buy3_trigger = pos_buy2_price * (1 - add_drop_pct)
                if l <= buy3_trigger:
                    ns = int(buy3_amount / buy3_trigger)
                    if ns > 0:
                        total = pos_avg_cost * pos_shares + buy3_trigger * ns
                        pos_shares  += ns
                        pos_avg_cost = total / pos_shares
                        pos_tranches = 3

        # ── IDLE: 트리거 탐색 ────────────────────────────────
        elif state == "IDLE":
            pr, nr = get_round_numbers(c)
            if h >= nr * (1 - trigger_pct):
                state        = "TRIGGERED"
                setup_prev_r = pr

        # ── TRIGGERED: 1차 진입 대기 ─────────────────────────
        if state == "TRIGGERED":
            cur_pr, _ = get_round_numbers(c)
            if cur_pr != setup_prev_r:
                state = "IDLE"
                continue

            b1 = setup_prev_r * (1 + buy1_pct)
            if l <= b1:
                ns = int(buy1_amount / b1)
                if ns > 0:
                    state          = "IN_TRADE"
                    pos_shares     = ns
                    pos_avg_cost   = b1
                    pos_tranches   = 1
                    pos_entry_date = date
                    pos_buy1_price = b1
                    pos_buy2_price = None

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

        trades = backtest_one(ticker, df)

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

    # 전체 수익률 & CAGR 계산
    total_return = total_pnl / initial_capital
    years = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 365.25
    cagr  = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    final_capital = initial_capital + total_pnl

    # 1행: 기간 전체 성과
    st.markdown("**📊 기간 전체 성과**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("초기 자본",     f"{initial_capital/10000:,.0f}만원")
    c2.metric("최종 자산",     f"{final_capital/10000:,.0f}만원",
              delta=f"+{total_pnl/10000:,.0f}만원")
    c3.metric("전체 수익률",   f"{total_return*100:.1f}%")
    c4.metric("연환산 CAGR",   f"{cagr*100:.1f}%",
              help="복리 기준 연평균 수익률")

    st.divider()

    # 2행: 거래 단위 성과
    st.markdown("**🔁 거래 단위 성과**")
    c5, c6, c7, c8, c9 = st.columns(5)
    c5.metric("총 거래 수",     f"{total:,}건")
    c6.metric("승률",           f"{win_rate:.1f}%")
    c7.metric("거래당 평균수익", f"{avg_ret:.1f}%",
              help="1건당 평균 수익률 (전체 수익률과 다름)")
    c8.metric("Profit Factor",  f"{profit_factor:.2f}")
    c9.metric("평균 보유기간",  f"{avg_hold:.0f}일")

    st.divider()

    # 3행: 청산 현황
    st.markdown("**🎯 청산 현황**")
    c10, c11, c12 = st.columns(3)
    c10.metric("목표수익 청산", f'{(rdf["청산사유"]=="목표수익").sum():,}건')
    c11.metric("손절 청산",     f'{(rdf["청산사유"]=="손절").sum():,}건')
    c12.metric("손절 비율",     f'{(rdf["청산사유"]=="손절").sum()/total*100:.1f}%')

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
    # 최종 자산 annotation
    fig_equity.add_annotation(
        x=rdf["청산일"].iloc[-1],
        y=rdf["누적손익"].iloc[-1] / 10000,
        text=f"최종 {rdf['누적손익'].iloc[-1]/10000:,.0f}만원<br>(+{total_return*100:.1f}% / CAGR {cagr*100:.1f}%)",
        showarrow=True, arrowhead=2,
        bgcolor="rgba(33,150,243,0.15)",
        bordercolor="#2196F3",
        font=dict(size=12),
        ax=-120, ay=-40,
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
        "1차매수가", "평균단가", "청산가", "수익률", "손익(원)", "청산사유", "매수횟수"
    ]].copy()
    display["수익률"]   = display["수익률"].apply(lambda x: f"{x*100:.1f}%")
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
            "1차매수비중(%)": buy1_cap_pct * 100,
            "2차매수비중(%)": buy1_cap_pct * 2 * 100,
            "3차매수비중(%)": buy1_cap_pct * 2 * 100,
            "추가매수트리거하락(%)": add_drop_pct * 100,
            "트리거(다음라운드-%)": trigger_pct * 100,
            "1차매수(이전라운드+%)": buy1_pct * 100,
            "목표수익률(%)": target_pct * 100,
            "손절기준(%, 3차이후)": stoploss_pct * 100,
        })
