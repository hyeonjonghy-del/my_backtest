import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings

# 경고 무시 및 차트 스타일 설정
warnings.filterwarnings('ignore')
plt.style.use('ggplot')

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="AI 투자 어드바이저",
    page_icon="🛡️",
    layout="wide"
)

st.title("🛡️ 만능 전략 AI 어드바이저")
st.caption("SPY(방어) / UPRO(공격) / SGOV(현금파킹) 자동 배분 전략")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 사이드바: 사용자 입력
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. 투자 종목 설정")
    ticker_safe = st.text_input("안전 자산 (Bear)", value="SPY")
    ticker_risky = st.text_input("공격 자산 (Bull)", value="UPRO")
    cash_ticker = st.text_input("현금 파킹 (Cash)", value="SGOV")

    st.header("2. 전략 옵션")
    start_date = st.date_input("시작일", pd.to_datetime("2015-01-01"))
    ma_window = st.number_input("추세 판단 이동평균(일)", value=120, min_value=5)

    st.header("3. 리스크(금리) 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    rate_ticker = st.text_input("금리 지표", value="^TNX")
    rate_ma_window = st.number_input("금리 이동평균(일)", value=120)
    exposure_ratio = st.slider("리스크 발생 시 공격비중", 0.0, 1.0, 0.6, 0.1)
    st.info(f"💡 리스크 발생 시 나머지 {100 - exposure_ratio*100:.0f}%는 \n{cash_ticker}에 투자하여 이자를 받습니다.")

# -----------------------------------------------------------------------------
# 3. 데이터 로딩 함수
# -----------------------------------------------------------------------------
@st.cache_data
def load_data(safe, risky, rate, cash, start):
    tickers = [safe, risky, rate]
    data = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    
    # yfinance 데이터 구조 처리
    if isinstance(data.columns, pd.MultiIndex):
        if 'Close' in data.columns.levels[0]:
             df = data['Close'].copy()
        else:
             df = data.copy()
    else:
        df = data.copy()
    
    df = df.dropna()
    
    # SGOV 별도 처리 (상장일 이슈 대응)
    try:
        cash_data = yf.download(cash, start=start, progress=False, auto_adjust=True)
        if isinstance(cash_data.columns, pd.MultiIndex):
             if 'Close' in cash_data.columns.levels[0]:
                 cash_series = cash_data['Close'][cash]
             else:
                 cash_series = cash_data.iloc[:, 0]
        else:
             cash_series = cash_data['Close']
        cash_series = cash_series.reindex(df.index)
    except Exception:
        cash_series = pd.Series(0, index=df.index)
        
    df[cash] = cash_series
    return df

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 전략 실행 (신호 + 백테스트)", type="primary", use_container_width=True):
    with st.spinner('시장 데이터 분석 및 시뮬레이션 중...'):
        
        # 1. 데이터 준비
        try:
            df = load_data(ticker_safe, ticker_risky, rate_ticker, cash_ticker, start_date)
        except Exception as e:
            st.error(f"데이터 오류: {e}")
            st.stop()

        # 2. 이동평균 계산
        stock_ma_col = f'Stock_{ma_window}MA'
        df[stock_ma_col] = df[ticker_safe].rolling(window=ma_window).mean()

        rate_ma_col = f'Rate_{rate_ma_window}MA'
        df[rate_ma_col] = df[rate_ticker].rolling(window=rate_ma_window).mean()

        # 3. 신호 판단 (오늘/어제 기준)
        if df.empty:
            st.error("데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
            st.stop()
            
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        last_date = df.index[-1].strftime('%Y-%m-%d')
        
        is_bull = last_row[ticker_safe] > last_row[stock_ma_col]
        is_hike = last_row[rate_ticker] > last_row[rate_ma_col]
        
        was_bull = prev_row[ticker_safe] > prev_row[stock_ma_col]
        was_hike = prev_row[rate_ticker] > prev_row[rate_ma_col]

        # ---------------------------------------------------------------------
        # [Part 1] 오늘의 행동 지침 (Action Plan)
        # ---------------------------------------------------------------------
        st.subheader(f"🔔 오늘의 행동 지침 ({last_date} 기준)")
        
        # 목표 포지션 설정
        if is_bull:
            if use_rate_filter and is_hike:
                target_name = "📉 혼합 포지션 (Risk Control)"
                detail_msg = f"- {ticker_risky}: {exposure_ratio*100:.0f}%\n- {cash_ticker}: {100 - exposure_ratio*100:.0f}%"
                cur_state = "bull_mix"
            else:
                target_name = "🚀 공격 포지션 (Full Bull)"
                detail_msg = f"- {ticker_risky}: 100%"
                cur_state = "bull_full"
        else:
            target_name = "🛡️ 방어 포지션 (Bear Defense)"
            detail_msg = f"- {ticker_safe}: 100%"
            cur_state = "bear"

        # 이전 상태 설정
        if was_bull:
            if use_rate_filter and was_hike: prev_state = "bull_mix"
            else: prev_state = "bull_full"
        else:
            prev_state = "bear"
            
        # 결과 박스 출력
        if cur_state == prev_state:
            st.success(f"☕ **오늘은 아무것도 할 필요 없습니다.**", icon="✅")
            st.markdown(f"""
                <div style='padding: 15px; background-color: #f0fdf4; border-radius: 10px; border: 1px solid #bbf7d0; margin-bottom: 20px;'>
                    <h4 style='color: #166534; margin:0;'>현재 포지션 유지: {target_name}</h4>
                    <p style='color: #374151; margin-top: 5px; margin-bottom: 0;'>
                        <b>보유 목표:</b><br>
                        {detail_msg.replace('\n', '<br>')}
                    </p>
                </div>
            """, unsafe_allow_html=True)
        else:
            st.error(f"🚨 **매매 신호 발생! 포지션을 변경하세요.**", icon="⚠️")
            st.markdown(f"""
                <div style='padding: 15px; background-color: #fef2f2; border-radius: 10px; border: 1px solid #fecaca; margin-bottom: 20px;'>
                    <h4 style='color: #991b1b; margin:0;'>변경 목표: {target_name}</h4>
                    <p style='color: #374151; margin-top: 5px; margin-bottom: 0;'>
                        <b>리밸런싱 목표:</b><br>
                        {detail_msg.replace('\n', '<br>')}
                    </p>
                </div>
            """, unsafe_allow_html=True)

        with st.expander("📋 판단 근거 (상세 데이터 보기)", expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("기준 날짜", last_date)
            trend_diff = last_row[ticker_safe] - last_row[stock_ma_col]
            c2.metric(f"추세 ({ticker_safe})", 
                      f"{'상승장 📈' if is_bull else '하락장 📉'}",
                      f"{trend_diff:.2f}")
            rate_diff = last_row[rate_ticker] - last_row[rate_ma_col]
            c3.metric(f"리스크 ({rate_ticker})", 
                      f"{'위험 ⚠️' if is_hike else '안전 🆗'}",
                      f"{rate_diff:.4f}")

        st.markdown("---")

        # ---------------------------------------------------------------------
        # [Part 2] 백테스트 결과 차트
        # ---------------------------------------------------------------------
        st.subheader("📊 전체 기간 시뮬레이션 (백테스트)")
        
        # 백테스트 신호 생성 (하루 Shift)
        df['Stock_Signal'] = 0
        df.loc[df[ticker_safe] > df[stock_ma_col], 'Stock_Signal'] = 1
        df['Rate_Hike'] = 0
        df.loc[df[rate_ticker] > df[rate_ma_col], 'Rate_Hike'] = 1
        
        # 오늘 신호보고 내일 매매 -> shift(1)
        df['Stock_Signal_Shift'] = df['Stock_Signal'].shift(1)
        df['Rate_Hike_Shift'] = df['Rate_Hike'].shift(1)
        
        # 수익률 계산
        daily_ret = df.pct_change()
        if cash_ticker in daily_ret.columns:
            daily_ret[cash_ticker] = daily_ret[cash_ticker].fillna(0)
            
        strategy_rets = []
        
        # 빠른 연산을 위해 Numpy 변환
        np_stock_sig = df['Stock_Signal_Shift'].values
        np_rate_hike = df['Rate_Hike_Shift'].values
        np_risky_ret = daily_ret[ticker_risky].fillna(0).values
        np_safe_ret = daily_ret[ticker_safe].fillna(0).values
        np_cash_ret = daily_ret[cash_ticker].values
        
        for i in range(len(df)):
            if i == 0: 
                strategy_rets.append(0.0)
                continue
                
            if np_stock_sig[i] == 1: # Bull Market
                if use_rate_filter and np_rate_hike[i] == 1: # Risk Control (Mix)
                    ret = (np_risky_ret[i] * exposure_ratio) + (np_cash_ret[i] * (1-exposure_ratio))
                    strategy_rets.append(ret)
                else: # Full Bull
                    strategy_rets.append(np_risky_ret[i])
            else: # Bear Market
                strategy_rets.append(np_safe_ret[i])
                    
        df['Strategy_Ret'] = strategy_rets
        
        # 지표 계산
        df['My_Asset'] = (1 + df['Strategy_Ret']).cumprod()
        df['Hold_Safe'] = (1 + daily_ret[ticker_safe]).cumprod()
        
        # MDD 계산
        df['Peak'] = df['My_Asset'].cummax()
        df['MDD'] = (df['My_Asset'] - df['Peak']) / df['Peak'] * 100
        
        # 성과 요약
        final_return = df['My_Asset'].iloc[-1]
        cagr = final_return ** (252 / len(df)) - 1
        mdd_min = df['MDD'].min()
        
        k1, k2, k3 = st.columns(3)
        k1.metric("최종 자산 배수", f"{final_return:.2f}배")
        k2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
        k3.metric("최대 낙폭 (Max MDD)", f"{mdd_min:.2f}%")

        # ---------------------------------------------------------------------
        # [차트 그리기] 2행 2열 (총 4개 차트)
        # ---------------------------------------------------------------------
        fig, axes = plt.subplots(2, 2, figsize=(16, 12)) # 사이즈 세로 확장
        
        # (1) 수익률 차트 (로그) - 좌측 상단
        axes[0, 0].plot(df.index, df['My_Asset'], label='Strategy', color='red', linewidth=1.5)
        axes[0, 0].plot(df.index, df['Hold_Safe'], label=f'{ticker_safe} Hold', color='black', alpha=0.3, linestyle='--')
        axes[0, 0].set_title('1. Asset Growth (Log Scale)')
        axes[0, 0].set_yscale('log')
        axes[0, 0].legend(loc='upper left')
        axes[0, 0].grid(True, which='both', alpha=0.3)
        
        # (2) MDD 차트 - 우측 상단
        axes[0, 1].plot(df.index, df['MDD'], label='Drawdown', color='blue', linewidth=1)
        axes[0, 1].fill_between(df.index, df['MDD'], 0, color='blue', alpha=0.1)
        axes[0, 1].set_title('2. Drawdown Risk (MDD)')
        axes[0, 1].legend(loc='lower left')
        axes[0, 1].grid(True, alpha=0.3)
        
        # (3) 마켓 시그널 (가격 vs 이평선) + 매매 신호 화살표 - 좌측 하단
        axes[1, 0].plot(df.index, df[ticker_safe], label='Price', color='black', linewidth=1, alpha=0.6)
        axes[1, 0].plot(df.index, df[stock_ma_col], label=f'MA ({ma_window})', color='orange', linewidth=1.5, alpha=0.8)
        
        # 매매 신호 포착
        df['Signal_Change'] = df['Stock_Signal'].diff()
        buy_risky = df[df['Signal_Change'] == 1]
        buy_safe = df[df['Signal_Change'] == -1]

        # 화살표 그리기
        axes[1, 0].plot(buy_risky.index, buy_risky[ticker_safe], '^', markersize=8, color='red', label='Buy Risky')
        axes[1, 0].plot(buy_safe.index, buy_safe[ticker_safe], 'v', markersize=8, color='blue', label='Buy Safe')

        axes[1, 0].set_title(f'3. Market Signal ({ticker_safe} vs MA)')
        axes[1, 0].legend(loc='best')
        axes[1, 0].grid(True, alpha=0.3)

        # (4) 금리 차트 (금리 vs 이평선) - 우측 하단
        axes[1, 1].plot(df.index, df[rate_ticker], label='Rate', color='purple', linewidth=1)
        axes[1, 1].plot(df.index, df[rate_ma_col], label=f'MA ({rate_ma_window})', color='green', linewidth=1.5)
        axes[1, 1].set_title(f'4. Interest Rate Risk ({rate_ticker} vs MA)')
        axes[1, 1].legend(loc='upper left')
        axes[1, 1].grid(True, alpha=0.3)

        plt.tight_layout() # 차트 간격 자동 조절
        st.pyplot(fig)
        
        # ---------------------------------------------------------------------
        # 엑셀 다운로드 (월별 데이터 포함)
        # ---------------------------------------------------------------------
        # 월별 수익률 계산
        try:
            # Pandas 2.0+ 대응
            monthly_ret = df['Strategy_Ret'].resample('ME').apply(lambda x: (1 + x).prod() - 1)
        except:
            monthly_ret = df['Strategy_Ret'].resample('M').apply(lambda x: (1 + x).prod() - 1)

        monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
        monthly_table.columns = [f"{c}월" for c in monthly_table.columns]
        
        # 연도별 수익률 (Year Total)
        try:
            yearly_ret = df['Strategy_Ret'].resample('YE').apply(lambda x: (1 + x).prod() - 1)
        except:
            yearly_ret = df['Strategy_Ret'].resample('Y').apply(lambda x: (1 + x).prod() - 1)
            
        yearly_ret.index = yearly_ret.index.year
        monthly_table['Year_Total'] = yearly_ret

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Daily_Data')
            monthly_table.to_excel(writer, sheet_name='Monthly_Returns')
            
        st.download_button("📥 백테스트 결과 엑셀 다운로드", output.getvalue(), "Backtest_Result.xlsx")