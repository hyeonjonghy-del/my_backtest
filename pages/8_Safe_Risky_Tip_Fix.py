import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import xlsxwriter # 엑셀 생성을 위해 필요

# -----------------------------------------------------------------------------
# 1. 기본 설정 및 데이터 로딩
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot') 
st.set_page_config(page_title="HAA Strategy Report", page_icon="📈", layout="wide")

# 사용할 티커 목록
ALL_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "069500.KS", 
    "SSO", "UPRO", "QLD", "TQQQ", "UWM", "122630.KS", 
    "BIL", "SGOV", "SHV", "IEF", "TLT", "GOVT", "BND", 
    "TIP", "DBC", "VWO"
]

@st.cache_data(ttl=3600*24) 
def load_all_data_cached():
    # 데이터 다운로드
    df = yf.download(ALL_TICKERS, start="2000-01-01", progress=False, auto_adjust=True)
    
    # 멀티인덱스 처리 (Close 컬럼만 추출)
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
            df = df['Close']
        elif df.columns.nlevels > 1:
            df = df.droplevel(0, axis=1)
            
    # 결측치 제거 및 정렬
    return df.ffill().dropna().sort_index()

# -----------------------------------------------------------------------------
# 2. 사이드바 (설정)
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 전략 파라미터")
    
    with st.expander("자산 선택 (Tickers)", expanded=True):
        ticker_risky_base = st.selectbox("공격 1 (Base)", ["SPY", "QQQ", "IWM", "069500.KS"], index=0)
        ticker_risky_lev = st.selectbox("공격 2 (Leverage)", ["UPRO", "TQQQ", "QLD", "122630.KS"], index=0)
        ticker_safe_cash = st.selectbox("방어 1 (Cash)", ["BIL", "SGOV", "SHV"], index=0)
        ticker_safe_bond = st.selectbox("방어 2 (Bond)", ["IEF", "TLT", "BND"], index=0)
        ticker_canary = st.selectbox("카나리아 (Signal)", ["TIP", "DBC", "VWO"], index=0)
    
    with st.expander("비중 및 자금 설정", expanded=True):
        w_base = st.slider("상승장 공격1 비중 (%)", 0, 100, 30, step=5) / 100.0
        w_def_atk = st.slider("방어장 공격1 유지 비중 (%)", 0, 100, 0, step=5) / 100.0
        initial_capital = st.number_input("초기 투자금 (원)", value=100_000_000, step=1_000_000)
        start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
full_df = load_all_data_cached()

st.title("🛡️ HAA 전략 운용 리포트")

# [1] 전략 요약
with st.expander("📌 전략 개요 (Strategy Summary)", expanded=False):
    st.markdown(f"""
    **하이브리드 자산 배분 (HAA) 전략:**
    1. **카나리아 신호:** `{ticker_canary}`의 모멘텀 스코어가 0보다 크면 **상승장**, 작으면 **하락장**으로 판단합니다.
    2. **상승장 (Bull):** 공격 자산(`{ticker_risky_base}` + `{ticker_risky_lev}`)을 매수하여 수익을 극대화합니다.
    3. **하락장 (Bear):** 방어 자산(`{ticker_safe_cash}`, `{ticker_safe_bond}`) 중 모멘텀이 더 강한 자산으로 대피합니다.
    4. **모멘텀 스코어:** 최근 1, 3, 6, 12개월 수익률에 가중치를 두어 계산합니다.
    """)

if st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True):
    # 데이터 준비
    needed = list(set([ticker_risky_base, ticker_risky_lev, ticker_safe_cash, ticker_safe_bond, ticker_canary]))
    
    sim_start = pd.to_datetime(start_date)
    # 데이터 인덱스 안전 처리
    if sim_start < full_df.index[0]:
        sim_start = full_df.index[0]
        
    df_price = full_df[needed].loc[sim_start:]
    
    if df_price.empty:
        st.error("데이터 부족: 시작일을 확인해주세요.")
        st.stop()
    
    # 스코어 계산
    def get_score(series):
        return (series.pct_change(21)*12) + (series.pct_change(63)*4) + (series.pct_change(126)*2) + (series.pct_change(252)*1)

    scores = pd.DataFrame({t: get_score(full_df[t]) for t in needed}, index=full_df.index)
    scores = scores.loc[sim_start:] # 시뮬레이션 기간 매칭
    
    df_ret = df_price.pct_change().fillna(0)
    
    # 백테스트 루프
    cap = initial_capital
    b_cap = initial_capital
    equity, b_equity = [], []
    trade_logs = []
    
    curr_w = {ticker_safe_cash: 1.0}
    prev_mode = "Init"
    
    for i in range(len(df_price)):
        date = df_price.index[i]
        
        if i == 0:
            equity.append(cap); b_equity.append(b_cap); continue

        # 전일 종가 기준 신호
        try:
            canary_score = scores[ticker_canary].iloc[i-1]
            base_score = scores[ticker_risky_base].iloc[i-1]
            cash_score = scores[ticker_safe_cash].iloc[i-1]
            bond_score = scores[ticker_safe_bond].iloc[i-1]
        except:
            equity.append(cap); b_equity.append(b_cap); continue
            
        # 포지션 결정
        target = {}
        mode = ""
        
        # Bull Market
        if canary_score > 0 and base_score > 0:
            mode = "Bull (공격)"
            target = {ticker_risky_base: w_base, ticker_risky_lev: 1.0 - w_base}
        # Bear Market
        else:
            mode = "Defense (방어)"
            if cash_score > 0 and bond_score > 0:
                s_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
            elif bond_score > 0:
                s_alloc = {ticker_safe_bond: 1.0}
            else:
                s_alloc = {ticker_safe_cash: 1.0}
            
            if w_def_atk > 0:
                target[ticker_risky_base] = w_def_atk
                for t, w in s_alloc.items(): target[t] = w * (1.0 - w_def_atk)
            else:
                target = s_alloc
        
        # 매매 기록 (상태 변경 or 월 변경 시)
        if mode != prev_mode or date.month != df_price.index[i-1].month:
            alloc_str = ", ".join([f"{t}({w:.0%})" for t, w in target.items() if w > 0])
            trade_logs.append({
                "날짜": date.strftime('%Y-%m-%d'),
                "시장상태": mode,
                "매수 종목 및 비중": alloc_str,
                "평가금": round(cap)
            })
            prev_mode = mode
            
        curr_w = target
        day_ret = sum(df_ret[t].iloc[i] * w for t, w in curr_w.items())
        
        cap *= (1 + day_ret)
        b_cap *= (1 + df_ret[ticker_risky_base].iloc[i])
        
        equity.append(cap)
        b_equity.append(b_cap)

    # 결과 정리
    res = pd.DataFrame({'Strategy': equity, 'Benchmark': b_equity}, index=df_price.index[:len(equity)])
    
    # [2] 오늘 해야 할 일 (Action Plan)
    st.divider()
    last_canary = scores[ticker_canary].iloc[-1]
    last_base = scores[ticker_risky_base].iloc[-1]
    last_cash = scores[ticker_safe_cash].iloc[-1]
    last_bond = scores[ticker_safe_bond].iloc[-1]
    
    st.markdown("### 🔔 오늘 해야 할 일 (Action Plan)")
    
    final_target = {}
    action_msg = ""
    msg_color = ""
    
    if last_canary > 0 and last_base > 0:
        final_target = {ticker_risky_base: w_base, ticker_risky_lev: 1.0 - w_base}
        action_msg = f"🚀 **상승장 진입/유지**: 공격 자산을 적극 매수하세요."
        msg_color = "success"
    else:
        # 방어 로직 재계산
        if last_cash > 0 and last_bond > 0: s_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
        elif last_bond > 0: s_alloc = {ticker_safe_bond: 1.0}
        else: s_alloc = {ticker_safe_cash: 1.0}
        
        if w_def_atk > 0:
            final_target[ticker_risky_base] = w_def_atk
            for t, w in s_alloc.items(): final_target[t] = w * (1.0 - w_def_atk)
        else:
            final_target = s_alloc
        action_msg = f"🛡️ **방어장 진입/유지**: 리스크 관리를 위해 방어 자산으로 이동하세요."
        msg_color = "warning"

    c1, c2 = st.columns([2, 1])
    with c1:
        if msg_color == "success": st.success(action_msg)
        else: st.warning(action_msg)
    with c2:
        st.markdown("**👇 목표 포트폴리오**")
        for t, w in final_target.items():
            if w > 0: st.markdown(f"- **{t}**: `{w*100:.1f}%`")

    # [3] 시뮬레이션 결과 (Metrics)
    st.divider()
    final_bal = res['Strategy'].iloc[-1]
    profit = final_bal - initial_capital
    total_yield = (profit / initial_capital) * 100
    
    # CAGR, MDD
    days = (res.index[-1] - res.index[0]).days
    cagr = (final_bal / initial_capital) ** (365 / days) - 1
    
    res['peak'] = res['Strategy'].cummax()
    res['dd'] = (res['Strategy'] - res['peak']) / res['peak']
    mdd = res['dd'].min()
    
    st.subheader("📊 시뮬레이션 결과")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("최종 자산 (수익금)", f"{final_bal:,.0f} 원", f"+{profit:,.0f} 원")
    m2.metric("총 수익률", f"{total_yield:.2f}%")
    m3.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
    m4.metric("최대 낙폭 (MDD)", f"{mdd*100:.2f}%")

    # [4] 3개 탭 구성
    st.markdown("---")
    tab1, tab2, tab3 = st.tabs(["📈 차트 (수익률/MDD)", "📝 매매 로그", "📅 월별 수익률"])
    
    with tab1:
        fig, ax = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [2, 1]})
        
        # 누적 수익률 비교
        ax[0].plot(res.index, res['Strategy'], label='Strategy', color='#d62728', lw=2)
        ax[0].plot(res.index, res['Benchmark'], label=f'Benchmark ({ticker_risky_base})', color='gray', linestyle='--')
        ax[0].set_title("누적 자산 추이 (Equity Curve)")
        ax[0].legend()
        ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))
        
        # MDD
        ax[1].fill_between(res.index, res['dd']*100, 0, color='blue', alpha=0.3, label='Strategy MDD')
        
        # BM MDD 계산
        b_peak = res['Benchmark'].cummax()
        b_dd = (res['Benchmark'] - b_peak) / b_peak
        ax[1].plot(res.index, b_dd*100, color='black', alpha=0.5, linestyle=':', label='Benchmark MDD')
        
        ax[1].set_title("MDD 비교 (%)")
        ax[1].legend()
        
        st.pyplot(fig)
        
    with tab2:
        log_df = pd.DataFrame(trade_logs)
        st.dataframe(log_df, use_container_width=True, height=500)
        
    with tab3:
        # 월별 수익률 히트맵 테이블
        m_ret = res['Strategy'].resample('M').last().pct_change().fillna(0)
        m_df = pd.DataFrame({'Return': m_ret})
        m_df['Year'] = m_df.index.year
        m_df['Month'] = m_df.index.month
        
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Return')
        
        # YTD 계산
        y_ret = res['Strategy'].resample('Y').last().pct_change().fillna(0)
        # 첫해 보정
        if len(y_ret) > 0:
            first_val = res['Strategy'].iloc[0]
            first_year_end = res['Strategy'][res.index.year == res.index[0].year].iloc[-1]
            y_ret.iloc[0] = (first_year_end / first_val) - 1
            
        m_pivot['Total (Year)'] = y_ret.values
        
        # 컬럼명 월 이름으로 변경
        cols = {i: f"{i}월" for i in range(1, 13)}
        m_pivot.rename(columns=cols, inplace=True)
        
        st.dataframe(
            m_pivot.style.background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1).format("{:.2%}"),
            use_container_width=True
        )

    # [5] 엑셀 다운로드
    st.markdown("---")
    
    # 엑셀 파일 생성 로직
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        res.to_excel(writer, sheet_name='Daily Data')
        if not log_df.empty:
            log_df.to_excel(writer, sheet_name='Trade Logs', index=False)
        m_pivot.to_excel(writer, sheet_name='Monthly Returns')
        
    excel_data = output.getvalue()
    
    st.download_button(
        label="📥 결과 엑셀 파일 다운로드",
        data=excel_data,
        file_name="HAA_Strategy_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )