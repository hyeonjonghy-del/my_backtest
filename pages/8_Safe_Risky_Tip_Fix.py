import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정 (Basic Setup)
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot') 

st.set_page_config(page_title="HAA Final Custom", page_icon="🛡️", layout="wide")

st.title("🛡️ HAA 전략 커스텀 시뮬레이터 (Action Plan 포함)")
st.markdown("""
**주요 기능:**
1. **오늘의 할 일:** 최신 데이터 기준, 당장 매수해야 할 종목과 비중을 최상단에 표시
2. **커스텀 방어:** 하락장에서도 공격 1 자산을 일정 비율 보유하는 옵션 적용
3. **벤치마크:** 모든 성과는 '공격 1 자산'과 직접 비교
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
    df = yf.download(ALL_TICKERS, start=fetch_start, progress=False, auto_adjust=True)
    
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
    risky_base_options = {
        "SPY (S&P500)": "SPY",
        "QQQ (나스닥)": "QQQ",
        "IWM (러셀2000)": "IWM",
        "DIA (다우존스)": "DIA",
        "KODEX 200 (한국)": "069500.KS"
    }
    r_base_choice = st.selectbox("공격 1 (1배수)", list(risky_base_options.keys()), index=0)
    ticker_risky_base = risky_base_options[r_base_choice]
    
    risky_lev_options = {
        "SSO (S&P500 2배)": "SSO",
        "UPRO (S&P500 3배)": "UPRO",
        "QLD (나스닥 2배)": "QLD",
        "TQQQ (나스닥 3배)": "TQQQ",
        "UWM (러셀2000 2배)": "UWM",
        "KODEX 레버리지 (한국)": "122630.KS"
    }
    r_lev_choice = st.selectbox("공격 2 (레버리지)", list(risky_lev_options.keys()), index=0)
    ticker_risky_lev = risky_lev_options[r_lev_choice]

    st.subheader("🛡️ 방어 자산")
    ticker_safe_cash = st.selectbox("방어 1 (현금)", ["BIL", "SGOV", "SHV"], index=0)
    ticker_safe_bond = st.selectbox("방어 2 (국채)", ["IEF", "TLT", "GOVT", "BND"], index=0)

    st.subheader("🐥 카나리아")
    ticker_canary = st.selectbox("위험 감지", ["TIP", "DBC", "VWO"], index=0)

    st.markdown("---")
    st.header("2. 비중 설정 (핵심)")
    
    st.markdown("#### (1) 상승장 비중")
    base_weight_percent = st.slider(
        f"불장 시 공격1 비중 (%)", 
        0, 100, 30, 5,
        help="나머지는 레버리지(공격2)에 투자됩니다."
    )
    w_base = base_weight_percent / 100.0
    w_lev = 1.0 - w_base
    
    st.markdown("#### (2) 방어장 비중")
    def_attack_percent = st.slider(
        f"방어장 시 공격1 유지 비중 (%)", 
        0, 100, 0, 5,
        help="방어 신호가 떠도 이 비율만큼은 공격1 자산을 유지합니다."
    )
    w_def_atk = def_attack_percent / 100.0
    w_def_safe = 1.0 - w_def_atk

    st.markdown("---")
    st.header("3. 운용 설정")
    initial_capital = st.number_input("투자금 (원)", value=100000000, step=1000000)
    commission_rate = st.number_input("수수료율 (%)", value=0.10, step=0.01) / 100.0
    apply_tax = st.checkbox("양도세(22%) 적용", value=True)
    start_date = st.date_input("시작일", pd.to_datetime("2016-01-01"))
    ma_window = st.number_input("이평선 (일)", value=120)

# -----------------------------------------------------------------------------
# 4. 데이터 로딩
# -----------------------------------------------------------------------------
with st.spinner("데이터 준비 중..."):
    full_df = load_all_data_cached()

# -----------------------------------------------------------------------------
# 5. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True):
    
    # 데이터 준비
    needed_tickers = list(set([ticker_risky_base, ticker_risky_lev, ticker_safe_cash, ticker_safe_bond, ticker_canary]))
    df_price_all = full_df[needed_tickers].fillna(method='ffill')
    
    # 스코어 계산
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

    # 기간 필터링
    sim_start = pd.to_datetime(start_date)
    if sim_start < df_price_all.index[0]: sim_start = df_price_all.index[0]
        
    df_price = df_price_all.loc[sim_start:]
    score_df = score_df.loc[sim_start:]
    ma_line = ma_line.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)
    
    dates = df_price.index
    
    # 변수 초기화
    current_capital = initial_capital
    equity_curve = []
    weights_history = [] 
    trade_logs = []
    position_changes = []
    current_weights = {ticker_safe_cash: 1.0}
    prev_mode_desc = "Init"
    year_realized_gain = 0
    
    # 벤치마크 (공격 1 자산)
    bench_capital = initial_capital
    bench_equity = []
    bench_year_start = initial_capital

    progress_bar = st.progress(0)
    
    for i in range(len(dates)):
        if i % 100 == 0: progress_bar.progress(i / len(dates))
        today = dates[i]
        
        if i == 0:
            equity_curve.append(current_capital)
            weights_history.append(current_weights)
            bench_equity.append(bench_capital)
            continue
            
        prev_date = dates[i-1]
        
        # --- [1] 신호 판단 ---
        canary = score_df[f'{ticker_canary}_Score'].iloc[i-1]
        base = score_df[f'{ticker_risky_base}_Score'].iloc[i-1]
        cash = score_df[f'{ticker_safe_cash}_Score'].iloc[i-1]
        bond = score_df[f'{ticker_safe_bond}_Score'].iloc[i-1]
        
        target = {}
        mode = ""
        
        # A. 상승장
        if (canary > 0) and (base > 0):
            target = {ticker_risky_base: w_base, ticker_risky_lev: w_lev}
            mode = f"Bull (Lev Mix)"
            
        # B. 방어장 (커스텀 로직)
        else:
            # 방어 자산 선택
            safe_alloc = {}
            safe_name = ""
            if (cash > 0) and (bond > 0):
                safe_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
                safe_name = "Mix"
            elif bond > 0:
                safe_alloc = {ticker_safe_bond: 1.0}
                safe_name = "Bond"
            else:
                safe_alloc = {ticker_safe_cash: 1.0}
                safe_name = "Cash"
                
            # 비중 배분 (공격 유지 비중 + 방어 비중)
            if w_def_atk > 0:
                target[ticker_risky_base] = w_def_atk
            
            for t, w in safe_alloc.items():
                target[t] = w * w_def_safe
                
            if w_def_atk > 0:
                mode = f"Defense ({safe_name}) + {ticker_risky_base} {int(w_def_atk*100)}%"
            else:
                mode = f"Defense ({safe_name})"

        # --- [2] 리밸런싱 및 수수료 ---
        is_signal_chg = (mode != prev_mode_desc)
        is_month_start = (today.month != prev_date.month)
        
        if is_signal_chg or is_month_start:
            turnover = 0
            all_t = set(current_weights.keys()) | set(target.keys())
            for t in all_t:
                turnover += abs(target.get(t,0) - current_weights.get(t,0))
            
            fee = (turnover / 2) * current_capital * commission_rate
            current_capital -= fee
            current_weights = target.copy()
            
            if fee > 10:
                trade_logs.append({
                    "Date": today.strftime('%Y-%m-%d'),
                    "Desc": mode,
                    "Amount": round(current_capital),
                    "Fee": round(fee)
                })
            
            if is_signal_chg:
                position_changes.append({"Date": today, "Desc": mode})
        
        prev_mode_desc = mode

        # --- [3] 수익률 적용 ---
        val = 0
        new_w = {}
        for t, w in current_weights.items():
            r = df_ret[t].iloc[i]
            v = current_capital * w * (1+r)
            new_w[t] = v
            val += v
        current_capital = val if val > 0 else 0
        
        for t in new_w: 
            if current_capital > 0: new_w[t] /= current_capital
        current_weights = new_w
        
        equity_curve.append(current_capital)
        weights_history.append(current_weights.copy())
        
        # --- [4] 벤치마크 (공격 1 자산) ---
        r_bench = df_ret[ticker_risky_base].iloc[i]
        bench_capital = bench_capital * (1 + r_bench)
        bench_equity.append(bench_capital)

        # --- [5] 세금 (연말) ---
        year_realized_gain += (current_capital - (equity_curve[-2] if len(equity_curve)>1 else initial_capital))
        
        if apply_tax and (today.year != prev_date.year):
            # 전략 세금
            tax = max(0, year_realized_gain - 2500000) * 0.22
            if tax > 0: 
                current_capital -= tax
                trade_logs.append({"Date": today.strftime('%Y-%m-%d'), "Desc": "Tax", "Amount": -round(tax), "Fee": 0})
            year_realized_gain = 0
            
            # 벤치마크 세금
            b_gain = bench_capital - bench_year_start
            tax_b = max(0, b_gain - 2500000) * 0.22
            if tax_b > 0: bench_capital -= tax_b
            bench_year_start = bench_capital

    progress_bar.progress(1.0)

    # -------------------------------------------------------------------------
    # 6. Action Plan (오늘 해야 할 일) - 최상단 배치
    # -------------------------------------------------------------------------
    st.divider()
    st.markdown("### 🔔 오늘 해야 할 일 (Action Plan)")
    
    # 최신 데이터 기준 신호 재확인
    last_canary = score_df[f'{ticker_canary}_Score'].iloc[-1]
    last_base = score_df[f'{ticker_risky_base}_Score'].iloc[-1]
    last_cash = score_df[f'{ticker_safe_cash}_Score'].iloc[-1]
    last_bond = score_df[f'{ticker_safe_bond}_Score'].iloc[-1]
    
    # 1. 목표 포트폴리오 계산
    today_target = {}
    today_mode = ""
    
    if (last_canary > 0) and (last_base > 0):
        today_target = {ticker_risky_base: w_base, ticker_risky_lev: w_lev}
        today_mode = "🐂 상승장 (Bull Market)"
        mode_color = "green"
    else:
        # 방어 로직
        s_alloc = {}
        if (last_cash > 0) and (last_bond > 0): s_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
        elif last_bond > 0: s_alloc = {ticker_safe_bond: 1.0}
        else: s_alloc = {ticker_safe_cash: 1.0}
        
        if w_def_atk > 0:
            today_target[ticker_risky_base] = w_def_atk
        
        for t, w in s_alloc.items():
            today_target[t] = w * w_def_safe
            
        today_mode = "🛡️ 방어장 (Defense Mode)"
        mode_color = "orange"

    # 2. 화면 표시
    ac1, ac2 = st.columns([1, 2])
    
    with ac1:
        # [수정됨] res_df 대신 dates[-1] 사용 (res_df는 아래에서 생성됨)
        st.info(f"**기준일:** {dates[-1].strftime('%Y-%m-%d')}")
        
        if mode_color == "green":
            st.success(f"## {today_mode}")
        else:
            st.warning(f"## {today_mode}")
            
    with ac2:
        st.markdown("#### 👇 포트폴리오 목표 비중 (Target)")
        
        action_str = ""
        for t, w in today_target.items():
            if w > 0.001:
                action_str += f"- **{t}**: `{w*100:.1f}%`\n"
        
        st.markdown(action_str)
        st.caption("※ 오늘(혹은 내일) 장이 열리면 위 비율대로 계좌를 리밸런싱하세요.")

    st.divider()

    # -------------------------------------------------------------------------
    # 7. 성과 및 차트
    # -------------------------------------------------------------------------
    res_df = pd.DataFrame({
        'Equity': equity_curve,
        'Bench_Equity': bench_equity
    }, index=dates[:len(equity_curve)])
    
    res_df['Price'] = df_price[ticker_risky_base]
    res_df['MA'] = ma_line
    res_df['Canary_Score'] = score_df[f'{ticker_canary}_Score']
    
    # 성과 요약
    final = equity_curve[-1]
    final_b = bench_equity[-1]
    cagr = (final/initial_capital)**(1/(len(res_df)/252))-1
    cagr_b = (final_b/initial_capital)**(1/(len(res_df)/252))-1
    
    peak = res_df['Equity'].cummax()
    mdd = ((res_df['Equity'] - peak) / peak).min()
    
    peak_b = res_df['Bench_Equity'].cummax()
    mdd_b = ((res_df['Bench_Equity'] - peak_b) / peak_b).min()

    st.subheader(f"📊 성과 요약 (vs {ticker_risky_base})")
    m1, m2, m3 = st.columns(3)
    m1.metric("최종 자산", f"{final:,.0f} 원", delta=f"vs Bench: {final - final_b:,.0f}")
    m2.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr-cagr_b)*100:.2f}%p")
    m3.metric("MDD", f"{mdd*100:.2f} %", delta=f"Bench MDD: {mdd_b*100:.2f}%")

    # --- 차트 4종 세트 ---
    st.subheader("📈 상세 분석")
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # 1. Equity
    axes[0, 0].plot(res_df.index, res_df['Equity'], color='firebrick', label='My Strategy')
    axes[0, 0].plot(res_df.index, res_df['Bench_Equity'], color='gray', linestyle='--', alpha=0.6, label=f'{ticker_risky_base}')
    axes[0, 0].set_yscale('log'); axes[0, 0].set_title(f'1. Equity Curve (vs {ticker_risky_base})')
    axes[0, 0].legend()
    axes[0, 0].grid(True, which='both', alpha=0.3)
    
    # 2. Drawdown (Comparison)
    dd = (res_df['Equity'] - peak) / peak
    dd_b = (res_df['Bench_Equity'] - peak_b) / peak_b
    axes[0, 1].plot(res_df.index, dd * 100, color='blue', label='Strategy MDD')
    axes[0, 1].plot(res_df.index, dd_b * 100, color='gray', linestyle=':', alpha=0.5, label=f'{ticker_risky_base} MDD')
    axes[0, 1].fill_between(res_df.index, dd * 100, 0, color='blue', alpha=0.1)
    axes[0, 1].set_title('2. Drawdown Comparison (%)')
    axes[0, 1].legend()
    axes[0, 1].grid(True, alpha=0.3)
    
    # 3. Market Trend
    axes[1, 0].plot(res_df.index, res_df['Price'], color='black', alpha=0.6, label='Price')
    axes[1, 0].plot(res_df.index, res_df['MA'], color='orange', linestyle='--', label='MA')
    axes[1, 0].set_title(f'3. Market Trend ({ticker_risky_base})')
    axes[1, 0].legend()
    axes[1, 0].grid(True, alpha=0.3)
    
    # 4. Canary
    axes[1, 1].plot(res_df.index, res_df['Canary_Score'], color='purple')
    axes[1, 1].axhline(0, color='red', linestyle='--')
    axes[1, 1].fill_between(res_df.index, res_df['Canary_Score'], 0, where=(res_df['Canary_Score'] < 0), color='red', alpha=0.2)
    axes[1, 1].set_title(f'4. Risk Signal ({ticker_canary})')
    axes[1, 1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    st.pyplot(fig)
    
    # --- 엑셀 생성 (3개 시트) ---
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

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        res_df.to_excel(writer, sheet_name='Daily_Data')
        pd.DataFrame(trade_logs).to_excel(writer, sheet_name='Trade_Log', index=False)
        m_pivot.to_excel(writer, sheet_name='Monthly_Returns')
        
        wb = writer.book
        ws = writer.sheets['Monthly_Returns']
        fmt = wb.add_format({'num_format': '0.00%'})
        ws.set_column(1, 13, 10, fmt)

    st.download_button("📥 통합 리포트 다운로드 (Excel)", output.getvalue(), "HAA_Final_Report.xlsx")