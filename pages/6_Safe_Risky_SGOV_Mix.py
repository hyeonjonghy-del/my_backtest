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
    
    if isinstance(data.columns, pd.MultiIndex):
        if 'Close' in data.columns.levels[0]:
             df = data['Close'].copy()
        else:
             df = data.copy()
    else:
        df = data.copy()
    
    df = df.dropna()
    
    # SGOV 별도 처리
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
if st.button("🚀 오늘의 매매 신호 확인하기", type="primary", use_container_width=True):
    with st.spinner('미국 시장 데이터를 분석 중입니다...'):
        
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
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        last_date = df.index[-1].strftime('%Y-%m-%d')
        
        # 상태 판단 로직
        is_bull = last_row[ticker_safe] > last_row[stock_ma_col]
        is_hike = last_row[rate_ticker] > last_row[rate_ma_col]
        
        was_bull = prev_row[ticker_safe] > prev_row[stock_ma_col]
        was_hike = prev_row[rate_ticker] > prev_row[rate_ma_col]

        # ---------------------------------------------------------------------
        # [핵심] 오늘의 행동 지침 (Action Plan) - UI 개선
        # ---------------------------------------------------------------------
        st.write("") # 여백
        
        # 목표 포지션 메시지 설정
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

        # 이전 상태 판단
        if was_bull:
            if use_rate_filter and was_hike: prev_state = "bull_mix"
            else: prev_state = "bull_full"
        else:
            prev_state = "bear"
            
        # ------------------- 결과 출력 -------------------
        
        # [상황 1] 매매 필요 없음 (유지)
        if cur_state == prev_state:
            st.success(f"☕ **오늘은 아무것도 할 필요 없습니다.**", icon="✅")
            st.markdown(f"""
                <div style='padding: 20px; background-color: #f0fdf4; border-radius: 10px; border: 1px solid #bbf7d0;'>
                    <h3 style='color: #166534; margin:0;'>현재 포지션 유지: {target_name}</h3>
                    <p style='color: #374151; margin-top: 10px;'>
                        어제와 시장 상황이 동일합니다. 계좌를 열지 않고 편안하게 주무셔도 됩니다.<br>
                        <b>보유 종목:</b> <br>
                        {detail_msg.replace('\n', '<br>')}
                    </p>
                </div>
            """, unsafe_allow_html=True)
            
        # [상황 2] 매매 필요 (변경)
        else:
            st.error(f"🚨 **매매 신호 발생! 포지션을 변경하세요.**", icon="⚠️")
            st.markdown(f"""
                <div style='padding: 20px; background-color: #fef2f2; border-radius: 10px; border: 1px solid #fecaca;'>
                    <h3 style='color: #991b1b; margin:0;'>목표 포지션: {target_name}</h3>
                    <p style='color: #374151; margin-top: 10px;'>
                        시장 추세가 변경되었습니다. 오늘 밤 장 시작 시 아래와 같이 리밸런싱 하세요.<br>
                        <b>변경 목표:</b> <br>
                        {detail_msg.replace('\n', '<br>')}
                    </p>
                </div>
            """, unsafe_allow_html=True)

        st.divider()

        # 근거 데이터 표시
        c1, c2, c3 = st.columns(3)
        c1.metric("기준 날짜", last_date)
        c1.caption("미국 현지 종가 기준")
        
        trend_diff = last_row[ticker_safe] - last_row[stock_ma_col]
        c2.metric(f"추세 ({ticker_safe})", 
                  f"{'상승장 📈' if is_bull else '하락장 📉'}",
                  f"{trend_diff:.2f} (Price-MA)")
        
        rate_diff = last_row[rate_ticker] - last_row[rate_ma_col]
        c3.metric(f"리스크 ({rate_ticker})", 
                  f"{'위험 ⚠️' if is_hike else '안전 🆗'}",
                  f"{rate_diff:.4f} (Rate-MA)")

        st.divider()

        # ---------------------------------------------------------------------
        # 백테스트 차트 (하단 배치)
        # ---------------------------------------------------------------------
        with st.expander("📊 과거 시뮬레이션 결과 보기 (Click)"):
            
            # 백테스트 로직 (검증용)
            df['Stock_Signal'] = 0
            df.loc[df[ticker_safe] > df[stock_ma_col], 'Stock_Signal'] = 1
            df['Rate_Hike'] = 0
            df.loc[df[rate_ticker] > df[rate_ma_col], 'Rate_Hike'] = 1
            
            # 하루 shift
            df['Stock_Signal'] = df['Stock_Signal'].shift(1)
            df['Rate_Hike'] = df['Rate_Hike'].shift(1)
            
            daily_ret = df.pct_change()
            if cash_ticker in daily_ret.columns:
                daily_ret[cash_ticker] = daily_ret[cash_ticker].fillna(0)
                
            strategy_rets = []
            
            # Numpy 변환 가속
            np_stock_sig = df['Stock_Signal'].values
            np_rate_hike = df['Rate_Hike'].values
            np_risky_ret = daily_ret[ticker_risky].fillna(0).values
            np_safe_ret = daily_ret[ticker_safe].fillna(0).values
            np_cash_ret = daily_ret[cash_ticker].values
            
            for i in range(len(df)):
                if i == 0: 
                    strategy_rets.append(0.0)
                    continue
                    
                if np_stock_sig[i] == 1: # Bull
                    if use_rate_filter and np_rate_hike[i] == 1: # Mix
                        ret = (np_risky_ret[i] * exposure_ratio) + (np_cash_ret[i] * (1-exposure_ratio))
                        strategy_rets.append(ret)
                    else: # Full Bull
                        strategy_rets.append(np_risky_ret[i])
                else: # Bear
                    strategy_rets.append(np_safe_ret[i])
                    
            df['Strategy_Ret'] = strategy_rets
            df['My_Asset'] = (1 + df['Strategy_Ret']).cumprod()
            df['Hold_Safe'] = (1 + daily_ret[ticker_safe]).cumprod()
            
            # 성과 지표
            final_return = df['My_Asset'].iloc[-1]
            cagr = final_return ** (252 / len(df)) - 1
            
            k1, k2 = st.columns(2)
            k1.metric("총 수익배수", f"{final_return:.2f}배")
            k2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")

            # 차트
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(df.index, df['My_Asset'], label='Strategy', color='red')
            ax.plot(df.index, df['Hold_Safe'], label=f'{ticker_safe} Buy&Hold', color='gray', alpha=0.5, linestyle='--')
            ax.set_yscale('log')
            ax.legend()
            st.pyplot(fig)
            
            # 엑셀 다운로드
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='Result')
            st.download_button("📥 백테스트 결과 엑셀 다운로드", output.getvalue(), "Backtest_Result.xlsx")