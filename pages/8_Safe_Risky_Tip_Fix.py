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

st.set_page_config(page_title="HAA Final Custom", page_icon="🛡️", layout="wide")

st.title("🛡️ HAA 전략 커스텀 시뮬레이터 (Action Plan 포함)")
st.markdown("""
**수정 사항:**
- **TIP Signal 오류 수정:** 데이터 결측치 처리(`dropna`) 및 인덱스 정렬 보완
- **데이터 로딩 안정화:** yfinance 데이터 구조 변경 대응
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 캐싱
# -----------------------------------------------------------------------------
ALL_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "069500.KS",       # 공격 1
    "SSO", "UPRO", "QLD", "TQQQ", "UWM", "122630.KS", # 공격 2
    "BIL", "SGOV", "SHV", "IEF", "TLT", "GOVT", "BND", # 방어
    "TIP", "DBC", "VWO"                  # 카나리아
]

@st.cache_data(ttl=3600*24) 
def load_all_data_cached():
    fetch_start = "2000-01-01" 
    # auto_adjust=True로 수정주가 반영
    df = yf.download(ALL_TICKERS, start=fetch_start, progress=False, auto_adjust=True)
    
    # yfinance 출력 구조 대응
    if isinstance(df.columns, pd.MultiIndex):
        df = df['Close']
    
    # [핵심 수정] 데이터가 없는 빈 행 제거 및 결측치 채우기
    df = df.ffill().dropna()
    return df.sort_index()

# -----------------------------------------------------------------------------
# 3. 사이드바 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. 자산 구성")
    
    st.subheader("⚔️ 공격 자산")
    risky_base_options = {
        "SPY (S&P500)": "SPY", "QQQ (나스닥)": "QQQ", "IWM (러셀2000)": "IWM",
        "DIA (다우존스)": "DIA", "KODEX 200 (한국)": "069500.KS"
    }
    r_base_choice = st.selectbox("공격 1 (1배수)", list(risky_base_options.keys()), index=0)
    ticker_risky_base = risky_base_options[r_base_choice]
    
    risky_lev_options = {
        "SSO (S&P500 2배)": "SSO", "UPRO (S&P500 3배)": "UPRO", "QLD (나스닥 2배)": "QLD",
        "TQQQ (나스닥 3배)": "TQQQ", "UWM (러셀2000 2배)": "UWM", "KODEX 레버리지 (한국)": "122630.KS"
    }
    r_lev_choice = st.selectbox("공격 2 (레버리지)", list(risky_lev_options.keys()), index=1)
    ticker_risky_lev = risky_lev_options[r_lev_choice]

    st.subheader("🛡️ 방어 자산")
    ticker_safe_cash = st.selectbox("방어 1 (현금)", ["BIL", "SGOV", "SHV"], index=0)
    ticker_safe_bond = st.selectbox("방어 2 (국채)", ["IEF", "TLT", "GOVT", "BND"], index=0)

    st.subheader("🐥 카나리아")
    ticker_canary = st.selectbox("위험 감지", ["TIP", "DBC", "VWO"], index=0)

    st.markdown("---")
    st.header("2. 비중 설정")
    w_base = st.slider("불장 시 공격1 비중 (%)", 0, 100, 30, 5) / 100.0
    w_lev = 1.0 - w_base
    
    w_def_atk = st.slider("방어장 시 공격1 유지 비중 (%)", 0, 100, 0, 5) / 100.0
    w_def_safe = 1.0 - w_def_atk

    st.header("3. 운용 설정")
    initial_capital = st.number_input("투자금 (원)", value=100000000, step=1000000)
    commission_rate = st.number_input("수수료율 (%)", value=0.10, step=0.01) / 100.0
    apply_tax = st.checkbox("양도세(22%) 적용", value=True)
    start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))
    ma_window = st.number_input("이평선 (일)", value=120)

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
full_df = load_all_data_cached()

if st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True):
    # 사용할 티커 데이터 필터링 및 결측치 재정리
    needed_tickers = list(set([ticker_risky_base, ticker_risky_lev, ticker_safe_cash, ticker_safe_bond, ticker_canary]))
    df_price_all = full_df[needed_tickers].ffill().dropna()
    
    # [스코어 계산 함수]
    def get_score(series):
        r1 = series.pct_change(21)
        r3 = series.pct_change(63)
        r6 = series.pct_change(126)
        r12 = series.pct_change(252)
        return (r1 * 12) + (r3 * 4) + (r6 * 2) + (r12 * 1)

    score_df = pd.DataFrame(index=df_price_all.index)
    for t in [ticker_canary, ticker_risky_base, ticker_safe_cash, ticker_safe_bond]:
        score_df[f'{t}_Score'] = get_score(df_price_all[t])
        
    ma_line = df_price_all[ticker_risky_base].rolling(window=ma_window).mean()

    # 기간 필터링 (스코어 계산을 위해 데이터는 미리 계산 후 자름)
    sim_start = pd.to_datetime(start_date)
    df_price = df_price_all.loc[sim_start:]
    score_df = score_df.loc[sim_start:]
    ma_line = ma_line.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)
    
    dates = df_price.index
    
    # 변수 초기화
    current_capital = initial_capital
    equity_curve, weights_history, trade_logs, position_changes = [], [], [], []
    current_weights = {ticker_safe_cash: 1.0}
    prev_mode_desc = "Init"
    year_realized_gain = 0
    bench_capital = initial_capital
    bench_equity = []
    bench_year_start = initial_capital

    # 백테스트 루프
    progress_bar = st.progress(0)
    for i in range(len(dates)):
        if i % 100 == 0: progress_bar.progress(i / len(dates))
        today, prev_date = dates[i], dates[i-1] if i > 0 else dates[i]
        
        if i == 0:
            equity_curve.append(current_capital); weights_history.append(current_weights); bench_equity.append(bench_capital)
            continue
            
        # 신호 판단 (전일 종가 스코어 기준)
        canary = score_df[f'{ticker_canary}_Score'].iloc[i-1]
        base = score_df[f'{ticker_risky_base}_Score'].iloc[i-1]
        cash = score_df[f'{ticker_safe_cash}_Score'].iloc[i-1]
        bond = score_df[f'{ticker_safe_bond}_Score'].iloc[i-1]
        
        target = {}
        if (canary > 0) and (base > 0):
            target = {ticker_risky_base: w_base, ticker_risky_lev: w_lev}
            mode = "Bull"
        else:
            safe_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5} if (cash > 0 and bond > 0) else ({ticker_safe_bond: 1.0} if bond > 0 else {ticker_safe_cash: 1.0})
            if w_def_atk > 0: target[ticker_risky_base] = w_def_atk
            for t, w in safe_alloc.items(): target[t] = w * w_def_safe
            mode = "Defense"

        # 리밸런싱
        if mode != prev_mode_desc or today.month != prev_date.month:
            turnover = sum(abs(target.get(t, 0) - current_weights.get(t, 0)) for t in set(current_weights) | set(target))
            fee = (turnover / 2) * current_capital * commission_rate
            current_capital -= fee
            current_weights = target.copy()
            if fee > 10: trade_logs.append({"Date": today.strftime('%Y-%m-%d'), "Desc": mode, "Amount": round(current_capital), "Fee": round(fee)})
        
        prev_mode_desc = mode

        # 수익률 적용
        val, new_w = 0, {}
        for t, w in current_weights.items():
            r = df_ret[t].iloc[i]
            v = current_capital * w * (1+r)
            new_w[t] = v; val += v
        current_capital = val
        current_weights = {t: v/val for t, v in new_w.items()} if val > 0 else current_weights
        
        equity_curve.append(current_capital)
        bench_capital *= (1 + df_ret[ticker_risky_base].iloc[i])
        bench_equity.append(bench_capital)

        # 세금 (연말)
        if apply_tax and today.year != prev_date.year:
            tax = max(0, (current_capital - (equity_curve[-252] if len(equity_curve)>252 else initial_capital)) - 2500000) * 0.22
            current_capital -= max(0, tax)
            bench_capital -= max(0, (bench_capital - bench_year_start - 2500000) * 0.22)
            bench_year_start = bench_capital

    progress_bar.progress(1.0)

    # -------------------------------------------------------------------------
    # 5. 결과 표시 (Action Plan & 성과)
    # -------------------------------------------------------------------------
    st.divider()
    st.markdown("### 🔔 오늘 해야 할 일 (Action Plan)")
    last_canary = score_df[f'{ticker_canary}_Score'].iloc[-1]
    last_base = score_df[f'{ticker_risky_base}_Score'].iloc[-1]
    
    if last_canary > 0 and last_base > 0:
        st.success(f"## 🐂 상승장 (Bull Market) - 현재 자산: {ticker_risky_base} {w_base*100}% / {ticker_risky_lev} {w_lev*100}%")
    else:
        st.warning(f"## 🛡️ 방어장 (Defense Mode) - 안전 자산 비중 확대 필요")

    # 성과 리포트 & 탭 차트
    res_df = pd.DataFrame({'Equity': equity_curve, 'Bench_Equity': bench_equity}, index=dates)
    res_df['Canary_Score'] = score_df[f'{ticker_canary}_Score']
    
    tab1, tab2, tab3 = st.tabs(["📈 차트 분석", "📝 매매 기록", "📅 월별 수익률"])
    
    with tab1:
        fig, ax = plt.subplots(2, 1, figsize=(12, 10))
        ax[0].plot(res_df.index, res_df['Equity'], label='Strategy')
        ax[0].plot(res_df.index, res_df['Bench_Equity'], label='Benchmark', alpha=0.5)
        ax[0].set_title("Equity Curve"); ax[0].legend()
        
        ax[1].plot(res_df.index, res_df['Canary_Score'], color='purple', label=f'Canary ({ticker_canary})')
        ax[1].axhline(0, color='red', linestyle='--')
        ax[1].set_title("Risk Signal (Score)"); ax[1].legend()
        st.pyplot(fig)

    with tab2:
        st.dataframe(pd.DataFrame(trade_logs), use_container_width=True)

    with tab3:
        m_ret = res_df['Equity'].resample('M').last().pct_change().fillna(0)
        m_df = pd.DataFrame({'Return': m_ret, 'Year': m_ret.index.year, 'Month': m_ret.index.month})
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Return')
        st.dataframe(m_pivot.style.background_gradient(cmap='RdYlGn', axis=None).format("{:.2%}"), use_container_width=True)