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

st.set_page_config(page_title="K-Momentum FX", page_icon="🇰🇷", layout="wide")

st.title("🇰🇷 한국형 환율 방어 전략 (K-Defense)")
st.markdown("""
**전략 핵심 (FX Signal):**
1. **신호 변경:** **'원/달러 환율(USD/KRW)'**을 위기 감지기로 사용합니다.
2. **논리:** 환율이 이평선보다 **높으면(원화 약세)** 외국인 이탈 가능성이 높으므로 **방어**, **낮으면(원화 강세)** **공격**합니다.
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 미리 받기
# -----------------------------------------------------------------------------
K_TICKERS = {
    "KOSPI Index": "^KS11",              
    "USD/KRW": "KRW=X",                  # [핵심] 환율 데이터
    "KODEX 200": "069500.KS",            
    "KODEX 코스닥150": "229200.KS",      # [추가됨]
    "KODEX 레버리지": "122630.KS",       
    "KODEX 코스닥150레버리지": "233740.KS", 
    "TIGER 차이나전기차": "371460.KS",    
    "KODEX 국고채10년": "152380.KS",     
    "KODEX 단기채권": "153130.KS",       
    "KODEX KOFR금리": "423160.KS",       
}

# [수정] 함수 이름을 변경하여 강제로 캐시를 갱신하게 함 (v2)
@st.cache_data(ttl=3600*24)
def load_k_data_v2():
    tickers = list(K_TICKERS.values())
    # 코스닥150 등 상장일이 늦은 종목을 위해 start를 넉넉히 잡음
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
    # 공격 1
    att1_options = ["KODEX 200", "KODEX 코스닥150", "KODEX 레버리지", "KODEX 코스닥150레버리지", "TIGER 차이나전기차"]
    att1_name = st.selectbox("공격 1 (지수/테마)", att1_options, index=0) # Default: KODEX 200
    ticker_att1 = K_TICKERS.get(att1_name, "069500.KS")
    
    # 공격 2
    att2_options = ["KODEX 레버리지", "KODEX 200", "KODEX 코스닥150", "KODEX 코스닥150레버리지"]
    att2_name = st.selectbox("공격 2 (교체/혼합용)", att2_options, index=0) # Default: KODEX 레버리지
    ticker_att2 = K_TICKERS.get(att2_name, "122630.KS")

    st.subheader("⚖️ 비중 설정 (상승장)")
    att1_weight = st.slider(f"{att1_name} 비중 (%)", 0, 100, 100, 10)
    w1 = att1_weight / 100.0
    w2 = 1.0 - w1
    
    st.subheader("🛡️ 방어 자산")
    def_name = st.selectbox("위기 시 대피처", ["KODEX 국고채10년", "KODEX 단기채권", "KODEX KOFR금리"], index=0)
    ticker_def = K_TICKERS.get(def_name, "152380.KS")

    st.subheader("🚦 환율 신호 (FX Signal)")
    st.info("💡 **규칙:** 환율이 이평선보다 **높으면 위기(방어)**, **낮으면 평온(공격)**")
    
    ticker_sig = "KRW=X" 
    
    st.markdown("---")
    st.header("2. 옵션")
    initial_capital = st.number_input("투자금 (원)", value=50000000, step=1000000, format="%d")
    fee_rate = st.number_input("매매 비용 (%)", value=0.02, step=0.01, format="%.2f") / 100.0
    tax_rate = st.number_input("세금 (%)", value=0.0, step=1.0, format="%.1f") / 100.0
    
    start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))
    ma_window = st.number_input("환율 이평선 (일)", value=120, help="보통 120~200일 사용")

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 실행 (Run)", type="primary", use_container_width=True):
    with st.spinner("데이터 로딩 및 분석 중..."):
        full_df = load_k_data_v2()
    
    # [에러 방지] 선택한 종목이 데이터에 있는지 확인
    use_tickers = [ticker_att1, ticker_att2, ticker_def, ticker_sig]
    missing = [t for t in use_tickers if t not in full_df.columns]
    
    if missing:
        st.error(f"다음 종목의 데이터를 불러오지 못했습니다: {missing}. (상장일 이전이거나 데이터 오류)")
        st.stop()
        
    df_raw = full_df[use_tickers].fillna(method='ffill')
    
    sim_start = pd.to_datetime(start_date)
    if sim_start < df_raw.index[0]: sim_start = df_raw.index[0]
    
    # 2. 환율 지표 계산
    sig_series = df_raw[ticker_sig]
    ma_line = sig_series.rolling(window=ma_window).mean()
    
    # 3. 백테스트 준비
    df_price = df_raw.loc[sim_start:]
    ma_line = ma_line.loc[sim_start:]
    # [중요] 상장 전 데이터(NaN)가 있으면 0으로 처리하여 에러 방지
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
            
        # [핵심 로직] 환율 신호 확인 (어제 종가 기준)
        yesterday_fx = df_price[ticker_sig].iloc[i-1]
        yesterday_ma = ma_line.iloc[i-1]
        
        target_w = {}
        state = ""
        
        # 환율 < 이평선 => "저환율/원화강세" => 호재 => 공격 자산
        if yesterday_fx < yesterday_ma:
            target_w = {ticker_att1: w1, ticker_att2: w2}
            target_w = {k:v for k,v in target_w.items() if v > 0}
            state = "Bull (Stable FX)"
        else:
            # 환율 > 이평선 => "고환율/원화약세" => 위기 => 방어 자산
            target_w = {ticker_def: 1.0}
            state = "Bear (High FX)"
            
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
        'FX_Rate': df_price[ticker_sig],
        'FX_MA': ma_line
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
        last_fx = res_df['FX_Rate'].iloc[-1]
        last_ma = res_df['FX_MA'].iloc[-1]
        st.markdown(f"### 📢 현재 환율 상태: **{last_fx:.2f}원** (기준선 {last_ma:.2f}원)")
        
        if last_fx < last_ma:
            st.success("✅ **환율 안정 (Attack)**: 원화가 안정적입니다. 주식 투자가 유리합니다.")
        else:
            st.error("🚨 **환율 불안 (Defense)**: 환율이 높습니다. 현금/채권으로 대피하세요.")

    tab1, tab2, tab3 = st.tabs(["📈 Chart", "📝 Trade Logs", "📅 Monthly Returns"])
    
    with tab1:
        fig, axes = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})
        
        # 1. Equity
        axes[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='FX Strategy')
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
        
        # 3. FX Signal
        axes[2].plot(res_df.index, res_df['FX_Rate'], label='USD/KRW', color='green')
        axes[2].plot(res_df.index, res_df['FX_MA'], label='MA Line', color='orange', linestyle='--')
        axes[2].fill_between(res_df.index, res_df['FX_Rate'], res_df['FX_MA'], 
                             where=(res_df['FX_Rate'] > res_df['FX_MA']), color='red', alpha=0.3, label='Crisis Zone')
        axes[2].set_title("3. FX Signal (Red Zone = Defensive)")
        axes[2].legend()
        
        plt.tight_layout()
        st.pyplot(fig)
        
    with tab2:
        if logs: st.dataframe(pd.DataFrame(logs), use_container_width=True)
        else: st.info("기록 없음")
        
    with tab3:
        # 월별 수익률 히트맵 (요청하신 스타일 적용)
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
        
        st.markdown("##### 월별/연도별 수익률 Heatmap")
        
        # [히트맵 핵심] background_gradient 사용 (빨강-노랑-초록)
        # axis=None: 전체 테이블 기준 색상 (가장 높으면 진한 초록, 낮으면 진한 빨강)
        styler = m_pivot.style\
            .background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1)\
            .format("{:.2%}", na_rep="")
            
        st.dataframe(styler, use_container_width=True, height=600)