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
# 한글 폰트 설정 제거 (깨짐 방지 위해 차트는 영어로 표기)
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="K-Momentum Simulator", page_icon="🇰🇷", layout="wide")

st.title("🇰🇷 한국형 KOSPI 모멘텀 시뮬레이터")
st.markdown("""
**전략 개요 (K-Market Switching):**
1. **특징:** 박스권인 한국 시장(KOSPI)의 하락 구간을 피하고 상승 구간만 취하는 전략입니다.
2. **세금 혜택:** 국내 주식형 ETF는 매매차익 비과세(거래세 제외) 효과가 있어 회전율이 높아도 비용이 낮습니다.
3. **로직 (Trend Following):**
    - **매수 신호:** 주가가 이동평균선(MA)보다 **위에** 있을 때 (상승 추세)
    - **매도 신호:** 주가가 이동평균선(MA)보다 **아래에** 있을 때 (하락 추세) -> 채권/현금으로 대피
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 미리 받기 (한국 ETF)
# -----------------------------------------------------------------------------
# 주요 한국 ETF 티커 정의 (Yahoo Finance 기준 .KS 접미사)
K_TICKERS = {
    "KOSPI Index": "^KS11",              # 코스피 지수 (신호용)
    "KODEX 200": "069500.KS",            # 대표 지수
    "KODEX 코스닥150": "229200.KS",      # [추가됨] 코스닥 1배수
    "KODEX 레버리지": "122630.KS",       # 2배 레버리지
    "KODEX 코스닥150레버리지": "233740.KS", # 코스닥 레버리지
    "TIGER 차이나전기차": "371460.KS",    # (예시) 테마형
    "KODEX 국고채10년": "152380.KS",     # 중장기 채권
    "KODEX 단기채권": "153130.KS",       # 단기 채권
    "KODEX KOFR금리": "423160.KS",       # 파킹통장(현금)
    "USD/KRW": "KRW=X"                   # 환율
}

@st.cache_data(ttl=3600*24)
def load_k_data():
    """한국 시장 데이터 로딩 (2010년부터)"""
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
    st.header("1. 자산 구성 (국내 ETF)")
    
    st.subheader("⚔️ 공격 자산")
    # 딕셔너리 역참조를 위해 리스트 변환
    k_names = list(K_TICKERS.keys())
    k_codes = list(K_TICKERS.values())
    
    # 공격 1
    att1_options = ["KODEX 200", "KODEX 코스닥150", "KODEX 레버리지", "KODEX 코스닥150레버리지", "TIGER 차이나전기차"]
    att1_name = st.selectbox("공격 1 (지수/테마)", att1_options, index=0)
    ticker_att1 = K_TICKERS.get(att1_name, "069500.KS")
    
    # 공격 2
    att2_options = ["KODEX 레버리지", "KODEX 200", "KODEX 코스닥150", "KODEX 코스닥150레버리지"]
    att2_name = st.selectbox("공격 2 (교체/혼합용)", att2_options, index=0)
    ticker_att2 = K_TICKERS.get(att2_name, "122630.KS")

    st.subheader("⚖️ 비중 설정 (상승장)")
    att1_weight = st.slider(f"{att1_name} 비중 (%)", 0, 100, 100, 10, help="나머지는 공격 2에 배분됩니다.")
    w1 = att1_weight / 100.0
    w2 = 1.0 - w1
    
    st.subheader("🛡️ 방어 자산")
    def_name = st.selectbox("위기 시 대피처", ["KODEX 국고채10년", "KODEX 단기채권", "KODEX KOFR금리"], index=0)
    ticker_def = K_TICKERS.get(def_name, "152380.KS")

    st.subheader("🚦 신호 (Signal)")
    # 신호 설명 추가
    st.info("💡 **신호 기준:**\n주가가 이평선보다 **높으면 매수**, **낮으면 매도**")
    sig_name = st.selectbox("추세 판단 기준", ["KOSPI Index", "KODEX 200"], index=0)
    ticker_sig = K_TICKERS.get(sig_name, "^KS11")

    st.markdown("---")
    st.header("2. 옵션")
    initial_capital = st.number_input("투자금 (원)", value=50000000, step=1000000, format="%d")
    fee_rate = st.number_input("매매 비용 (%)", value=0.02, step=0.01, format="%.2f") / 100.0
    tax_rate = st.number_input("세금 (%)", value=0.0, step=1.0, format="%.1f") / 100.0
    
    start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))
    ma_window = st.number_input("이평선 기준 (일)", value=120, help="이 값보다 주가가 높으면 상승장으로 판단")

# -----------------------------------------------------------------------------
# 4. 데이터 로딩
# -----------------------------------------------------------------------------
with st.spinner("한국 증시 데이터 가져오는 중..."):
    full_df = load_k_data()

# -----------------------------------------------------------------------------
# 5. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 실행 (Run)", type="primary", use_container_width=True):
    
    # 1. 데이터 준비
    use_tickers = [ticker_att1, ticker_att2, ticker_def, ticker_sig]
    # 누락 확인 (없는 경우 대비)
    available_tickers = [t for t in use_tickers if t in full_df.columns]
    
    df_raw = full_df[available_tickers].fillna(method='ffill')
    
    # 사용자 시작일 처리
    if pd.to_datetime(start_date) < df_raw.index[0]:
        sim_start = df_raw.index[0]
    else:
        sim_start = pd.to_datetime(start_date)
    
    # 2. 지표 계산 (전체 기간)
    sig_series = df_raw[ticker_sig]
    ma_line = sig_series.rolling(window=ma_window).mean()
    
    # 3. 백테스트
    df_price = df_raw.loc[sim_start:]
    ma_line = ma_line.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)
    
    if len(df_price) < 20:
        st.error("데이터 기간이 너무 짧습니다.")
        st.stop()
        
    dates = df_price.index
    equity = initial_capital
    curve = []
    pos_history = []
    logs = []
    
    # 벤치마크 (공격 1 자산 Buy & Hold)
    bench_equity = initial_capital
    bench_curve = []
    
    # 초기 포지션 (현금)
    curr_w = {ticker_def: 1.0}
    prev_state = "Init"
    year_gain = 0
    
    for i in range(len(dates)):
        today = dates[i]
        
        # 벤치마크 계산
        if i > 0:
            r_bench = df_ret[ticker_att1].iloc[i]
            bench_equity = bench_equity * (1 + r_bench)
        bench_curve.append(bench_equity)
        
        if i == 0:
            curve.append(equity)
            pos_history.append(curr_w)
            continue
            
        # 신호 확인 (어제 종가 기준)
        yesterday_price = df_price[ticker_sig].iloc[i-1]
        yesterday_ma = ma_line.iloc[i-1]
        
        target_w = {}
        state = ""
        
        # [전략] Price > MA -> Bull
        if yesterday_price > yesterday_ma:
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
        pos_history.append(curr_w)
        
        # 세금 (옵션) - 매년 말
        if tax_rate > 0 and (i == len(dates)-1 or dates[i+1].year != today.year):
            tax = max(0, year_gain) * tax_rate
            if tax > 0:
                equity -= tax
                logs.append({"Date": today.strftime('%Y-%m-%d'), "Action": "Tax", "State": "-", "Cost": round(tax)})
            year_gain = 0

    # 결과 데이터프레임
    res_df = pd.DataFrame({
        'Equity': curve,
        'Benchmark': bench_curve
    }, index=dates)
    res_df['Signal_Price'] = df_price[ticker_sig]
    res_df['MA'] = ma_line
    
    # 통계 계산
    final = curve[-1]
    final_b = bench_curve[-1]
    cagr = (final/initial_capital)**(1/(len(curve)/252)) - 1
    cagr_b = (final_b/initial_capital)**(1/(len(curve)/252)) - 1
    
    peak = res_df['Equity'].cummax()
    dd = (res_df['Equity'] - peak) / peak
    mdd = dd.min()
    
    peak_b = res_df['Benchmark'].cummax()
    dd_b = (res_df['Benchmark'] - peak_b) / peak_b
    mdd_b = dd_b.min()
    
    # --- UI 리포트 ---
    st.divider()
    
    # Action Plan
    last_w = pos_history[-1]
    tgt_txt = " + ".join([f"{k} {v*100:.0f}%" for k, v in last_w.items()])
    
    c1, c2 = st.columns([1, 2])
    c1.metric("최종 자산", f"{final:,.0f} 원", delta=f"vs Bench: {final - final_b:,.0f}")
    c1.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr - cagr_b)*100:.2f}%p")
    c1.metric("MDD", f"{mdd*100:.2f} %", delta=f"Bench: {mdd_b*100:.2f}%")
    
    with c2:
        st.markdown(f"### 📢 현재 포지션: **[{tgt_txt}]**")
        # 신호 기준 설명
        st.info(f"**판단 기준:** {ticker_sig} 주가 > {ma_window}일 이평선 = **매수**")
        
        if "069500" in str(last_w) or "122630" in str(last_w) or "229200" in str(last_w):
            st.success("📈 **상승 추세 (Bull)**: 주식형 자산을 보유하세요.")
        else:
            st.warning("🛡️ **하락 추세 (Bear)**: 채권/현금으로 대피해 계세요.")

    # --- [탭 인터페이스] ---
    tab1, tab2, tab3 = st.tabs(["📈 Chart", "📝 Trade Logs", "📅 Monthly Returns"])
    
    # 1. Chart Tab
    with tab1:
        st.markdown(f"##### Equity & MDD (vs {att1_name})")
        fig, axes = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})
        
        # (1) Equity Curve
        axes[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='Strategy')
        axes[0].plot(res_df.index, res_df['Benchmark'], color='gray', linestyle='--', alpha=0.6, label=f'Benchmark ({att1_name})')
        axes[0].set_yscale('log')
        axes[0].set_title("1. Equity Curve (Log Scale)")
        axes[0].legend(loc='upper left')
        axes[0].grid(True, which='both', alpha=0.3)
        
        # (2) MDD
        axes[1].plot(res_df.index, dd * 100, color='blue', label='Strategy MDD')
        axes[1].plot(res_df.index, dd_b * 100, color='gray', linestyle=':', alpha=0.5, label='Benchmark MDD')
        axes[1].fill_between(res_df.index, dd * 100, 0, color='blue', alpha=0.1)
        axes[1].set_title("2. Drawdown (%)")
        axes[1].set_ylabel("MDD (%)")
        axes[1].legend(loc='lower right')
        axes[1].grid(True, alpha=0.3)
        
        # (3) Signal
        axes[2].plot(res_df.index, res_df['Signal_Price'], label='Price', color='black', alpha=0.6)
        axes[2].plot(res_df.index, res_df['MA'], label=f'MA ({ma_window})', color='orange', linestyle='--')
        axes[2].set_title(f"3. Market Trend ({ticker_sig})")
        axes[2].legend(loc='upper left')
        axes[2].grid(True, alpha=0.3)
        
        plt.tight_layout()
        st.pyplot(fig)

    # 2. Trade Logs Tab
    with tab2:
        st.markdown("##### 매매 기록 (Trade Logs)")
        if logs:
            log_df = pd.DataFrame(logs)
            st.dataframe(log_df, use_container_width=True)
        else:
            st.info("매매 기록이 없습니다.")
            
    # 3. Monthly Returns Tab
    with tab3:
        st.markdown("##### 월별 수익률 Heatmap")
        
        # 월별 데이터 계산
        m_eq = res_df['Equity'].resample('M').last()
        m_ret = m_eq.pct_change().fillna(0)
        m_df = pd.DataFrame(m_ret)
        m_df['Year'] = m_df.index.year
        m_df['Month'] = m_df.index.month
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Equity')
        m_pivot.columns = [calendar.month_abbr[i] for i in m_pivot.columns]
        
        # 연도별 Total 계산
        yearly_ret = []
        for y in m_pivot.index:
            yd = res_df[res_df.index.year == y]['Equity']
            if len(yd) > 0:
                start_v = res_df[res_df.index.year == (y-1)]['Equity'].iloc[-1] if y > res_df.index.year.min() else yd.iloc[0]
                yearly_ret.append((yd.iloc[-1]/start_v) - 1)
            else:
                yearly_ret.append(0)
        m_pivot['Total'] = yearly_ret
        
        # Heatmap 표시
        styler = m_pivot.style\
            .background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1)\
            .format("{:.2%}", na_rep="")
        st.dataframe(styler, use_container_width=True, height=600)

    # 엑셀 다운로드 (유지)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        res_df.to_excel(writer, sheet_name='Daily')
        pd.DataFrame(logs).to_excel(writer, sheet_name='Logs', index=False)
        m_pivot.to_excel(writer, sheet_name='Monthly_Returns')
    st.download_button("📥 엑셀 다운로드", output.getvalue(), "K_Momentum.xlsx")