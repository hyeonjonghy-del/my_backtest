import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import datetime
import io
import FinanceDataReader as fdr
import time  # 데이터 다운로드 지연 및 재시도를 위한 모듈

# -----------------------------------------------------------------------------
# 1. 페이지 설정 및 캐싱 함수
# -----------------------------------------------------------------------------
st.set_page_config(page_title="KOSPI 모멘텀 전략", page_icon="🇰🇷")

@st.cache_data(ttl=3600*24) # 24시간 동안 데이터 캐싱
def get_kospi_data(start_year, sample_size):
    """
    KOSPI 시가총액 상위 종목의 데이터를 다운로드합니다.
    Streamlit 캐시를 사용하여 속도를 최적화합니다.
    """
    # 1. 상장 종목 가져오기
    df_list = fdr.StockListing('KOSPI')
    df_list = df_list.sort_values(by='Marcap', ascending=False)
    
    # 상위 N개 선정
    target_df = df_list.head(sample_size)
    tickers = target_df['Code'].tolist()
    code_map = target_df.set_index('Code')['Name'].to_dict()
    
    # 2. 주가 데이터 다운로드
    all_prices = []
    fetch_year = start_year - 2
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    failed_stocks = []
    
    for i, ticker in enumerate(tickers):
        # 진행상황 표시
        status_text.text(f"데이터 다운로드 중.. ({i+1}/{len(tickers)}) - {code_map.get(ticker)}")
        progress_bar.progress((i + 1) / len(tickers))
        
        success = False
        max_retries = 3  # 최대 3번까지 재시도
        
        for attempt in range(max_retries):
            try:
                # 데이터 다운로드 시도
                df = fdr.DataReader(ticker, str(fetch_year))['Close']
                df.name = ticker
                all_prices.append(df)
                success = True
                
                # 성공 시 트래픽 방지용으로 아주 잠깐 대기 후 다음 종목으로 넘어감
                time.sleep(0.05) 
                break 
                
            except Exception:
                # 실패하면 1초 대기 후 다시 시도 (서버 접속 일시 차단 방지)
                time.sleep(1)
                continue
        
        # 3번 모두 실패했을 경우에만 누락 명단에 추가
        if not success:
            failed_stocks.append(f"{code_map.get(ticker)} ({ticker})")
            
    status_text.empty()
    progress_bar.empty()
    
    # 다운로드에 완전히 실패한 종목이 있다면 화면에 경고창으로 안내
    if failed_stocks:
        st.warning(f"⚠️ 다음 종목들은 네트워크 문제로 3회 재시도했으나 데이터를 가져오지 못해 제외되었습니다:\n\n{', '.join(failed_stocks)}")
    
    if not all_prices:
        return pd.DataFrame(), {}

    price_df = pd.concat(all_prices, axis=1).fillna(method='ffill')
    return price_df, code_map

# -----------------------------------------------------------------------------
# 2. 사이드바 UI
# -----------------------------------------------------------------------------
st.title("🇰🇷 KOSPI 상대 모멘텀 전략")
st.markdown("KOSPI 대형주 중 **최근 수익률이 좋은 종목**으로 포트폴리오를 교체하는 전략입니다.")

with st.sidebar:
    st.header("⚙️ 전략 설정")
    
    start_year = st.number_input("시작 연도", value=2020, min_value=2000, max_value=2024)
    
    sample_size = st.slider("투자 유니버스 (시총 상위 N개)", 50, 300, 200, step=50,
                            help="데이터 다운로드 시간이 길어질 수 있으니 적절히 조절하세요.")
    
    top_n = st.number_input("보유 종목 수 (Top N)", value=20, min_value=1)
    
    rebalance_map = {
        "1개월 (월간)": 1,
        "3개월 (분기)": 3,
        "6개월 (반기)": 6,
        "12개월 (연간)": 12
    }
    rebal_label = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()), index=0)
    rebalance_step = rebalance_map[rebal_label]
    
    momentum_window = st.number_input("모멘텀 기간 (개월)", value=12, help="과거 몇 개월 수익률을 비교할까요?")

    st.markdown("---")
    export_excel_option = st.checkbox("📥 엑셀 다운로드 기능 활성화", value=True)
    
    run_btn = st.button("🚀 전략 실행", type="primary")

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
if run_btn:
    with st.spinner("데이터를 분석하고 있습니다... 이 작업은 네트워크 상태에 따라 시간이 소요될 수 있습니다."):
        # 1. 데이터 로드
        df_price, code_map = get_kospi_data(start_year, sample_size)
        
        if df_price.empty:
            st.error("데이터를 가져오지 못했습니다.")
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
            
            # 벤치마크 (KOSPI 200)
            try:
                kospi_bm = fdr.DataReader('KS200', start=full_returns.index[0], end=full_returns.index[-1])['Close']
                bm_ret = kospi_bm.pct_change().fillna(0)
                bm_cum = (1 + bm_ret).cumprod()
                bm_cum = bm_cum / bm_cum.iloc[0]
            except:
                bm_cum = None

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
                ax[0].plot(cum_returns.index, cum_returns, label='Momentum Strategy', color='red')
                if bm_cum is not None:
                    ax[0].plot(bm_cum.index, bm_cum, label='KOSPI 200', color='gray', linestyle='--', alpha=0.7)
                ax[0].set_title("Strategy Growth (Log Scale)")
                ax[0].set_yscale('log')
                ax[0].legend()
                ax[0].grid(alpha=0.3)
                
                ax[1].fill_between(drawdown.index, drawdown*100, 0, color='blue', alpha=0.2)
                ax[1].plot(drawdown.index, drawdown*100, color='blue', linewidth=1)
                ax[1].set_title("Drawdown (%)")
                ax[1].grid(alpha=0.3)
                st.pyplot(fig)
                
            with tab2:
                st.subheader(f"📢 오늘 기준 추천 종목 (Top {top_n})")
                df_picks_display = df_picks.copy()
                df_picks_display['1년 수익률'] = df_picks_display['1년 수익률'].apply(lambda x: f"{x*100:.2f}%")
                df_picks_display['현재가'] = df_picks_display['현재가'].apply(lambda x: f"{x:,.0f}원")
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
                        format_money = workbook.add_format({'num_format': '#,##0'})
                        
                        worksheet_picks = writer.sheets['Current_Picks']
                        worksheet_picks.set_column('C:C', 12, format_pct)
                        worksheet_picks.set_column('D:D', 12, format_money)

                    st.success("파일 생성 완료!")
                    st.download_button(
                        label="📥 엑셀 파일 다운로드 (Excel)",
                        data=buffer.getvalue(),
                        file_name=f"KOSPI_Momentum_{start_year}_Result.xlsx",
                        mime="application/vnd.ms-excel"
                    )
        else:
            st.warning("수익률을 계산할 수 없습니다. 기간이나 종목 수를 확인해주세요.")