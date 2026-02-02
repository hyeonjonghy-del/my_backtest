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
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(
    page_title="Safe/Risky/Cash Mix Strategy",
    page_icon="🛡️",
    layout="wide"
)

st.title("🛡️ Safe/Risky/Cash Mix Strategy (Tax Option Added)")
st.markdown("""
**전략 개요 (Verified Version):**
- **로직:** 시그널 발생(T일) → 다음 날(T+1일) 장 마감(종가)에 매매
- **자산 배분:**
    - **Bear (하락장):** 안전자산 100%
    - **Bull (상승장):**
        - **Low Risk (금리 안정):** 공격자산 100%
        - **High Risk (금리 급등):** 공격자산 + 현금 혼합 (리스크 관리)
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 사이드바 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. 투자 종목 설정")
    ticker_safe = st.text_input("안전 자산 (Bear)", value="SPY")
    ticker_risky = st.text_input("공격 자산 (Bull)", value="UPRO")
    ticker_cash = st.text_input("현금 파킹 (Cash)", value="SGOV")

    st.header("2. 전략 옵션")
    start_date = st.date_input("시작일", pd.to_datetime("2020-01-01"))
    initial_capital = st.number_input("초기 자본", value=100000000, step=1000000)
    fee_rate = st.number_input("매매 수수료 (%)", value=0.02, step=0.01) / 100.0
    
    # [NEW] 세금 옵션 추가
    apply_tax = st.checkbox("양도소득세 22% 차감 (수익 발생 시)", value=False)
    if apply_tax:
        st.caption("ℹ️ 최종 총 수익금의 22%를 세금으로 제하고 계산합니다.")
    
    ma_window = st.number_input("추세 판단 이평선 (일)", value=120, min_value=5)

    st.header("3. 리스크(금리) 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    ticker_rate = st.text_input("금리 지표", value="^TNX")
    rate_ma_window = st.number_input("금리 이평선 (일)", value=120)
    exposure_ratio = st.slider("리스크 시 공격비중", 0.0, 1.0, 0.6, 0.1)
    st.caption(f"나머지 {100 - exposure_ratio*100:.0f}%는 현금({ticker_cash}) 보유")

# -----------------------------------------------------------------------------
# 3. 데이터 로드 (배당 제외, Raw Close)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600*24)
def load_data_verified(safe, risky, rate, cash):
    tickers = [safe, risky, rate, cash]
    # [중요] auto_adjust=False (배당 착시 제거)
    df = yf.download(tickers, start="2000-01-01", progress=False, auto_adjust=False)
    
    # 멀티인덱스 처리
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
            df = df['Close'].copy()
        else:
            df = df.copy()
            if df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
    
    # 중복 제거
    df = df.loc[~df.index.duplicated(keep='first')]
    return df.sort_index()

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 Run Verified Backtest", type="primary", use_container_width=True):
    with st.spinner('검증된 로직으로 분석 중...'):
        
        # 1. 데이터 준비
        full_df = load_data_verified(ticker_safe, ticker_risky, ticker_rate, ticker_cash)
        
        # 필수 데이터 확인
        req_cols = [ticker_safe, ticker_risky, ticker_rate]
        if not all(c in full_df.columns for c in req_cols):
            st.error(f"데이터 부족: {req_cols} 중 일부를 가져오지 못했습니다.")
            st.stop()
            
        df_raw = full_df.ffill() # 결측치 보완
        
        # 2. 지표 계산 (전체 기간)
        series_safe = df_raw[ticker_safe]
        ma_safe = series_safe.rolling(window=ma_window).mean()
        
        series_rate = df_raw[ticker_rate]
        ma_rate = series_rate.rolling(window=rate_ma_window).mean()
        
        # 3. 시그널 판단 (T일 기준)
        is_bull = series_safe > ma_safe
        is_hike = series_rate > ma_rate
        
        # 4. 포지션 결정 (Raw State)
        conditions = [
            (~is_bull),  # Bear
            (is_bull & (~is_hike | ~use_rate_filter)), # Bull Full
            (is_bull & is_hike & use_rate_filter)      # Bull Mix
        ]
        choices = ["Bear", "Bull_Full", "Bull_Mix"]
        
        raw_state = np.select(conditions, choices, default="Bear")
        raw_state_series = pd.Series(raw_state, index=df_raw.index)
        
        # 5. 매매 시점 지연 (T+1일 매매)
        trade_state = raw_state_series.shift(1)
        
        # 6. 시뮬레이션 루프
        sim_start = pd.to_datetime(start_date)
        if sim_start < df_raw.index[0]: sim_start = df_raw.index[0]
        
        df_sim = df_raw.loc[sim_start:].copy()
        trade_state = trade_state.loc[sim_start:].fillna("Bear")
        
        equity = initial_capital
        peak = equity
        history = []
        
        # 초기 포지션 설정
        first_state = trade_state.iloc[0]
        curr_w = {}
        if first_state == "Bear": curr_w = {ticker_safe: 1.0}
        elif first_state == "Bull_Full": curr_w = {ticker_risky: 1.0}
        elif first_state == "Bull_Mix": 
            curr_w = {ticker_risky: exposure_ratio}
            if ticker_cash in df_raw.columns: curr_w[ticker_cash] = 1.0 - exposure_ratio
        
        curr_w = {k:v for k,v in curr_w.items() if v > 0}
        equity -= equity * fee_rate # 첫 진입 수수료
        
        for i in range(len(df_sim)):
            today = df_sim.index[i]
            state = trade_state.iloc[i]
            
            # [A] 목표 가중치
            target_w = {}
            if state == "Bear":
                target_w = {ticker_safe: 1.0}
            elif state == "Bull_Full":
                target_w = {ticker_risky: 1.0}
            elif state == "Bull_Mix":
                target_w = {ticker_risky: exposure_ratio}
                if ticker_cash in df_raw.columns:
                    target_w[ticker_cash] = 1.0 - exposure_ratio
            
            target_w = {k:v for k,v in target_w.items() if v > 0}
            
            # [B] 수익률 반영
            day_ret = 0
            if i > 0:
                for t, w in curr_w.items():
                    try:
                        r = df_raw[t].pct_change().loc[today]
                        if pd.isna(r): r = 0
                    except: r = 0
                    day_ret += r * w
            
            equity *= (1 + day_ret)
            
            # [C] 리밸런싱 Check
            action = ""
            is_same = (curr_w.keys() == target_w.keys())
            if is_same:
                for k in curr_w:
                    if abs(curr_w[k] - target_w[k]) > 1e-6:
                        is_same = False; break
            
            if not is_same:
                action = "SWITCH"
                equity -= equity * fee_rate
                curr_w = target_w
                
            # MDD & Log
            if equity > peak: peak = equity
            dd = (equity - peak) / peak
            
            pos_str = ""
            if state == "Bear": pos_str = f"Bear ({ticker_safe})"
            elif state == "Bull_Full": pos_str = f"Bull Full ({ticker_risky})"
            elif state == "Bull_Mix": pos_str = f"Bull Mix ({ticker_risky}+{ticker_cash})"
            
            history.append({
                "Date": today,
                "State": state,
                "Position": pos_str,
                "Action": action,
                "Equity": round(equity),
                "Daily_Return(%)": round(day_ret * 100, 2),
                "Drawdown(%)": round(dd * 100, 2),
                "Safe_Price": df_sim[ticker_safe].iloc[i],
                "Safe_MA": ma_safe.loc[today] if today in ma_safe.index else None
            })
            
        res_df = pd.DataFrame(history).set_index("Date")
        res_df['Benchmark'] = (1 + df_raw[ticker_safe].loc[sim_start:].pct_change().fillna(0)).cumprod() * initial_capital
        res_df = res_df.loc[res_df.index <= df_sim.index[-1]]

        # ==============================================================================
        # [NEW] 오늘/내일 행동 가이드 (Today's Action Guide)
        # ==============================================================================
        st.divider()
        st.markdown("### 📢 오늘의 투자 가이드 (Today's Action)")
        
        last_date = df_raw.index[-1]
        last_safe_p = df_raw[ticker_safe].iloc[-1]
        last_safe_m = ma_safe.iloc[-1]
        
        last_rate_p = df_raw[ticker_rate].iloc[-1]
        last_rate_m = ma_rate.iloc[-1]
        
        is_bull_now = last_safe_p > last_safe_m
        is_hike_now = last_rate_p > last_rate_m
        
        current_state = "Bear"
        if is_bull_now:
            if use_rate_filter and is_hike_now:
                current_state = "Bull_Mix"
            else:
                current_state = "Bull_Full"
        
        st.caption(f"기준 데이터: {last_date.strftime('%Y-%m-%d')} 종가")
        
        col_g1, col_g2 = st.columns([1, 2])
        with col_g1:
            st.metric(label=f"{ticker_safe} 추세", value=f"{last_safe_p:,.2f}", delta=f"{last_safe_p - last_safe_m:.2f} (vs 이평선)", delta_color="normal")
            trend_emoji = "📈 상승장 (Bull)" if is_bull_now else "📉 하락장 (Bear)"
            risk_emoji = "🔥 금리 주의!" if (is_hike_now and use_rate_filter) else "🍀 금리 안정"
            st.text(f"{trend_emoji}")
            st.text(f"{risk_emoji}")

        with col_g2:
            if current_state == "Bear":
                st.error(f"🛑 **[하락장 방어]** 현재 시장이 좋지 않습니다.\n\n👉 **{ticker_safe} (안전자산)** 100% 보유 (공격 자산 매도)")
            elif current_state == "Bull_Full":
                st.success(f"🚀 **[강한 상승장]** 시장과 금리가 모두 좋습니다.\n\n👉 **{ticker_risky} (공격자산)** 100% 보유 (전액 투자)")
            elif current_state == "Bull_Mix":
                mix_pct = int(exposure_ratio * 100)
                cash_pct = 100 - mix_pct
                st.warning(f"⚠️ **[리스크 관리]** 상승장이지만 금리가 높습니다.\n\n👉 **{ticker_risky}**: {mix_pct}% / **{ticker_cash}**: {cash_pct}% 비율로 리밸런싱 하세요.")

        # ==============================================================================
        # [NEW] 세금 계산 및 성과 지표
        # ==============================================================================
        final_pre_tax = res_df['Equity'].iloc[-1]
        profit = final_pre_tax - initial_capital
        tax_amount = 0
        
        if apply_tax and profit > 0:
            tax_amount = profit * 0.22
            final_balance = final_pre_tax - tax_amount
        else:
            final_balance = final_pre_tax

        final_b = res_df['Benchmark'].iloc[-1]
        days = (res_df.index[-1] - res_df.index[0]).days
        
        if days > 0:
            cagr = (final_balance / initial_capital) ** (365 / days) - 1
            cagr_b = (final_b / initial_capital) ** (365 / days) - 1
        else: cagr = 0; cagr_b = 0
            
        mdd = res_df['Drawdown(%)'].min() / 100.0

        st.divider()
        c1, c2, c3 = st.columns(3)
        
        # 세금 반영 여부에 따른 라벨 변경
        label_balance = "Final Balance (After Tax)" if apply_tax else "Final Balance"
        
        c1.metric(label_balance, f"{final_balance:,.0f} KRW", delta=f"세금: -{tax_amount:,.0f}" if tax_amount > 0 else None)
        c2.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr - cagr_b)*100:.2f}%p")
        c3.metric("MDD", f"{mdd*100:.2f} %")
        st.divider()
        
        # 차트 및 데이터 출력 (기존 유지)
        m_df = res_df[['Equity']].resample('M').last()
        m_df['Return'] = m_df['Equity'].pct_change()
        pivot_table = m_df['Return'].groupby([m_df.index.year, m_df.index.month]).sum().unstack()
        pivot_table.columns = [calendar.month_abbr[i] for i in pivot_table.columns]
        
        y_ret = res_df['Equity'].resample('Y').last().pct_change()
        y_ret.index = y_ret.index.year
        pivot_table['Total'] = y_ret
        
        if len(res_df) > 0:
            first_year = res_df.index.year[0]
            val_1st = res_df[res_df.index.year == first_year]['Equity'].iloc[-1]
            pivot_table.loc[first_year, 'Total'] = (val_1st / initial_capital) - 1
            
        def color_map(val):
            if pd.isna(val): return ''
            return f'color: {"red" if val < 0 else "green"}'

        tab1, tab2, tab3 = st.tabs(["📊 Chart", "📝 Trade Logs", "📅 Monthly Returns"])
        
        with tab1:
            st.subheader("Strategy Performance & Indicators")
            fig, ax = plt.subplots(4, 1, figsize=(14, 20), gridspec_kw={'height_ratios': [2, 1, 1, 1]})
            
            # 1. Equity
            ax[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='Strategy (Pre-Tax)')
            ax[0].plot(res_df.index, res_df['Benchmark'], color='gray', linestyle='--', alpha=0.6, label='Benchmark')
            ax[0].set_yscale('log')
            ax[0].set_title("1. Equity Curve (Log, Pre-Tax)")
            ax[0].legend()
            
            # 2. MDD
            ax[1].plot(res_df.index, res_df['Drawdown(%)'], color='blue')
            ax[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0, color='blue', alpha=0.1)
            ax[1].set_title("2. Drawdown (%)")
            
            # 3. Main Signal
            ax[2].plot(res_df.index, res_df['Safe_Price'], color='black', label=f'{ticker_safe} Price')
            ax[2].plot(res_df.index, res_df['Safe_MA'], color='orange', linestyle='--', label=f'MA ({ma_window})')
            ax[2].set_title(f"3. Main Trend Signal ({ticker_safe})")
            ax[2].legend()
            
            # 4. Risk Signal
            plot_rate = df_raw.loc[res_df.index, ticker_rate]
            plot_rate_ma = ma_rate.loc[res_df.index]
            ax[3].plot(res_df.index, plot_rate, color='purple', label=f'{ticker_rate} (Rate)')
            ax[3].plot(res_df.index, plot_rate_ma, color='green', linestyle='--', label=f'MA ({rate_ma_window})')
            ax[3].set_title(f"4. Risk Signal ({ticker_rate})")
            ax[3].legend()
            
            st.pyplot(fig)
            
        with tab2:
            st.dataframe(res_df.sort_index(ascending=False), use_container_width=True)
            
        with tab3:
            st.dataframe(pivot_table.style.applymap(color_map).format("{:.2%}", na_rep=""), use_container_width=True)
            
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            res_df.to_excel(writer, sheet_name='Daily_Log')
            pivot_table.to_excel(writer, sheet_name='Monthly_Returns')
            
        st.download_button("📥 엑셀 결과 다운로드", output.getvalue(), f"Verified_Mix_{ticker_safe}.xlsx")