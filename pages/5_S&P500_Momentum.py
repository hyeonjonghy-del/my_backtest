import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import datetime
import io
import FinanceDataReader as fdr

# -----------------------------------------------------------------------------
# 1. 페이지 설정 및 캐싱 함수
# -----------------------------------------------------------------------------
st.set_page_config(page_title="S&P 500 모멘텀 전략", page_icon="🇺🇸")

@st.cache_data(ttl=3600*24) # 24시간 동안 데이터 캐싱
def get_sp500_data(start_year, sample_size):
    """
    S&P 500 종목의 데이터를 다운로드합니다.
    Streamlit 캐시를 사용하여 속도를 최적화합니다.
    """
    # 1. S&P 500 종목 리스트 가져오기
    try:
        df_list = fdr.StockListing('S&P500')
    except Exception as e:
        st.error(f"종목 리스트를 가져오는 중 오류가 발생했습니다: {e}")
        return pd.DataFrame(), {}
        
    # 컬럼명 통일 (Symbol -> Code, Security -> Name)
    # S&P500 리스팅은 보통 'Symbol', 'Security' 등의 컬럼을 가짐
    mapper = {'Symbol': 'Code', 'Security': 'Name'}
    df_list = df_list.rename(columns=mapper)
    
    # 필수 컬럼 확인 ('Code'가 없으면 진행 불가)
    if 'Code' not in df_list.columns:
        st.error("데이터 소스에서 종목 코드를 찾을 수 없습니다.")
        return pd.DataFrame(), {}
        
    # 'Name' 컬럼이 없는 경우 Code로 대체
    if 'Name' not in df_list.columns:
        df_list['Name'] = df_list['Code']

    # 상위 N개 선정 (S&P500은 이미 대형주이므로 리스트 순서대로 가져옴)
    target_df = df_list.head(sample_size)
    tickers = target_df['Code'].tolist()
    code_map = target_df.set_index('Code')['Name'].to_dict()
    
    # 2. 주가 데이터 다운로드
    all_prices = []
    fetch_year = start_year - 2
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, ticker in enumerate(tickers):
        try:
            status_text.text(f"데이터 다운로드 중.. ({i+1}/{len(tickers)}) - {code_map.get(ticker, ticker)}")
            progress_bar.progress((i + 1) / len(tickers))
            
            # 미국 주식 데이터 다운로드
            df = fdr.DataReader(ticker, str(fetch_year))['Close']
            df.name = ticker
            all_prices.append(df)
        except:
            continue
            
    status_text.empty()
    progress_bar.empty()
    
    if not all_prices:
        return pd.DataFrame(), {}

    price_df = pd.concat(all_prices, axis=1).fillna(method='ffill')
    return price_df, code_map

# -----------------------------------------------------------------------------
# 2. 사이드바 UI
# -----------------------------------------------------------------------------
st.title("🇺🇸 S&P 500 상대 모멘텀 전략")
st.markdown("S&P 500 종목 중 **최근 수익률이 좋은 종목**으로 포트폴리오를 교체하는 전략입니다.")

with st.sidebar:
    st.header("⚙️ 전략 설정")
    
    start_year = st.number_input("시작 연도", value=2015, min_value=2000, max_value=2024)
    
    # S&P 500은 약 500개 종목이므로 최대값을 505로 설정
    sample_size = st.slider("투자 유니버스 (종목 수)", 50, 505, 100, step=50,
                            help="S&P 500 리스트 상위 N개 종목을 사용합니다. 전체를 보려면 505로 설정하세요.")
    
    top_n = st.number_input("보유 종목 수 (Top N)", value=10, min_value=1)
    
    rebalance_map = {
        "1개월 (월간)": 1,
        "3개월 (분기)": 3,
        "6개월 (반기)": 6,
        "12개월 (연간)": 12
    }
    rebal_label = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()), index=1)
    rebalance_step = rebalance_map[rebal_label]
    
    momentum_window = st.number_input("모멘텀 기간 (개월)", value=12, help="과거 몇 개월 수익률을 비교할까요?")

    st.markdown("---")
    export_excel_option = st.checkbox("📥 엑셀 다운로드 기능 활성화", value=True)
    
    run_btn = st.button("🚀 전략 실행", type="primary")

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
if run_btn:
    with st.spinner("데이터를 분석하고 있습니다... (미국 주식 데이터는 시간이 조금 더 걸릴 수 있습니다)"):
        # 1. 데이터 로드
        df_price, code_map = get_sp500_data(start_year, sample_size)
        
        if df_price.empty:
            st.error("데이터를 가져오지 못했습니다. 잠시 후 다시 시도하거나 종목 수를 줄여보세요.")
            st.stop()

        # 2. 백테스트 시뮬레이션 준비
        start_dt = pd.to_datetime(f'{start_year}-01-01')
        if start_dt < df_price.index[0]: start_dt = df_price.index[0]
        end_dt = df_price.index[-1]
        
        # 리밸런싱 날짜 계산
        all_days = df_price.index
        target_months = list(range(1, 13, rebalance_step))
        rebalance_dates = []
        
        for year in range(start_dt.year, end_dt.year + 1):
            for month in target_months:
                month_data = all_days[(all_days.year == year) & (all_days.month == month)]
                if not month_data.empty:
                    rebalance_dates.append(month_data[-1])
        
        rebalance_dates = sorted(list(set([d for d in rebalance_dates if start_dt <= d <= end_dt])))
        
        # 시뮬레이션 루프
        portfolio_returns = []
        history_records = []
        
        for i in range(len(rebalance_dates) - 1):
            curr_date = rebalance_dates[i]
            next_date = rebalance_dates[i+1]
            
            # 모멘텀 계산 시점
            past_date_target = curr_date - pd.DateOffset(months=momentum_window)
            
            try:
                idx_loc = df_price.index.get_indexer([past_date_target], method='nearest')[0]
                past_date_real = df_price.index[idx_loc]
                
                price_curr = df_price.loc[:curr_date].iloc[-1]
                price_past = df_price.loc[past_date_real]
                
                mom_score = (price_curr - price_past) / price_past
                
                top_series = mom_score.nlargest(top_n)
                top_stocks = top_series.index.tolist()
                
                # 기록
                for stock in top_stocks:
                    history_records.append({
                        'Date': curr_date.strftime('%Y-%m-%d'),
                        'Code': stock,
                        'Name': code_map.get(stock, stock),
                        'Momentum': top_series[stock]
                    })
                
                price_period = df_price[top_stocks].loc[curr_date:next_date]
                if not price_period.empty:
                    period_ret = price_period.pct_change().fillna(0).mean(axis=1)
                    portfolio_returns.append(period_ret)
                    
            except Exception as e:
                continue
                
        # 결과 처리
        if portfolio_returns:
            full_returns = pd.concat(portfolio_returns)
            full_returns = full_returns[~full_returns.index.duplicated(keep='first')]
            
            cum_returns = (1 + full_returns).cumprod()
            running_max = cum_returns.cummax()
            drawdown = (cum_returns / running_max) - 1
            mdd = drawdown.min()
            
            total_days = (cum_returns.index[-1] - cum_returns.index[0]).days
            cagr = cum_returns.iloc[-1]**(365/total_days) - 1
            
            # 벤치마크 (S&P 500 Index)
            try:
                # FDR에서 S&P 500 지수 심볼: 'US500' 또는 'SPX' (데이터 소스에 따라 다름, 여기선 US500 시도)
                kospi_bm = fdr.DataReader('US500', start=full_returns.index[0], end=full_returns.index[-1])['Close']
                bm_ret = kospi_bm.pct_change().fillna(0)
                bm_cum = (1 + bm_ret).cumprod()
                bm_cum = bm_cum / bm_cum.iloc[0]
                bm_label = 'S&P 500 Index'
            except:
                bm_cum = None
                bm_label = 'Benchmark'

            # ----------------------------------
            # 데이터 준비 (엑셀 및 탭 표시용)
            # ----------------------------------
            # 1. 월별 수익률 테이블
            monthly_ret = full_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
            monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
            monthly_table.columns = [f"{c}월" for c in monthly_table.columns]
            
            # 2. 현재 추천 종목
            latest_date = df_price.index[-1]
            past_target = latest_date - pd.DateOffset(months=momentum_window)
            idx_loc = df_price.index.get_indexer([past_target], method='nearest')[0]
            past_real = df_price.index[idx_loc]
            
            p_curr = df_price.loc[latest_date]
            p_past = df_price.loc[past_real]
            
            curr_mom = (p_curr - p_past) / p_past
            curr_top = curr_mom.nlargest(top_n)
            
            picks_data = []
            for code, score in curr_top.items():
                picks_data.append({
                    '종목명': code_map.get(code, code),
                    '종목코드': code,
                    '1년 수익률': score, 
                    '현재가': p_curr[code]
                })
            df_picks = pd.DataFrame(picks_data)
            
            # 3. 매매 기록
            df_history = pd.DataFrame(history_records)

            # ----------------------------------
            # 결과 화면 출력
            # ----------------------------------
            col1, col2, col3 = st.columns(3)
            col1.metric("총 수익률", f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
            col2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
            col3.metric("최대 낙폭 (MDD)", f"{mdd*100:.2f}%", delta_color="inverse")
            
            tab1, tab2, tab3, tab4 = st.tabs(["📊 차트", "🏆 현재 추천 종목", "📅 월별 수익률", "📝 매매 기록"])
            
            with tab1:
                fig, ax = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [2, 1]})
                ax[0].plot(cum_returns.index, cum_returns, label='Momentum Strategy', color='blue') # 미국은 보통 상승이 초록/파랑이지만 가독성 위해 블루
                if bm_cum is not None:
                    ax[0].plot(bm_cum.index, bm_cum, label=bm_label, color='gray', linestyle='--', alpha=0.7)
                ax[0].set_title("Strategy Growth (Log Scale)")
                ax[0].set_yscale('log')
                ax[0].legend()
                ax[0].grid(alpha=0.3)
                
                ax[1].fill_between(drawdown.index, drawdown*100, 0, color='red', alpha=0.2)
                ax[1].plot(drawdown.index, drawdown*100, color='red', linewidth=1)
                ax[1].set_title("Drawdown (%)")
                ax[1].grid(alpha=0.3)
                st.pyplot(fig)
                
            with tab2:
                st.subheader(f"📢 오늘 기준 추천 종목 (Top {top_n})")
                # 화면 표시용 포맷팅
                df_picks_display = df_picks.copy()
                df_picks_display['1년 수익률'] = df_picks_display['1년 수익률'].apply(lambda x: f"{x*100:.2f}%")
                # 달러($) 포맷 적용
                df_picks_display['현재가'] = df_picks_display['현재가'].apply(lambda x: f"${x:,.2f}")
                st.table(df_picks_display)
                
            with tab3:
                st.subheader("📅 월별 수익률 Heatmap")
                st.dataframe(monthly_table.style.background_gradient(cmap='RdYlGn', axis=None).format("{:.2%}"))
                
            with tab4:
                st.subheader("📝 과거 매매 내역")
                st.dataframe(df_history)

            # ----------------------------------
            # 엑셀 다운로드 로직
            # ----------------------------------
            if export_excel_option:
                st.markdown("---")
                st.subheader("💾 데이터 내보내기")
                
                with st.spinner("엑셀 파일을 생성 중입니다..."):
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        df_history.to_excel(writer, sheet_name='Trade_History', index=False)
                        monthly_table.to_excel(writer, sheet_name='Monthly_Returns')
                        df_picks.to_excel(writer, sheet_name='Current_Picks', index=False)
                        
                        workbook = writer.book
                        format_pct = workbook.add_format({'num_format': '0.00%'})
                        format_money = workbook.add_format({'num_format': '$#,##0.00'}) # 달러 포맷
                        
                        worksheet_picks = writer.sheets['Current_Picks']
                        worksheet_picks.set_column('C:C', 12, format_pct)
                        worksheet_picks.set_column('D:D', 15, format_money)

                    st.success("파일 생성 완료!")
                    st.download_button(
                        label="📥 엑셀 파일 다운로드 (Excel)",
                        data=buffer.getvalue(),
                        file_name=f"SP500_Momentum_{start_year}_Result.xlsx",
                        mime="application/vnd.ms-excel"
                    )
        else:
            st.warning("수익률을 계산할 수 없습니다. 기간이나 종목 수를 확인해주세요.")