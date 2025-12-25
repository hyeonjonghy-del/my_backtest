import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import datetime
import io
import warnings

# 경고 무시
warnings.filterwarnings('ignore')

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="만능 전략 백테스터 (Tax Ver)",
    page_icon="📊",
    layout="wide"
)

st.title("📊 만능 전략 백테스터 (Safe vs Risky + 양도세)")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 사이드바: 사용자 입력
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 설정 패널")
    
    # 2-1. 자산 및 세금 설정 (New)
    st.subheader("1. 자산 및 세금 설정")
    initial_capital = st.number_input("초기 투자금 (원)", value=100000000, step=1000000, format="%d")
    apply_tax = st.checkbox("양도세(22%) 적용", value=True, help="매년 연초에 전년도 수익 250만원 공제 후 22% 세금 차감")

    # 2-2. 종목 설정
    st.subheader("2. 투자 종목")
    ticker_safe = st.text_input("안전 자산 (Safe)", value="069500.KS")
    ticker_risky = st.text_input("공격 자산 (Risky)", value="122630.KS")
    
    # 2-3. 기간 설정
    st.subheader("3. 백테스트 기간")
    start_col, end_col = st.columns(2)
    with start_col:
        start_date_input = st.date_input("시작일", datetime.date(2015, 1, 1))
    with end_col:
        end_date_input = st.date_input("종료일", datetime.date.today())
    
    # 2-4. 전략 옵션
    st.subheader("4. 전략 옵션")
    ma_window = st.number_input("이동평균(MA) 기간", min_value=5, max_value=365, value=120)
    
    # 2-5. 금리 필터
    st.subheader("5. 거시경제(금리) 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=False)
    rate_ticker = st.text_input("금리 티커", value="^TNX")
    rate_ma_window = st.number_input("금리 MA 기간", min_value=5, max_value=365, value=120)
    exposure_ratio = st.slider("금리 상승 시 투자 비중", 0.0, 1.0, 0.7, 0.1)

    run_btn = st.button("🚀 백테스트 실행", type="primary")

# -----------------------------------------------------------------------------
# 3. 함수 정의 (데이터 다운로드 및 처리)
# -----------------------------------------------------------------------------
@st.cache_data
def get_data(tickers, start, end):
    """데이터 다운로드 및 캐싱"""
    try:
        end_dt = end + datetime.timedelta(days=1)
        data = yf.download(tickers, start=start, end=end_dt, auto_adjust=True, progress=False)
        
        if isinstance(data.columns, pd.MultiIndex):
            if 'Close' in data.columns.levels[0]:
                df = data['Close'].copy()
            else:
                try:
                    df = data.xs('Close', level=0, axis=1)
                except:
                    df = data.copy()
        else:
            df = data.copy()
            
        return df.dropna()
    except Exception as e:
        st.error(f"데이터 다운로드 중 오류 발생: {e}")
        return pd.DataFrame()

def run_strategy(df, t_safe, t_risky, t_rate, ma_win, rate_ma_win, use_rate, exp_ratio, init_cap, apply_tax):
    """전략 로직 계산 (세금 포함)"""
    df = df.copy()
    
    # 지표 계산
    stock_ma_col = f'Stock_{ma_win}MA'
    df[stock_ma_col] = df[t_safe].rolling(window=ma_win).mean()

    rate_ma_col = f'Rate_{rate_ma_win}MA'
    df[rate_ma_col] = df[t_rate].rolling(window=rate_ma_win).mean()

    # 신호 생성
    df['Stock_Signal'] = 0
    df.loc[df[t_safe] > df[stock_ma_col], 'Stock_Signal'] = 1

    df['Rate_Hike'] = 0
    df.loc[df[t_rate] > df[rate_ma_col], 'Rate_Hike'] = 1

    # 신호 시프트 (오늘 신호로 내일 매매)
    df['Stock_Signal'] = df['Stock_Signal'].shift(1)
    df['Rate_Hike'] = df['Rate_Hike'].shift(1)

    # 일별 수익률 계산
    daily_ret = df.pct_change().fillna(0)
    
    # -------------------------------------------------------
    # 자산 및 세금 계산 로직 (Loop)
    # -------------------------------------------------------
    equity_curve = []
    positions = []
    
    current_capital = init_cap
    year_start_capital = init_cap
    
    # Loop 최적화를 위한 numpy 변환
    signals = df['Stock_Signal'].values
    rate_hikes = df['Rate_Hike'].values
    safe_rets = daily_ret[t_safe].values
    risky_rets = daily_ret[t_risky].values
    dates = df.index
    
    for i in range(len(df)):
        # 1. 자산 선택 및 수익률 적용
        if signals[i] == 1:
            base_ret = risky_rets[i]
            pos = 1
        else:
            base_ret = safe_rets[i]
            pos = 0
            
        # 금리 필터 적용
        if use_rate and rate_hikes[i] == 1:
            real_ret = base_ret * exp_ratio
        else:
            real_ret = base_ret
            
        # 자산 업데이트
        current_capital = current_capital * (1 + real_ret)
        
        # 2. 세금 계산 (연도가 바뀐 경우 체크)
        # i가 0보다 크고, 어제와 연도가 다르면 => 새해 첫 거래일
        if i > 0 and dates[i].year != dates[i-1].year:
            if apply_tax:
                # 전년도 수익 계산 (전일 자산 - 작년 초 자산)
                last_year_end_capital = equity_curve[-1] 
                year_profit = last_year_end_capital - year_start_capital
                
                # 공제 250만원 적용
                taxable_income = year_profit - 2500000
                if taxable_income > 0:
                    tax = taxable_income * 0.22
                    current_capital -= tax # 세금 차감
                    
                # 올해 기준 자산 업데이트 (세금 차감 후 금액이 올해 시작 금액)
                year_start_capital = current_capital
            else:
                # 세금 적용 안하면 그냥 갱신
                year_start_capital = current_capital

        equity_curve.append(current_capital)
        positions.append(pos)
        
    df['My_Asset'] = equity_curve
    df['Position'] = positions
    
    # 전략 일별 수익률 역산 (차트/통계용)
    df['Strategy_Ret'] = df['My_Asset'].pct_change().fillna(0)

    # 비교군 (단순 보유)도 초기 자금 기준으로 계산
    df['Hold_Safe'] = init_cap * (1 + daily_ret[t_safe]).cumprod()
    df['Hold_Risky'] = init_cap * (1 + daily_ret[t_risky]).cumprod()
    
    df['Peak'] = df['My_Asset'].cummax()
    df['MDD'] = (df['My_Asset'] - df['Peak']) / df['Peak'] * 100
    
    return df, stock_ma_col, rate_ma_col

# -----------------------------------------------------------------------------
# 4. 메인 실행 로직
# -----------------------------------------------------------------------------
if run_btn:
    tickers = [ticker_safe, ticker_risky, rate_ticker]
    
    with st.spinner('데이터 다운로드 및 전략 계산 중...'):
        raw_df = get_data(tickers, start_date_input, end_date_input)
        
        if raw_df.empty:
            st.error("데이터를 가져오지 못했습니다. 티커나 기간을 확인해주세요.")
        else:
            # 전략 실행 (파라미터 추가됨)
            df, stock_ma_name, rate_ma_name = run_strategy(
                raw_df, ticker_safe, ticker_risky, rate_ticker,
                ma_window, rate_ma_window, use_rate_filter, exposure_ratio,
                initial_capital, apply_tax
            )
            
            # ----------------------------------
            # 결과 요약 (Metrics)
            # ----------------------------------
            total_days = len(df)
            final_value = df['My_Asset'].iloc[-1]
            total_profit = final_value - initial_capital
            total_ret_pct = (total_profit / initial_capital) * 100
            
            # CAGR 계산
            cagr = (final_value / initial_capital) ** (252/total_days) - 1
            mdd_min = df['MDD'].min()

            st.markdown("### 📈 성과 요약")
            col1, col2, col3 = st.columns(3)
            col1.metric("최종 자산 (Total Equity)", f"{final_value:,.0f} 원", delta=f"{total_profit:,.0f} 원")
            col2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%", delta=f"총 수익률: {total_ret_pct:.2f}%")
            col3.metric("최대 낙폭 (MDD)", f"{mdd_min:.2f}%", delta_color="inverse")
            
            # ----------------------------------
            # 차트 시각화
            # ----------------------------------
            st.markdown("### 📊 상세 차트")
            
            trades = df['Position'].diff().fillna(0)
            buy_signals = df[trades == 1].index
            buy_prices = df.loc[buy_signals, ticker_safe]
            sell_signals = df[trades == -1].index
            sell_prices = df.loc[sell_signals, ticker_safe]

            fig, axes = plt.subplots(2, 2, figsize=(16, 12))
            
            # (1) 주식 시장 신호
            ax1 = axes[0, 0]
            ax1.plot(df.index, df[ticker_safe], label=f'{ticker_safe}', color='black', alpha=0.5)
            ax1.plot(df.index, df[stock_ma_name], label=f'MA {ma_window}', color='orange')
            ax1.scatter(buy_signals, buy_prices, marker='^', color='red', s=80, label='Buy Risky', zorder=5)
            ax1.scatter(sell_signals, sell_prices, marker='v', color='blue', s=80, label='Buy Safe', zorder=5)
            ax1.set_title(f'Market Signal ({ticker_safe})')
            ax1.legend()
            ax1.grid(alpha=0.3)

            # (2) 금리 신호
            ax2 = axes[0, 1]
            ax2.plot(df.index, df[rate_ticker], label='Rate', color='purple', alpha=0.7)
            ax2.plot(df.index, df[rate_ma_name], label=f'MA {rate_ma_window}', color='green', linestyle='--')
            if use_rate_filter:
                ax2.fill_between(df.index, df[rate_ticker], df[rate_ma_name], 
                                where=(df[rate_ticker] > df[rate_ma_name]), color='gray', alpha=0.2, label='Filter On')
            ax2.set_title(f'Macro Filter ({rate_ticker})')
            ax2.legend()
            ax2.grid(alpha=0.3)

            # (3) 자산 성장 (금액 기준)
            ax3 = axes[1, 0]
            ax3.plot(df.index, df['My_Asset'], label='Strategy (After Tax)', color='red', linewidth=2)
            ax3.plot(df.index, df['Hold_Safe'], label=f'{ticker_safe} Hold', color='green', linestyle='--', alpha=0.5)
            ax3.plot(df.index, df['Hold_Risky'], label=f'{ticker_risky} Hold', color='orange', linestyle='--', alpha=0.5)
            ax3.set_yscale('log')
            ax3.set_title(f'Portfolio Growth (Log Scale, Start: {initial_capital:,} won)')
            ax3.legend()
            ax3.grid(alpha=0.3)

            # (4) MDD
            ax4 = axes[1, 1]
            ax4.fill_between(df.index, df['MDD'], 0, color='blue', alpha=0.3)
            ax4.plot(df.index, df['MDD'], color='blue', linewidth=1)
            ax4.axhline(y=-30, color='red', linestyle='--', label='-30% Line')
            ax4.set_title('Drawdown (%)')
            ax4.legend()
            ax4.grid(alpha=0.3)

            plt.tight_layout()
            st.pyplot(fig)

            # ----------------------------------
            # 엑셀 다운로드
            # ----------------------------------
            st.markdown("### 📥 결과 다운로드")
            
            # 월별 수익률 (금액 기반 계산)
            monthly_resample = df['My_Asset'].resample('ME').last()
            monthly_ret = monthly_resample.pct_change().fillna(0)
            
            monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
            monthly_table.columns = [f"{c}월" for c in monthly_table.columns]
            
            # 연간 수익률
            yearly_resample = df['My_Asset'].resample('YE').last()
            yearly_ret = yearly_resample.pct_change().fillna(0)
            yearly_ret.index = yearly_ret.index.year
            monthly_table['Year_Total'] = yearly_ret

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='Daily_Data')
                monthly_table.to_excel(writer, sheet_name='Monthly_Returns')
                
                workbook = writer.book
                worksheet = writer.sheets['Monthly_Returns']
                format_pct = workbook.add_format({'num_format': '0.00%'})
                worksheet.set_column('B:N', 10, format_pct)

            st.download_button(
                label="엑셀 파일 다운로드 (Excel)",
                data=buffer.getvalue(),
                file_name=f"Backtest_Tax_Applied.xlsx",
                mime="application/vnd.ms-excel"
            )