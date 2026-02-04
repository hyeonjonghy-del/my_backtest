import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정 및 데이터 로딩
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot') 
st.set_page_config(page_title="HAA Final Custom", page_icon="📈", layout="wide")

ALL_TICKERS = ["SPY", "QQQ", "IWM", "DIA", "069500.KS", "SSO", "UPRO", "QLD", "TQQQ", "UWM", "122630.KS", "BIL", "SGOV", "SHV", "IEF", "TLT", "GOVT", "BND", "TIP", "DBC", "VWO"]

@st.cache_data(ttl=3600*24) 
def load_all_data_cached():
    # 데이터 다운로드 및 전처리
    df = yf.download(ALL_TICKERS, start="2000-01-01", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        # MultiIndex 컬럼일 경우 Close만 선택하거나 레벨 조정
        if 'Close' in df.columns.levels[0]:
             df = df['Close']
        elif df.columns.nlevels > 1:
             df = df.droplevel(0, axis=1) # Ticker 레벨만 남김
             
    return df.ffill().dropna().sort_index()

# -----------------------------------------------------------------------------
# 2. 사이드바 및 입력 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 전략 설정")
    ticker_risky_base = st.selectbox("공격 1", ["SPY", "QQQ", "IWM", "069500.KS"], index=0)
    ticker_risky_lev = st.selectbox("공격 2 (레버리지)", ["UPRO", "TQQQ", "QLD", "122630.KS"], index=0)
    ticker_safe_cash = st.selectbox("방어 1 (현금)", ["BIL", "SGOV"], index=0)
    ticker_safe_bond = st.selectbox("방어 2 (국채)", ["IEF", "TLT", "BND"], index=0)
    ticker_canary = st.selectbox("카나리아", ["TIP", "DBC", "VWO"], index=0)
    
    st.divider()
    w_base = st.slider("상승장 공격1 비중 (%)", 0, 100, 30) / 100.0
    w_def_atk = st.slider("방어장 공격1 유지 비중 (%)", 0, 100, 0) / 100.0
    
    initial_capital = st.number_input("투자금 (원)", value=100000000, step=1000000)
    start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))

# -----------------------------------------------------------------------------
# 3. 시뮬레이션 실행
# -----------------------------------------------------------------------------
full_df = load_all_data_cached()

if st.button("🚀 전략 시뮬레이션 실행", type="primary", use_container_width=True):
    needed = list(set([ticker_risky_base, ticker_risky_lev, ticker_safe_cash, ticker_safe_bond, ticker_canary]))
    
    # [수정] 데이터 슬라이싱 안전장치 추가
    sim_start_date = pd.to_datetime(start_date)
    df_price = full_df[needed].loc[sim_start_date:]
    
    if df_price.empty:
        st.error("선택한 시작일에 데이터가 없습니다. 날짜를 조정해주세요.")
        st.stop()

    def get_score(series):
        return (series.pct_change(21)*12)+(series.pct_change(63)*4)+(series.pct_change(126)*2)+(series.pct_change(252)*1)

    # [수정된 부분] df_price.index[0]을 사용하여 명확한 시작점(Scalar) 지정
    scores = pd.DataFrame({t: get_score(full_df[t]) for t in needed}, index=full_df.index)
    scores = scores.loc[df_price.index[0]:] 
    
    df_ret = df_price.pct_change().fillna(0)
    
    # 백테스트 루프
    cap, b_cap = initial_capital, initial_capital
    equity, b_equity = [], []
    curr_w = {ticker_safe_cash: 1.0}
    
    # 인덱스 길이 불일치 방지 (df_price 길이만큼만 반복)
    loop_len = len(df_price)
    
    for i in range(loop_len):
        today = df_price.index[i]
        
        if i == 0:
            equity.append(cap); b_equity.append(b_cap); continue
            
        # 신호 (전일 기준)
        # scores 인덱스가 df_price와 동일하게 맞춰졌으므로 i-1로 접근 가능
        try:
            canary = scores[ticker_canary].iloc[i-1]
            base = scores[ticker_risky_base].iloc[i-1]
            cash = scores[ticker_safe_cash].iloc[i-1]
            bond = scores[ticker_safe_bond].iloc[i-1]
        except IndexError:
            # 데이터 초반부 인덱스 오류 방지
            continue
        
        # 리밸런싱 로직
        if canary > 0 and base > 0:
            target = {ticker_risky_base: w_base, ticker_risky_lev: 1-w_base}
        else:
            s_w = {ticker_safe_bond: 1.0} if bond > 0 else {ticker_safe_cash: 1.0}
            target = {ticker_risky_base: w_def_atk}
            for t, w in s_w.items(): target[t] = w * (1-w_def_atk)
        
        curr_w = target
        
        # 수익률 계산
        daily_ret = 0
        for t, w in curr_w.items():
            daily_ret += w * df_ret[t].iloc[i]
            
        cap *= (1 + daily_ret)
        b_cap *= (1 + df_ret[ticker_risky_base].iloc[i])
        
        equity.append(cap); b_equity.append(b_cap)

    # 결과 데이터 정리
    res = pd.DataFrame({'Strategy': equity, 'SPY_Bench': b_equity}, index=df_price.index[:len(equity)])
    
    if res.empty:
        st.error("결과 데이터가 생성되지 않았습니다.")
        st.stop()
        
    res['S_Peak'] = res['Strategy'].cummax()
    res['B_Peak'] = res['SPY_Bench'].cummax()
    res['S_DD'] = (res['Strategy'] - res['S_Peak']) / res['S_Peak']
    res['B_DD'] = (res['SPY_Bench'] - res['B_Peak']) / res['B_Peak']

    # -------------------------------------------------------------------------
    # 4. 결과 출력 (Metrics & Charts)
    # -------------------------------------------------------------------------
    st.subheader("📊 전략 성과 리포트")
    c1, c2, c3 = st.columns(3)
    final_val = res['Strategy'].iloc[-1]
    total_ret = (final_val / initial_capital - 1) * 100
    mdd_val = res['S_DD'].min() * 100
    
    c1.metric("최종 자산", f"{final_val:,.0f} 원", f"{total_ret:.2f}%")
    c2.metric("전략 MDD", f"{mdd_val:.2f}%")
    c3.metric("벤치마크 MDD", f"{res['B_DD'].min()*100:.2f}%")

    tab1, tab2 = st.tabs(["📈 수익률 & MDD 비교", "🐤 카나리아 신호"])
    
    with tab1:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [2, 1]})
        
        # 주 차트 (수익률)
        ax1.plot(res.index, res['Strategy'], label='My Strategy', color='#d62728', lw=2)
        ax1.plot(res.index, res['SPY_Bench'], label=f'{ticker_risky_base} (Bench)', color='gray', linestyle='--', alpha=0.7)
        ax1.set_title("Strategy Performance (Equity Curve)", fontsize=15); ax1.legend()
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))
        
        # MDD 비교 차트
        ax2.fill_between(res.index, res['S_DD']*100, 0, color='red', alpha=0.3, label='Strategy DD')
        ax2.plot(res.index, res['B_DD']*100, color='black', lw=1, label='Bench DD')
        ax2.set_title("MDD Comparison (%)", fontsize=13); ax2.legend()
        plt.tight_layout()
        st.pyplot(fig)
        
    with tab2:
        fig2, ax3 = plt.subplots(figsize=(12, 4))
        # scores 데이터 길이 안전하게 자르기
        plot_scores = scores.loc[res.index]
        ax3.plot(plot_scores.index, plot_scores[ticker_canary], color='purple', label=f'Canary ({ticker_canary})')
        ax3.axhline(0, color='red', linestyle='--')
        ax3.set_title("Risk Signal Score"); ax3.legend()
        st.pyplot(fig2)