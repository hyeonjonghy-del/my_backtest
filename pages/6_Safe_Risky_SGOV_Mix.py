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

st.title("🛡️ Safe/Risky/Cash Mix Strategy (Verified Logic)")
st.markdown("""
**전략 개요 (Verified Version):**
- **로직:** 시그널 발생(T일) → 다음 날(T+1일) 장 마감(종가)에 매매 (시차 적용 완료)
- **자산 배분:**
    - **Bear (하락장):** 안전자산 100%
    - **Bull (상승장):**
        - **Low Risk (금리 안정):** 공격자산 100%
        - **High Risk (금리 급등):** 공격자산 + 현금 혼합 (리스크 관리)
- **데이터:** 수정주가 아님 (배당금 제외, 실제 시장가 기준)
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
            
        # SGOV 등이 상장 전이라 데이터가 없는 경우 0 처리하지 않고, 해당 기간엔 수익률 0으로 처리됨
        # (pct_change 시 fillna(0) 처리 예정)
            
        df_raw = full_df.ffill() # 결측치 보완
        
        # 2. 지표 계산 (전체 기간)
        # 안전자산 이평선
        series_safe = df_raw[ticker_safe]
        ma_safe = series_safe.rolling(window=ma_window).mean()
        
        # 금리 이평선
        series_rate = df_raw[ticker_rate]
        ma_rate = series_rate.rolling(window=rate_ma_window).mean()
        
        # 3. 시그널 판단 (T일 기준)
        # Bull: 종가 > 이평
        is_bull = series_safe > ma_safe
        # Hike(Risk): 금리 > 이평
        is_hike = series_rate > ma_rate
        
        # 4. 포지션 결정 (Raw State)
        # 0: Bear (Safe 100%)
        # 1: Bull_Full (Risky 100%)
        # 2: Bull_Mix (Risky X% + Cash Y%)
        
        conditions = [
            (~is_bull),  # Bear
            (is_bull & (~is_hike | ~use_rate_filter)), # Bull Full (필터 끄면 무조건 Full)
            (is_bull & is_hike & use_rate_filter)      # Bull Mix
        ]
        choices = ["Bear", "Bull_Full", "Bull_Mix"]
        
        raw_state = np.select(conditions, choices, default="Bear")
        raw_state_series = pd.Series(raw_state, index=df_raw.index)
        
        # [핵심] 5. 매매 시점 지연 (T+1일 매매)
        # T일의 시그널 -> T+1일 포지션에 반영
        trade_state = raw_state_series.shift(1)
        
        # 6. 시뮬레이션 루프
        # 사용자 시작일 이후 데이터만 사용
        sim_start = pd.to_datetime(start_date)
        if sim_start < df_raw.index[0]: sim_start = df_raw.index[0]
        
        df_sim = df_raw.loc[sim_start:].copy()
        # 시그널도 잘라냄
        trade_state = trade_state.loc[sim_start:]
        # 혹시 앞부분 잘려서 NaN이면 Bear로 시작
        trade_state = trade_state.fillna("Bear")
        
        # 필요한 자산만 모음 (수익률 계산용)
        # Cash 티커가 없거나 데이터가 비어있을 수 있으므로 처리
        asset_cols = [ticker_safe, ticker_risky]
        if ticker_cash in df_raw.columns:
            asset_cols.append(ticker_cash)
            
        equity = initial_capital
        peak = equity
        history = []
        
        # 초기 포지션 (첫날 시그널 기준)
        first_state = trade_state.iloc[0]
        curr_w = {}
        
        if first_state == "Bear":
            curr_w = {ticker_safe: 1.0}
        elif first_state == "Bull_Full":
            curr_w = {ticker_risky: 1.0}
        elif first_state == "Bull_Mix":
            curr_w = {ticker_risky: exposure_ratio}
            if ticker_cash in df_raw.columns:
                curr_w[ticker_cash] = 1.0 - exposure_ratio
                
        # 가중치 0 제거
        curr_w = {k:v for k,v in curr_w.items() if v > 0}
        
        # 첫 진입 수수료
        equity -= equity * fee_rate
        
        for i in range(len(df_sim)):
            today = df_sim.index[i]
            state = trade_state.iloc[i]
            
            # [A] 목표 가중치 설정
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
            
            # [B] 수익률 반영 (보유 중인 자산)
            day_ret = 0
            if i > 0:
                for t, w in curr_w.items():
                    # 데이터프레임에서 등락률 계산
                    # 당일 종가 / 전일 종가 - 1
                    # (df_raw 전체에서 계산해두는 게 빠르지만, 가독성 위해 루프 내 처리)
                    try:
                        r = df_raw[t].pct_change().loc[today]
                        if pd.isna(r): r = 0
                    except:
                        r = 0
                    day_ret += r * w
            
            equity *= (1 + day_ret)
            
            # [C] 리밸런싱 (종가 기준)
            action = ""
            
            # 딕셔너리 비교 (키와 값이 모두 같아야 함)
            # 부동소수점 오차 고려하여 약간의 허용오차를 둘 수도 있으나,
            # 여기선 상태(State)가 바뀌면 구성이 바뀌므로 키 비교로 충분
            # (단, Bull_Mix -> Bull_Mix인데 비율만 바뀔 일은 없음)
            
            # 키 집합 비교 + 값 비교
            is_same = (curr_w.keys() == target_w.keys())
            if is_same:
                for k in curr_w:
                    if abs(curr_w[k] - target_w[k]) > 1e-6:
                        is_same = False; break
            
            if not is_same:
                action = "SWITCH"
                equity -= equity * fee_rate
                curr_w = target_w
                
            # MDD
            if equity > peak: peak = equity
            dd = (equity - peak) / peak
            
            # 로그용 포지션 문자열
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
                # 차트용
                "Safe_Price": df_sim[ticker_safe].iloc[i],
                "Safe_MA": ma_safe.loc[today] if today in ma_safe.index else None
            })
            
        # 결과 정리
        res_df = pd.DataFrame(history).set_index("Date")
        
        # 벤치마크 (Safe 자산 보유 시)
        res_df['Benchmark'] = (1 + df_raw[ticker_safe].loc[sim_start:].pct_change().fillna(0)).cumprod() * initial_capital
        # 길이 맞춤
        res_df = res_df.loc[res_df.index <= df_sim.index[-1]]

        # 성과 지표
        final = res_df['Equity'].iloc[-1]
        final_b = res_df['Benchmark'].iloc[-1]
        days = (res_df.index[-1] - res_df.index[0]).days
        if days > 0:
            cagr = (final / initial_capital) ** (365 / days) - 1
            cagr_b = (final_b / initial_capital) ** (365 / days) - 1
        else: cagr = 0; cagr_b = 0
            
        mdd = res_df['Drawdown(%)'].min() / 100.0

        # UI 출력
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Final Balance", f"{final:,.0f} KRW", delta=f"vs SPY: {final - final_b:,.0f}")
        c2.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr - cagr_b)*100:.2f}%p")
        c3.metric("MDD", f"{mdd*100:.2f} %")
        st.divider()
        
        # 월별 수익률
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

        # 탭 구성
        tab1, tab2, tab3 = st.tabs(["📊 Chart", "📝 Trade Logs", "📅 Monthly Returns"])
        
        with tab1:
            st.subheader("Strategy Performance")
            fig, ax = plt.subplots(3, 1, figsize=(14, 16), gridspec_kw={'height_ratios': [2, 1, 1]})
            
            # 1. Equity
            ax[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='Strategy')
            ax[0].plot(res_df.index, res_df['Benchmark'], color='gray', linestyle='--', alpha=0.6, label='Benchmark (Safe)')
            ax[0].set_yscale('log')
            ax[0].set_title("Equity Curve (Log)")
            ax[0].legend()
            
            # 2. MDD
            ax[1].plot(res_df.index, res_df['Drawdown(%)'], color='blue')
            ax[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0, color='blue', alpha=0.1)
            ax[1].set_title("Drawdown (%)")
            
            # 3. Signal Check
            # 안전자산 가격과 이평선만 표시 (시그널 확인용)
            ax[2].plot(res_df.index, res_df['Safe_Price'], color='black', label=f'{ticker_safe} Price')
            ax[2].plot(res_df.index, res_df['Safe_MA'], color='orange', linestyle='--', label=f'MA ({ma_window})')
            ax[2].set_title(f"Trend Signal ({ticker_safe})")
            ax[2].legend()
            
            st.pyplot(fig)
            
        with tab2:
            st.dataframe(res_df.sort_index(ascending=False), use_container_width=True)
            
        with tab3:
            st.dataframe(pivot_table.style.applymap(color_map).format("{:.2%}", na_rep=""), use_container_width=True)
            
        # 엑셀 다운로드
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            res_df.to_excel(writer, sheet_name='Daily_Log')
            pivot_table.to_excel(writer, sheet_name='Monthly_Returns')
            
        st.download_button("📥 엑셀 결과 다운로드", output.getvalue(), f"Verified_Mix_{ticker_safe}.xlsx")