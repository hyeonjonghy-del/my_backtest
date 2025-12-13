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
    page_title="방어형 전략 백테스터 (SGOV Mix)",
    page_icon="🛡️",
    layout="wide"
)

st.title("🛡️ 만능 전략 백테스터 (SPY 방어 + 현금 SGOV 파킹)")
st.markdown("""
이 앱은 **상승장에서는 공격형 자산(예: UPRO)**, **하락장에서는 안전 자산(예: SPY)**을 운용합니다.
또한, **금리 상승기** 등 리스크 구간에서는 공격형 자산 비중을 줄이고 **남은 현금을 단기채(SGOV)에 파킹**하여 이자 수익을 추구합니다.
""")

# -----------------------------------------------------------------------------
# 2. 사이드바: 사용자 입력
# -----------------------------------------------------------------------------
st.sidebar.header("1. 투자 종목 설정")
ticker_safe = st.sidebar.text_input("안전 자산 (Bear/Defense)", value="SPY")
ticker_risky = st.sidebar.text_input("공격 자산 (Bull/Risky)", value="UPRO")
cash_ticker = st.sidebar.text_input("현금 파킹 (Parking)", value="SGOV")

st.sidebar.header("2. 전략 옵션")
start_date = st.sidebar.date_input("시작일", pd.to_datetime("2015-01-01"))
ma_window = st.sidebar.number_input("추세 판단 이동평균(일)", value=120, min_value=5)

st.sidebar.header("3. 거시경제(금리) 필터")
use_rate_filter = st.sidebar.checkbox("금리 필터 사용", value=True)
rate_ticker = st.sidebar.text_input("금리 지표 (예: ^TNX)", value="^TNX")
rate_ma_window = st.sidebar.number_input("금리 이동평균(일)", value=120)
exposure_ratio = st.sidebar.slider("리스크 발생 시 공격자산 비중", 0.0, 1.0, 0.6, 0.1)
st.sidebar.caption(f"나머지 {100 - exposure_ratio*100:.0f}%는 {cash_ticker}에 투자됩니다.")

# -----------------------------------------------------------------------------
# 3. 데이터 로딩 및 처리 함수 (캐싱 적용)
# -----------------------------------------------------------------------------
@st.cache_data
def load_data(safe, risky, rate, cash, start):
    tickers = [safe, risky, rate]
    
    # 1) 메인 데이터 다운로드
    data = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    
    # yfinance 버전 이슈 대응 (MultiIndex 처리)
    if isinstance(data.columns, pd.MultiIndex):
        if 'Close' in data.columns.levels[0]:
             df = data['Close'].copy()
        else:
             df = data.copy() # fallback
    else:
        df = data.copy() # 단일 레벨 컬럼인 경우
    
    df = df.dropna()
    
    # 2) SGOV(현금성 자산) 데이터 별도 처리
    try:
        cash_data = yf.download(cash, start=start, progress=False, auto_adjust=True)
        if isinstance(cash_data.columns, pd.MultiIndex) and 'Close' in cash_data.columns.levels[0]:
             cash_series = cash_data['Close'][cash]
        elif isinstance(cash_data.columns, pd.MultiIndex): # ticker 레벨이 없는 경우
             cash_series = cash_data.iloc[:, 0]
        else:
             cash_series = cash_data['Close']
        
        # 메인 df 인덱스에 맞춰 리인덱싱 (빈 날짜는 NaN)
        cash_series = cash_series.reindex(df.index)
    except Exception as e:
        st.warning(f"현금 자산({cash}) 데이터 로드 실패. 0% 수익률로 대체합니다. ({e})")
        cash_series = pd.Series(0, index=df.index)
        
    df[cash] = cash_series
    return df

# -----------------------------------------------------------------------------
# 4. 백테스트 실행
# -----------------------------------------------------------------------------
if st.button("🚀 백테스트 실행", type="primary"):
    with st.spinner('데이터를 다운로드하고 전략을 계산 중입니다...'):
        
        # 데이터 로드
        try:
            df = load_data(ticker_safe, ticker_risky, rate_ticker, cash_ticker, start_date)
        except Exception as e:
            st.error(f"데이터 다운로드 중 오류 발생: {e}")
            st.stop()

        # 지표 계산
        stock_ma_col = f'Stock_{ma_window}MA'
        df[stock_ma_col] = df[ticker_safe].rolling(window=ma_window).mean()

        rate_ma_col = f'Rate_{rate_ma_window}MA'
        df[rate_ma_col] = df[rate_ticker].rolling(window=rate_ma_window).mean()

        # 신호 생성
        df['Stock_Signal'] = 0
        df.loc[df[ticker_safe] > df[stock_ma_col], 'Stock_Signal'] = 1 # Bull Market

        df['Rate_Hike'] = 0
        df.loc[df[rate_ticker] > df[rate_ma_col], 'Rate_Hike'] = 1 # Rate Risk

        # [중요] 신호 하루 뒤로 밀기 (오늘 신호보고 내일 대응)
        df['Stock_Signal'] = df['Stock_Signal'].shift(1)
        df['Rate_Hike'] = df['Rate_Hike'].shift(1)

        # 수익률 계산 & 포지션 스위칭 Loop
        daily_ret = df.pct_change()
        
        # SGOV 없는 구간 0 처리
        if cash_ticker in daily_ret.columns:
            daily_ret[cash_ticker] = daily_ret[cash_ticker].fillna(0)

        df['Strategy_Ret'] = 0.0
        df['Position_Type'] = "Safe" # 시각화용 태그

        # 벡터화 대신 명시적 Loop 사용 (복잡한 조건 처리 용이)
        # (속도 개선을 위해 numpy 배열로 변환하여 처리할 수도 있지만, 가독성을 위해 유지)
        
        strategy_rets = []
        position_types = []
        
        # 인덱스 접근을 위해 numpy 변환
        np_stock_sig = df['Stock_Signal'].values
        np_rate_hike = df['Rate_Hike'].values
        np_risky_ret = daily_ret[ticker_risky].fillna(0).values
        np_safe_ret = daily_ret[ticker_safe].fillna(0).values
        np_cash_ret = daily_ret[cash_ticker].values
        
        for i in range(len(df)):
            if i == 0: # 첫날은 수익률 0
                strategy_rets.append(0.0)
                position_types.append("-")
                continue

            # Bull Market
            if np_stock_sig[i] == 1:
                # 금리 필터 발동 (Mix 모드)
                if use_rate_filter and np_rate_hike[i] == 1:
                    invest_w = exposure_ratio
                    cash_w = 1.0 - exposure_ratio
                    ret = (np_risky_ret[i] * invest_w) + (np_cash_ret[i] * cash_w)
                    strategy_rets.append(ret)
                    position_types.append(f"Mix")
                else:
                    # Full Bull
                    strategy_rets.append(np_risky_ret[i])
                    position_types.append("Risky")
            
            # Bear Market
            else:
                strategy_rets.append(np_safe_ret[i])
                position_types.append("Safe")

        df['Strategy_Ret'] = strategy_rets
        df['Position_Type'] = position_types

        # 성과 지표 계산
        df['My_Asset'] = (1 + df['Strategy_Ret']).cumprod()
        df['Hold_Safe'] = (1 + daily_ret[ticker_safe]).cumprod()
        
        df['Peak'] = df['My_Asset'].cummax()
        df['MDD'] = (df['My_Asset'] - df['Peak']) / df['Peak'] * 100
        
        final_return = df['My_Asset'].iloc[-1]
        cagr = final_return ** (252 / len(df)) - 1
        mdd_min = df['MDD'].min()

        # ---------------------------------------------------------------------
        # 5. 결과 시각화
        # ---------------------------------------------------------------------
        
        # (1) 핵심 지표 표시
        st.subheader("📊 백테스트 요약")
        col1, col2, col3 = st.columns(3)
        col1.metric("최종 자산 배수", f"{final_return:.2f}배")
        col2.metric("CAGR (연평균 수익률)", f"{cagr * 100:.2f}%")
        col3.metric("Max MDD (최대 낙폭)", f"{mdd_min:.2f}%")

        # (2) 차트 그리기
        st.subheader("📈 상세 차트")
        fig, axes = plt.subplots(2, 2, figsize=(16, 12))

        # Asset Growth
        ax1 = axes[0, 0]
        ax1.plot(df.index, df['My_Asset'], label='My Strategy', color='red', linewidth=2)
        ax1.plot(df.index, df['Hold_Safe'], label=f'{ticker_safe} Buy&Hold', color='black', linestyle='--', alpha=0.5)
        ax1.set_title('1. Portfolio Growth (Log Scale)')
        ax1.set_yscale('log')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Drawdown
        ax2 = axes[0, 1]
        ax2.plot(df.index, df['MDD'], label='Drawdown', color='blue')
        ax2.fill_between(df.index, df['MDD'], 0, color='blue', alpha=0.1)
        ax2.set_title('2. Drawdown Risk')
        ax2.grid(True, alpha=0.3)

        # Position Status
        ax3 = axes[1, 0]
        ax3.plot(df.index, df['My_Asset'], color='gray', alpha=0.3)
        
        # 상태별 산점도
        mask_risky = df['Position_Type'] == "Risky"
        mask_mix = df['Position_Type'] == "Mix"
        mask_safe = df['Position_Type'] == "Safe"
        
        ax3.scatter(df[mask_risky].index, df[mask_risky]['My_Asset'], s=15, color='red', label='Risky (Full)', alpha=0.6)
        ax3.scatter(df[mask_mix].index, df[mask_mix]['My_Asset'], s=15, color='orange', label=f'Mix ({exposure_ratio*100:.0f}%+{cash_ticker})', alpha=0.6)
        ax3.scatter(df[mask_safe].index, df[mask_safe]['My_Asset'], s=15, color='green', label=f'Safe ({ticker_safe})', alpha=0.1)
        
        ax3.set_title('3. Position Status')
        ax3.legend(loc='upper left')
        ax3.grid(True, alpha=0.3)

        # Rate Filter
        ax4 = axes[1, 1]
        ax4.plot(df.index, df[rate_ticker], label='Interest Rate', color='purple')
        ax4.plot(df.index, df[rate_ma_col], label='Rate MA', color='green', linestyle='--')
        ax4.fill_between(df.index, df[rate_ticker], df[rate_ma_col], 
                         where=(df[rate_ticker] > df[rate_ma_col]), color='orange', alpha=0.3, label='Hike Zone (Mix)')
        ax4.set_title('4. Macro (Rate) Filter')
        ax4.legend()
        ax4.grid(True, alpha=0.3)

        st.pyplot(fig)

        # ---------------------------------------------------------------------
        # 6. 엑셀 다운로드
        # ---------------------------------------------------------------------
        st.subheader("💾 결과 다운로드")
        
        # 월별 수익률 테이블 생성
        monthly_ret = df['Strategy_Ret'].resample('ME').apply(lambda x: (1 + x).prod() - 1)
        monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
        monthly_table.columns = [f"{c}월" for c in monthly_table.columns]
        
        yearly_ret = df['Strategy_Ret'].resample('YE').apply(lambda x: (1 + x).prod() - 1)
        yearly_ret.index = yearly_ret.index.year
        monthly_table['Year_Total'] = yearly_ret

        # 엑셀 파일 메모리에 쓰기
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Daily_Data')
            monthly_table.to_excel(writer, sheet_name='Monthly_Returns')
        
        excel_data = output.getvalue()
        
        st.download_button(
            label="📥 엑셀 파일 다운로드 (Excel)",
            data=excel_data,
            file_name=f"Backtest_{ticker_risky}_Mix_{cash_ticker}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )