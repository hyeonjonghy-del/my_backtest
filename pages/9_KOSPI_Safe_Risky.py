import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot')
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="K-Momentum Multi-Signal", page_icon="🇰🇷", layout="wide")

st.title("🇰🇷 한국형 멀티 시그널 전략 (K-Switch)")
st.markdown("""
**전략 개요:**
한국 시장은 박스권 성향이 강해 '언제 들어가고 빠지는가'가 수익률을 결정합니다.
아래 **3가지 신호** 중 하나를 선택하여 어떤 기준이 내 성향(수익 vs 방어)에 맞는지 검증해 보세요.
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 미리 받기
# -----------------------------------------------------------------------------
K_TICKERS = {
    "KOSPI Index": "^KS11",              # [신호1] 코스피 지수
    "USD/KRW": "KRW=X",                  # [신호2] 환율
    "US S&P500": "SPY",                  # [신호3] 미국 대표 지수
    "KODEX 200": "069500.KS",            
    "KODEX 코스닥150": "229200.KS",      
    "KODEX 레버리지": "122630.KS",       
    "KODEX 코스닥150레버리지": "233740.KS", 
    "TIGER 차이나전기차": "371460.KS",    
    "KODEX 국고채10년": "152380.KS",     
    "KODEX 단기채권": "153130.KS",       
    "KODEX KOFR금리": "423160.KS",       
}

# [캐시 갱신용 v3] 새로운 종목(SPY 등) 반영을 위해 함수명 변경
@st.cache_data(ttl=3600*24)
def load_k_data_v3():
    tickers = list(K_TICKERS.values())
    df = yf.download(tickers, start="2010-01-01", progress=False, auto_adjust=True)
    
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
             df = df['Close'].copy()
        else:
             df = df.copy()
             if df.columns.nlevels > 1:
                 df.columns = df.columns.get_level_values(0)
    return df.sort_index()

# -----------------------------------------------------------------------------
# 3. 사이드바 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. 자산 구성")
    
    st.subheader("⚔️ 공격 자산")
    att1_name = st.selectbox("공격 1 (메인)", ["KODEX 200", "KODEX 코스닥150", "KODEX 레버리지", "KODEX 코스닥150레버리지", "TIGER 차이나전기차"], index=0)
    ticker_att1 = K_TICKERS.get(att1_name, "069500.KS")
    
    att2_name = st.selectbox("공격 2 (서브)", ["KODEX 레버리지", "KODEX 200", "KODEX 코스닥150"], index=0)
    ticker_att2 = K_TICKERS.get(att2_name, "122630.KS")

    st.subheader("⚖️ 비중 설정 (상승장)")
    att1_weight = st.slider(f"{att1_name} 비중 (%)", 0, 100, 100, 10)
    w1 = att1_weight / 100.0
    w2 = 1.0 - w1
    
    st.subheader("🛡️ 방어 자산")
    def_name = st.selectbox("위기 시 대피처", ["KODEX 국고채10년", "KODEX 단기채권", "KODEX KOFR금리"], index=0)
    ticker_def = K_TICKERS.get(def_name, "152380.KS")

    st.markdown("---")
    st.header("2. 신호 선택 (Signal)")
    
    signal_type = st.radio(
        "어떤 기준으로 매매할까요?",
        ("환율 (USD/KRW)", "코스피 지수 (KOSPI)", "미국 S&P500 (SPY)")
    )
    
    # 신호별 티커 및 설명 설정
    if signal_type == "환율 (USD/KRW)":
        ticker_sig = "KRW=X"
        sig_desc = "환율이 이평선보다 **낮으면(안정)** 매수"
        is_inverted = True # 환율은 낮아야 좋다
    elif signal_type == "코스피 지수 (KOSPI)":
        ticker_sig = "^KS11"
        sig_desc = "지수가 이평선보다 **높으면(상승)** 매수"
        is_inverted = False # 지수는 높아야 좋다
    else:
        ticker_sig = "SPY"
        sig_desc = "미국장(SPY)이 이평선보다 **높으면** 매수"
        is_inverted = False

    st.info(f"💡 **전략:** {sig_desc}")

    st.markdown("---")
    st.header("3. 옵션")
    initial_capital = st.number_input("투자금 (원)", value=50000000, step=1000000, format="%d")
    fee_rate = st.number_input("매매 비용 (%)", value=0.02, step=0.01, format="%.2f") / 100.0
    tax_rate = st.number_input("세금 (%)", value=0.0, step=1.0, format="%.1f") / 100.0
    
    start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))
    ma_window = st.number_input("이평선 (일)", value=120, help="보통 120일(6개월) 또는 200일(1년) 사용")

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 실행 (Run)", type="primary", use_container_width=True):
    with st.spinner("데이터 로딩 및 분석 중..."):
        full_df = load_k_data_v3()
    
    # 데이터 유효성 체크
    use_tickers = [ticker_att1, ticker_att2, ticker_def, ticker_sig]
    missing = [t for t in use_tickers if t not in full_df.columns]
    if missing:
        st.error(f"데이터 누락: {missing}. 새로고침(C) 하거나 종목을 변경하세요.")
        st.stop()
        
    df_raw = full_df[use_tickers].fillna(method='ffill')
    
    sim_start = pd.to_datetime(start_date)
    if sim_start < df_raw.index[0]: sim_start = df_raw.index[0]
    
    # 2. 신호(Signal) 지표 계산
    sig_series = df_raw[ticker_sig]
    ma_line = sig_series.rolling(window=ma_window).mean()
    
    # 3. 백테스트 준비
    df_price = df_raw.loc[sim_start:]
    ma_line = ma_line.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)
    
    dates = df_price.index
    equity = initial_capital
    curve = []
    bench_curve = []
    bench_equity = initial_capital
    
    logs = []
    curr_w = {ticker_def: 1.0}
    prev_state = "Init"
    year_gain = 0
    
    for i in range(len(dates)):
        today = dates[i]
        
        # 벤치마크 (공격1 Buy & Hold)
        if i > 0:
            bench_equity *= (1 + df_ret[ticker_att1].iloc[i])
        bench_curve.append(bench_equity)
        
        if i == 0:
            curve.append(equity)
            continue
            
        # [핵심] 신호 확인 (어제 종가 기준)
        last_val = df_price[ticker_sig].iloc[i-1]
        last_ma = ma_line.iloc[i-1]
        
        target_w = {}
        state = ""
        is_bull = False
        
        # 신호 해석 (역방향 vs 정방향)
        if is_inverted: # 환율 (낮아야 좋음)
            if last_val < last_ma: is_bull = True
        else: # 지수 (높아야 좋음)
            if last_val > last_ma: is_bull = True
            
        if is_bull:
            target_w = {ticker_att1: w1, ticker_att2: w2}
            target_w = {k:v for k,v in target_w.items() if v > 0}
            state = "Bull (Attack)"
        else:
            target_w = {ticker_def: 1.0}
            state = "Bear (Defense)"
            
        # 리밸런싱
        is_chg = (state != prev_state)
        is_month = (today.month != dates[i-1].month)
        
        if is_chg or is_month:
            turnover = 0
            all_keys = set(curr_w.keys()) | set(target_w.keys())
            for k in all_keys:
                turnover += abs(target_w.get(k,0) - curr_w.get(k,0))
            
            cost = (turnover / 2) * equity * fee_rate
            equity -= cost
            curr_w = target_w.copy()
            
            if cost > 0:
                logs.append({
                    "Date": today.strftime('%Y-%m-%d'), 
                    "Action": "Rebal", 
                    "State": state, 
                    "Cost": round(cost)
                })
        
        prev_state = state
        
        # 수익률 적용
        day_ret = 0
        for t, w in curr_w.items():
            day_ret += df_ret[t].iloc[i] * w
        
        profit = equity * day_ret
        equity += profit
        year_gain += profit
        
        curve.append(equity)
        
        # 세금
        if tax_rate > 0 and (i == len(dates)-1 or dates[i+1].year != today.year):
            tax = max(0, year_gain) * tax_rate
            if tax > 0:
                equity -= tax
                logs.append({"Date": today.strftime('%Y-%m-%d'), "Action": "Tax", "State": "-", "Cost": round(tax)})
            year_gain = 0

    # 결과 정리
    res_df = pd.DataFrame({
        'Equity': curve,
        'Benchmark': bench_curve,
        'Signal_Val': df_price[ticker_sig],
        'Signal_MA': ma_line
    }, index=dates)
    
    final = curve[-1]
    final_b = bench_curve[-1]
    cagr = (final/initial_capital)**(1/(len(curve)/252)) - 1
    cagr_b = (final_b/initial_capital)**(1/(len(curve)/252)) - 1
    
    peak = res_df['Equity'].cummax()
    mdd = ((res_df['Equity'] - peak) / peak).min()
    peak_b = res_df['Benchmark'].cummax()
    mdd_b = ((res_df['Benchmark'] - peak_b) / peak_b).min()
    
    # -------------------------------------------------------------------------
    # 결과 화면
    # -------------------------------------------------------------------------
    st.divider()
    c1, c2 = st.columns([1, 2])
    c1.metric("최종 자산", f"{final:,.0f} 원", delta=f"vs Bench: {final - final_b:,.0f}")
    c1.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr - cagr_b)*100:.2f}%p")
    c1.metric("MDD", f"{mdd*100:.2f} %", delta=f"Bench: {mdd_b*100:.2f}%")
    
    with c2:
        last_val = res_df['Signal_Val'].iloc[-1]
        last_ma = res_df['Signal_MA'].iloc[-1]
        
        # 현재 상태 표시 로직
        status_color = "off"
        status_msg = ""
        
        # 로직 재확인
        is_now_bull = False
        if is_inverted: 
            if last_val < last_ma: is_now_bull = True
        else:
            if last_val > last_ma: is_now_bull = True
            
        st.markdown(f"### 📢 현재 신호 상태 ({signal_type})")
        st.write(f"현재값: **{last_val:,.2f}** / 기준선: **{last_ma:,.2f}**")
        
        if is_now_bull:
            st.success("📈 **공격 (Attack)**: 긍정적 신호입니다. 주식 비중을 가져갑니다.")
        else:
            st.warning("🛡️ **방어 (Defense)**: 부정적 신호입니다. 안전 자산으로 대피합니다.")

    tab1, tab2, tab3 = st.tabs(["📈 Chart", "📝 Trade Logs", "📅 Monthly Returns"])
    
    with tab1:
        fig, axes = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})
        
        # 1. Equity
        axes[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='Strategy')
        axes[0].plot(res_df.index, res_df['Benchmark'], color='gray', linestyle='--', alpha=0.6, label=f'Bench ({att1_name})')
        axes[0].set_yscale('log')
        axes[0].set_title("1. Equity Curve")
        axes[0].legend()
        
        # 2. MDD
        dd = (res_df['Equity'] - peak) / peak
        dd_b = (res_df['Benchmark'] - peak_b) / peak_b
        axes[1].plot(res_df.index, dd * 100, color='blue', label='Strategy MDD')
        axes[1].plot(res_df.index, dd_b * 100, color='gray', linestyle=':', alpha=0.5, label='Bench MDD')
        axes[1].fill_between(res_df.index, dd * 100, 0, color='blue', alpha=0.1)
        axes[1].set_title("2. Drawdown (%)")
        axes[1].legend()
        
        # 3. Signal
        # 신호 종류에 따라 색상/라벨 변경
        sig_color = 'green' if not is_inverted else 'purple'
        
        axes[2].plot(res_df.index, res_df['Signal_Val'], label='Signal Value', color=sig_color)
        axes[2].plot(res_df.index, res_df['Signal_MA'], label='MA Line', color='orange', linestyle='--')
        
        # 위기 구간 칠하기 (방어 구간)
        if is_inverted: # 환율: 높으면 위기
            axes[2].fill_between(res_df.index, res_df['Signal_Val'], res_df['Signal_MA'], 
                                 where=(res_df['Signal_Val'] > res_df['Signal_MA']), color='red', alpha=0.3, label='Defensive Zone')
        else: # 지수: 낮으면 위기
            axes[2].fill_between(res_df.index, res_df['Signal_Val'], res_df['Signal_MA'], 
                                 where=(res_df['Signal_Val'] < res_df['Signal_MA']), color='red', alpha=0.3, label='Defensive Zone')
            
        axes[2].set_title(f"3. Signal Indicator ({signal_type})")
        axes[2].legend()
        
        plt.tight_layout()
        st.pyplot(fig)
        
    with tab2:
        if logs: st.dataframe(pd.DataFrame(logs), use_container_width=True)
        else: st.info("기록 없음")
        
    with tab3:
        # 월별 수익률 히트맵
        m_eq = res_df['Equity'].resample('M').last()
        m_ret = m_eq.pct_change().fillna(0)
        m_df = pd.DataFrame(m_ret)
        m_df['Year'] = m_df.index.year
        m_df['Month'] = m_df.index.month
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Equity')
        m_pivot.columns = [calendar.month_abbr[i] for i in m_pivot.columns]
        
        yearly_ret = []
        for y in m_pivot.index:
            yd = res_df[res_df.index.year == y]['Equity']
            if len(yd) > 0:
                start_v = res_df[res_df.index.year == (y-1)]['Equity'].iloc[-1] if y > res_df.index.year.min() else yd.iloc[0]
                yearly_ret.append((yd.iloc[-1]/start_v) - 1)
            else:
                yearly_ret.append(0)
        m_pivot['Total'] = yearly_ret
        
        styler = m_pivot.style\
            .background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1)\
            .format("{:.2%}", na_rep="")
        st.dataframe(styler, use_container_width=True, height=600)