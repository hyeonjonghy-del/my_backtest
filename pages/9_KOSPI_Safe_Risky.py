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
# 한글 폰트 설정 (Windows/Mac/Linux 환경에 따라 다를 수 있음, 깨지면 영어로 사용 권장)
plt.rcParams['font.family'] = 'Malgun Gothic' # 윈도우 기준
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="K-Momentum Simulator", page_icon="🇰🇷", layout="wide")

st.title("🇰🇷 한국형 KOSPI 모멘텀 시뮬레이터")
st.markdown("""
**전략 개요 (K-Market Switching):**
1. **특징:** 박스권인 한국 시장(KOSPI)의 하락 구간을 피하고 상승 구간만 취하는 전략입니다.
2. **세금 혜택:** 국내 주식형 ETF는 매매차익 비과세(거래세 제외) 효과가 있어 회전율이 높아도 비용이 낮습니다.
3. **로직:** 코스피 추세가 꺾이면 즉시 **국채/현금**으로 대피하여 MDD를 방어합니다.
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 미리 받기 (한국 ETF)
# -----------------------------------------------------------------------------
# 주요 한국 ETF 티커 정의 (Yahoo Finance 기준 .KS 접미사)
K_TICKERS = {
    "KOSPI Index": "^KS11",              # 코스피 지수 (신호용)
    "KODEX 200": "069500.KS",            # 대표 지수
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
    att1_name = st.selectbox("공격 1 (지수/테마)", ["KODEX 200", "KODEX 레버리지", "KODEX 코스닥150레버리지", "TIGER 차이나전기차"], index=0)
    ticker_att1 = K_TICKERS.get(att1_name, "069500.KS")
    
    # 공격 2
    att2_name = st.selectbox("공격 2 (교체/혼합용)", ["KODEX 레버리지", "KODEX 200", "KODEX 코스닥150레버리지"], index=0)
    ticker_att2 = K_TICKERS.get(att2_name, "122630.KS")

    st.subheader("⚖️ 비중 설정 (상승장)")
    att1_weight = st.slider(f"{att1_name} 비중 (%)", 0, 100, 100, 10, help="나머지는 공격 2에 배분됩니다.")
    w1 = att1_weight / 100.0
    w2 = 1.0 - w1
    
    st.subheader("🛡️ 방어 자산")
    def_name = st.selectbox("위기 시 대피처", ["KODEX 국고채10년", "KODEX 단기채권", "KODEX KOFR금리"], index=0)
    ticker_def = K_TICKERS.get(def_name, "152380.KS")

    st.subheader("🚦 신호 (Signal)")
    sig_name = st.selectbox("추세 판단 기준", ["KOSPI Index", "KODEX 200"], index=0)
    ticker_sig = K_TICKERS.get(sig_name, "^KS11")

    st.markdown("---")
    st.header("2. 옵션")
    initial_capital = st.number_input("투자금 (원)", value=50000000, step=1000000, format="%d")
    # 한국 ETF 거래세+수수료는 매우 저렴 (보통 0.01~0.03% 수준)
    fee_rate = st.number_input("매매 비용 (%)", value=0.02, step=0.01, format="%.2f") / 100.0
    # 국내 주식형은 비과세지만 보수적으로 0% 설정 (채권형 배당세 등 고려 시 조정 가능)
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
    # 누락 확인
    missing = [t for t in use_tickers if t not in full_df.columns]
    # KOFR 등 신규 상장 종목은 데이터가 짧을 수 있음 -> fillna(0) 처리 보단 상장일 이후부터
    
    df_raw = full_df[use_tickers].fillna(method='ffill')
    
    # 사용자 시작일 처리
    if pd.to_datetime(start_date) < df_raw.index[0]:
        sim_start = df_raw.index[0]
    else:
        sim_start = pd.to_datetime(start_date)
    
    # 2. 지표 계산 (전체 기간)
    # 신호선 (이평선)
    sig_series = df_raw[ticker_sig]
    ma_line = sig_series.rolling(window=ma_window).mean()
    
    # 3. 백테스트
    # 시뮬레이션 데이터 슬라이싱
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
    
    # 초기 포지션 (현금)
    curr_w = {ticker_def: 1.0}
    prev_state = "Init"
    
    # 연말 정산용
    year_gain = 0
    
    for i in range(len(dates)):
        today = dates[i]
        
        if i == 0:
            curve.append(equity)
            pos_history.append(curr_w)
            continue
            
        # 신호 확인 (어제 종가 기준)
        # 전체 데이터 프레임 기준 인덱싱 필요 (슬라이싱 된 df_price 기준이므로 인덱스 매칭)
        # i번째 날의 '전날' 데이터 확인
        
        # 간단히: 현재가 > MA -> 상승장
        # 주의: 실제 매매는 '어제 종가' 보고 '오늘 시가/종가' 매매
        # 여기서는 오늘 수익률(df_ret.iloc[i])을 적용하기 위해, 포지션 결정은 i-1 시점 데이터로 함
        
        yesterday_price = df_price[ticker_sig].iloc[i-1]
        yesterday_ma = ma_line.iloc[i-1]
        
        target_w = {}
        state = ""
        
        # [전략] 절대 모멘텀 (Price > MA)
        if yesterday_price > yesterday_ma:
            # 상승장: 공격 자산 매수
            target_w = {ticker_att1: w1, ticker_att2: w2}
            # 비중이 0인건 제거
            target_w = {k:v for k,v in target_w.items() if v > 0}
            state = "Bull (Attack)"
        else:
            # 하락장: 방어 자산 매수
            target_w = {ticker_def: 1.0}
            state = "Bear (Defense)"
            
        # 리밸런싱 체크 (월간 리밸런싱 + 신호 변경 시)
        is_chg = (state != prev_state)
        is_month = (today.month != dates[i-1].month)
        
        if is_chg or is_month:
            turnover = 0
            # 기존/신규 합집합
            all_keys = set(curr_w.keys()) | set(target_w.keys())
            for k in all_keys:
                turnover += abs(target_w.get(k,0) - curr_w.get(k,0))
            
            cost = (turnover / 2) * equity * fee_rate
            equity -= cost
            curr_w = target_w.copy()
            
            if cost > 0:
                logs.append({"Date": today.date(), "Action": "Rebal", "State": state, "Cost": round(cost)})
        
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
                logs.append({"Date": today.date(), "Action": "Tax", "State": "-", "Cost": round(tax)})
            year_gain = 0

    # 결과 정리
    res_df = pd.DataFrame({'Equity': curve}, index=dates)
    res_df['Signal_Price'] = df_price[ticker_sig]
    res_df['MA'] = ma_line
    
    # --- UI 리포트 ---
    final = curve[-1]
    cagr = (final/initial_capital)**(1/(len(curve)/252)) - 1
    dd = (res_df['Equity'] - res_df['Equity'].cummax()) / res_df['Equity'].cummax()
    mdd = dd.min()
    
    st.divider()
    
    # Action Plan
    last_w = pos_history[-1]
    tgt_txt = " + ".join([f"{k} {v*100:.0f}%" for k, v in last_w.items()])
    
    c1, c2 = st.columns([1, 2])
    c1.metric("최종 자산", f"{final:,.0f} 원")
    c1.metric("CAGR", f"{cagr*100:.2f} %")
    c1.metric("MDD", f"{mdd*100:.2f} %")
    
    c2.markdown(f"### 📢 현재 포지션: **[{tgt_txt}]**")
    if "069500" in str(last_w) or "122630" in str(last_w):
        c2.success("📈 **상승 추세 (Bull)**: 주식형 자산을 보유하세요.")
    else:
        c2.warning("🛡️ **하락 추세 (Bear)**: 채권/현금으로 대피해 계세요.")
        
    st.caption(f"기준: {ticker_sig} 주가가 {ma_window}일 이평선보다 높으면 매수, 낮으면 매도")

    # 차트
    fig, ax = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # 1. 자산 곡선
    ax[0].plot(res_df.index, res_df['Equity'], color='red', label='Strategy')
    ax[0].set_yscale('log')
    ax[0].set_title("자산 추이 (Log Scale)")
    ax[0].legend()
    ax[0].grid(True, alpha=0.3)
    
    # 2. 마켓 타이밍
    ax[1].plot(res_df.index, res_df['Signal_Price'], label='KOSPI', color='black', alpha=0.6)
    ax[1].plot(res_df.index, res_df['MA'], label=f'{ma_window} MA', color='orange', linestyle='--')
    ax[1].set_title("시장 추세 (Price vs MA)")
    ax[1].legend()
    ax[1].grid(True, alpha=0.3)
    
    st.pyplot(fig)
    
    # 엑셀 다운로드
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        res_df.to_excel(writer, sheet_name='Daily')
        pd.DataFrame(logs).to_excel(writer, sheet_name='Logs', index=False)
    st.download_button("📥 엑셀 다운로드", output.getvalue(), "K_Momentum.xlsx")