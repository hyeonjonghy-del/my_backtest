import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import warnings
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot')
# 한글 폰트 설정 제거 (깨짐 방지)
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="K-Momentum Multi-Signal", page_icon="🇰🇷", layout="wide")

st.title("🇰🇷 K-Switch: Multi-Signal Strategy (Verified Logic)")
st.markdown("""
**전략 개요 (Verified Version):**
- **로직:** 시그널 발생(T일) → 다음 날(T+1일) 장 마감(종가)에 매매 (시차 적용 완료)
- **데이터:** 수정주가 아님 (배당금 제외, 실제 시장가 기준)
- **자산:** 강세장(주식) ↔ 약세장(국고채/안전자산) 스위칭
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 로드 (배당금 제외)
# -----------------------------------------------------------------------------
K_TICKERS = {
    "KOSPI Index": "^KS11",
    "USD/KRW": "KRW=X",
    "US S&P500": "SPY",
    "KODEX 200": "069500.KS",            
    "KODEX KOSDAQ150": "229200.KS",      
    "KODEX Leverage": "122630.KS",       
    "KODEX KOSDAQ150 Leverage": "233740.KS", 
    "TIGER China EV": "371460.KS",    
    "KODEX KTB 10Y": "152380.KS",     
    "KODEX Short-term Bond": "153130.KS",       
    "KODEX KOFR": "423160.KS",       
}

@st.cache_data(ttl=3600*24)
def load_k_data_v5():
    tickers = list(K_TICKERS.values())
    # [중요] auto_adjust=False 로 설정하여 배당락 등이 반영되지 않은 '실제 종가' 사용
    df = yf.download(tickers, start="2010-01-01", progress=False, auto_adjust=False)
    
    # yfinance 데이터 구조 처리 ('Close' 컬럼만 추출)
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
            df = df['Close'].copy()
        else:
            df = df.copy()
            if df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
    
    # 중복 제거 및 정렬
    df = df.loc[~df.index.duplicated(keep='first')]
    return df.sort_index()

# -----------------------------------------------------------------------------
# 3. 사이드바 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. Portfolio Setup")
    
    st.subheader("⚔️ Aggressive Assets")
    att1_name = st.selectbox("Main Asset (1x)", ["KODEX 200", "KODEX KOSDAQ150", "TIGER China EV"], index=0)
    ticker_att1 = K_TICKERS.get(att1_name, "069500.KS")
    
    att2_name = st.selectbox("Sub Asset (Leverage)", ["KODEX Leverage", "KODEX KOSDAQ150 Leverage", "KODEX 200"], index=0)
    ticker_att2 = K_TICKERS.get(att2_name, "122630.KS")

    st.subheader("⚖️ Weights (Bull Market)")
    att1_weight = st.slider(f"{att1_name} Weight (%)", 0, 100, 100, 10)
    w1 = att1_weight / 100.0
    w2 = 1.0 - w1
    
    st.subheader("🛡️ Defensive Assets")
    def_name = st.selectbox("Safe Haven", ["KODEX KTB 10Y", "KODEX Short-term Bond", "KODEX KOFR"], index=0)
    ticker_def = K_TICKERS.get(def_name, "152380.KS")

    st.markdown("---")
    st.header("2. Signal Selection")
    
    signal_type = st.radio(
        "Which signal to use?",
        ("USD/KRW", "KOSPI", "S&P500 (SPY)"),
        index=2 # 기본값 SPY
    )
    
    if signal_type == "USD/KRW":
        ticker_sig = "KRW=X"
        is_inverted = True 
    elif signal_type == "KOSPI":
        ticker_sig = "^KS11"
        is_inverted = False 
    else:
        ticker_sig = "SPY"
        is_inverted = False

    st.markdown("---")
    st.header("3. Options")
    initial_capital = st.number_input("Initial Capital (KRW)", value=100000000, step=1000000, format="%d")
    fee_rate = st.number_input("Trading Fee (%)", value=0.02, step=0.01, format="%.2f") / 100.0
    start_date = st.date_input("Start Date", pd.to_datetime("2020-01-01"))
    ma_window = st.number_input("MA Window (Days)", value=10)

# -----------------------------------------------------------------------------
# 4. 메인 로직 (검증된 로직 적용)
# -----------------------------------------------------------------------------
if st.button("🚀 Run Backtest", type="primary", use_container_width=True):
    with st.spinner("Analyzing data with Verified Logic..."):
        full_df = load_k_data_v5()
    
    use_tickers = [ticker_att1, ticker_att2, ticker_def, ticker_sig]
    if not all(t in full_df.columns for t in use_tickers):
        st.error("Missing data for selected tickers. Please try again.")
        st.stop()

    # 데이터 준비
    df_raw = full_df[use_tickers].ffill()
    
    # 1. 시그널 계산 (전체 기간 기준)
    sig_series = df_raw[ticker_sig].dropna()
    ma_line = sig_series.rolling(window=ma_window).mean()
    
    if is_inverted:
        raw_signal = sig_series < ma_line
    else:
        raw_signal = sig_series > ma_line
        
    # [핵심] T일의 시그널을 T+1일 매매에 사용하기 위해 하루 미룸 (Shift 1)
    trade_signal = raw_signal.shift(1)
    
    # 2. 시뮬레이션 데이터셋 준비
    # 한국 메인 자산(KODEX 200 등)이 거래된 날짜만 남김 (휴일 제거)
    df_sim = df_raw[[ticker_att1, ticker_att2, ticker_def]].copy()
    df_sim = df_sim.dropna(subset=[ticker_att1])
    
    # 시그널 병합 (Left Join으로 한국 영업일에 맞춤)
    df_sim = df_sim.join(trade_signal.rename('Is_Bull'), how='left')
    df_sim = df_sim.join(sig_series.rename('Signal_Val'), how='left') # 차트용
    df_sim = df_sim.join(ma_line.rename('Signal_MA'), how='left')     # 차트용
    
    # 시그널 공백 처리 (앞의 값으로 채우거나, 없으면 Bear)
    df_sim['Is_Bull'] = df_sim['Is_Bull'].ffill().fillna(False)
    
    # 날짜 필터링
    sim_start = pd.to_datetime(start_date)
    df_sim = df_sim.loc[sim_start:]
    
    # 3. 백테스트 루프
    equity = initial_capital
    peak = equity
    history = []
    
    # 초기 포지션 설정 (첫날 시그널 기준)
    first_bull = df_sim['Is_Bull'].iloc[0]
    
    # Bull이면 공격자산, Bear면 방어자산(채권)
    if first_bull:
        curr_w = {ticker_att1: w1, ticker_att2: w2}
    else:
        curr_w = {ticker_def: 1.0}
    
    # 가중치 0인 종목 제거
    curr_w = {k:v for k,v in curr_w.items() if v > 0}
    
    # 첫 진입 수수료 차감
    equity -= equity * fee_rate
    
    for i in range(len(df_sim)):
        today = df_sim.index[i]
        is_bull = df_sim['Is_Bull'].iloc[i]
        
        # [내일의 자산 결정] 오늘 장 마감 시점에 맞춰야 할 목표 포트폴리오
        if is_bull:
            target_w = {ticker_att1: w1, ticker_att2: w2}
        else:
            target_w = {ticker_def: 1.0}
        target_w = {k:v for k,v in target_w.items() if v > 0}
        
        # [A] 수익률 계산 (오늘 하루 동안 보유했던 자산의 수익)
        # 종가 매매이므로, 오늘 등락폭은 온전히 현재 보유 자산(curr_w)이 받음
        day_ret = 0
        if i > 0:
            for t, w in curr_w.items():
                r = df_raw[t].pct_change().loc[today]
                if pd.isna(r): r = 0
                day_ret += r * w
        
        equity *= (1 + day_ret)
        
        # [B] 자산 교체 (장 마감 시점, 종가 거래)
        action = ""
        sell_p, buy_p = "", ""
        
        # 보유 자산 구성이 다르면 스위칭 발생
        keys_curr = set(curr_w.keys())
        keys_tgt = set(target_w.keys())
        
        if keys_curr != keys_tgt:
            action = "SWITCH"
            equity -= equity * fee_rate # 교체 수수료
            
            # 로그용 가격 기록 (종가)
            s_list = [f"{df_raw[t].loc[today]:,.0f}" for t in curr_w]
            sell_p = " | ".join(s_list)
            b_list = [f"{df_raw[t].loc[today]:,.0f}" for t in target_w]
            buy_p = " | ".join(b_list)
            
            # 자산 변경
            curr_w = target_w
            
        # MDD 계산
        if equity > peak: peak = equity
        dd = (equity - peak) / peak
        
        # 로그용 자산 이름 정리
        if len(curr_w) == 1:
            held_ticker = list(curr_w.keys())[0]
            # 딕셔너리 역추적하여 이름 찾기
            held_str = held_ticker
            for n, t in K_TICKERS.items():
                if t == held_ticker: held_str = n; break
        else:
            held_str = f"Aggressive ({int(w1*100)}:{int(w2*100)})"
            
        history.append({
            "Date": today,
            "Signal_State": "Bull" if is_bull else "Bear",
            "Held_Asset": held_str,
            "Action": action,
            "Sell_Price": sell_p,
            "Buy_Price": buy_p,
            "Equity": round(equity),
            "Daily_Return(%)": round(day_ret * 100, 2),
            "Cumulative_Return(%)": round(((equity / initial_capital) - 1) * 100, 2),
            "Peak_Equity": round(peak),
            "Drawdown(%)": round(dd * 100, 2),
            # 차트용 데이터
            "Signal_Val": df_sim['Signal_Val'].iloc[i],
            "Signal_MA": df_sim['Signal_MA'].iloc[i]
        })
        
    res_df = pd.DataFrame(history).set_index("Date")
    # 벤치마크 (메인 자산 보유)
    res_df['Benchmark'] = (1 + df_raw[ticker_att1].loc[sim_start:].pct_change().fillna(0)).cumprod() * initial_capital
    # 데이터 길이 맞춤
    res_df = res_df.loc[res_df.index <= df_sim.index[-1]]
    
    # -------------------------------------------------------------------------
    # 결과 시각화
    # -------------------------------------------------------------------------
    final = res_df['Equity'].iloc[-1]
    final_b = res_df['Benchmark'].iloc[-1]
    days = (res_df.index[-1] - res_df.index[0]).days
    if days > 0:
        cagr = (final / initial_capital) ** (365 / days) - 1
        cagr_b = (final_b / initial_capital) ** (365 / days) - 1
    else:
        cagr, cagr_b = 0, 0
    mdd = res_df['Drawdown(%)'].min() / 100.0
    
    st.divider()
    c1, c2 = st.columns([1, 2])
    c1.metric("Final Balance", f"{final:,.0f} KRW", delta=f"vs Bench: {final - final_b:,.0f}")
    c1.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr - cagr_b)*100:.2f}%p")
    c1.metric("MDD", f"{mdd*100:.2f} %")
    
    # 엑셀 다운로드 (상세 로그 포함)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        export_cols = ["Signal_State", "Held_Asset", "Action", "Sell_Price", "Buy_Price", 
                       "Daily_Return(%)", "Cumulative_Return(%)", "Equity", "Peak_Equity", "Drawdown(%)"]
        res_df[export_cols].to_excel(writer, sheet_name='Detailed_Log')
        st.info("엑셀 파일이 준비되었습니다. 아래 버튼을 눌러 다운로드하세요.")
        
    st.download_button(
        label="📥 상세 검증 데이터 다운로드 (Excel)",
        data=output.getvalue(),
        file_name=f"Verified_Backtest_{signal_type}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
    
    with c2:
        tab1, tab2 = st.tabs(["📈 Chart", "📝 Logs"])
        
        with tab1:
            fig, axes = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})
            
            # 1. Equity
            axes[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='Strategy')
            axes[0].plot(res_df.index, res_df['Benchmark'], color='gray', linestyle='--', alpha=0.6, label='Benchmark')
            axes[0].set_yscale('log')
            axes[0].set_title("1. Equity Curve (Log Scale)")
            axes[0].legend()
            
            # 2. MDD
            axes[1].plot(res_df.index, res_df['Drawdown(%)'], color='blue', label='Strategy MDD')
            axes[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0, color='blue', alpha=0.1)
            axes[1].set_title("2. Drawdown (%)")
            axes[1].legend()
            
            # 3. Signal
            axes[2].plot(res_df.index, res_df['Signal_Val'], label='Signal Value', color='green')
            axes[2].plot(res_df.index, res_df['Signal_MA'], label='MA Line', color='orange', linestyle='--')
            
            if is_inverted:
                 axes[2].fill_between(res_df.index, res_df['Signal_Val'], res_df['Signal_MA'], 
                                      where=(res_df['Signal_Val'] > res_df['Signal_MA']), color='red', alpha=0.3, label='Bear Zone')
            else:
                 axes[2].fill_between(res_df.index, res_df['Signal_Val'], res_df['Signal_MA'], 
                                      where=(res_df['Signal_Val'] < res_df['Signal_MA']), color='red', alpha=0.3, label='Bear Zone')
            
            axes[2].set_title(f"3. Signal Indicator ({signal_type})")
            axes[2].legend()
            
            plt.tight_layout()
            st.pyplot(fig)
            
        with tab2:
            st.dataframe(res_df[export_cols].sort_index(ascending=False), use_container_width=True)