import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import datetime
import io
import FinanceDataReader as fdr
import time
import requests
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# 1. 페이지 설정 및 캐싱 함수
# -----------------------------------------------------------------------------
st.set_page_config(page_title="KOSPI 200 모멘텀 전략", page_icon="🇰🇷")

@st.cache_data(ttl=3600*24) 
def get_kospi200_data(start_year, sample_size):
    """
    네이버 금융에서 KOSPI 200 최신 편입 종목을 실시간으로 크롤링하여
    수동 업데이트 없이 항상 최신 유니버스를 유지합니다.
    """
    status_text = st.empty()
    status_text.text("🌐 네이버 금융에서 최신 KOSPI 200 명단을 수집하고 있습니다...")
    
    # 1. KOSPI 200 최신 종목 코드 스크래핑
    kospi200_codes = []
    base_url = "https://finance.naver.com/sise/entryJongmok.naver?&page="
    
    try:
        for page in range(1, 21):
            url = base_url + str(page)
            response = requests.get(url, headers={'User-agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(response.text, 'html.parser')
            
            tds = soup.find_all('td', class_='ctg')
            for td in tds:
                code = td.a['href'].split('code=')[-1]
                kospi200_codes.append(code)
    except Exception as e:
        st.error(f"명단 수집 중 오류가 발생했습니다: {e}")
        return pd.DataFrame(), {}
        
    status_text.empty()
    
    tickers = kospi200_codes[:sample_size]
    
    if not tickers:
        return pd.DataFrame(), {}

    # 2. 종목코드 - 종목명 매핑
    try:
        desc_df = fdr.StockListing('KRX') 
        code_map = desc_df.set_index('Code')['Name'].to_dict()
    except Exception as e:
        st.error(f"종목명 매핑 중 오류가 발생했습니다: {e}")
        code_map = {}
    
    # 3. 주가 데이터 다운로드
    all_prices = []
    fetch_year = start_year - 2
    
    progress_bar = st.progress(0)
    failed_stocks = []
    
    for i, ticker in enumerate(tickers):
        stock_name = code_map.get(ticker, ticker)
        status_text.text(f"📊 주가 데이터 다운로드 중.. ({i+1}/{len(tickers)}) - {stock_name}")
        progress_bar.progress((i + 1) / len(tickers))
        
        success = False
        max_retries = 3 
        
        for attempt in range(max_retries):
            try:
                df = fdr.DataReader(ticker, str(fetch_year))['Close']
                df.name = ticker
                # 인덱스(날짜) 중복 제거
                df = df[~df.index.duplicated(keep='first')]
                all_prices.append(df)
                success = True
                time.sleep(0.05) 
                break 
            except Exception:
                time.sleep(0.5)
                continue
        
        if not success:
            failed_stocks.append(f"{stock_name} ({ticker})")
            
    status_text.empty()
    progress_bar.empty()
    
    if failed_stocks:
        st.warning(f"⚠️ 일부 종목 데이터를 가져오지 못했습니다: {len(failed_stocks)}건")
    
    if not all_prices:
        return pd.DataFrame(), {}

    # [수정 포인트] concat 후 fillna 방식 변경 (Pandas 2.0+ 대응)
    price_df = pd.concat(all_prices, axis=1)
    price_df = price_df.ffill().bfill() # 앞뒤 결측치 채우기
    
    return price_df, code_map

# -----------------------------------------------------------------------------
# 2. 사이드바 UI
# -----------------------------------------------------------------------------
st.title("🇰🇷 KOSPI 200 상대 모멘텀 전략")
st.markdown("KOSPI 200 우량주 중 **최근 수익률이 좋은 종목**으로 포트폴리오를 자동으로 교체하는 전략입니다.")

with st.sidebar:
    st.header("⚙️ 전략 설정")
    start_year = st.number_input("시작 연도", value=2020, min_value=2000, max_value=2025)
    sample_size = st.slider("투자 유니버스 (KOSPI 200 상위 N개)", 20, 200, 100, step=10)
    top_n = st.number_input("보유 종목 수 (Top N)", value=20, min_value=1)
    
    rebalance_map = {"1개월 (월간)": 1, "3개월 (분기)": 3, "6개월 (반기)": 6, "12개월 (연간)": 12}
    rebal_label = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()), index=0)
    rebalance_step = rebalance_map[rebal_label]
    
    momentum_window = st.number_input("모멘텀 기간 (개월)", value=12)

    st.markdown("---")
    export_excel_option = st.checkbox("📥 엑셀 다운로드 기능 활성화", value=True)
    run_btn = st.button("🚀 전략 실행", type="primary")

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
if run_btn:
    with st.spinner("데이터 분석 중..."):
        df_price, code_map = get_kospi200_data(start_year, sample_size)
        
        if df_price.empty:
            st.error("데이터를 가져오지 못했습니다.")
            st.stop()

        start_dt = pd.to_datetime(f'{start_year}-01-01')
        if start_dt < df_price.index[0]: start_dt = df_price.index[0]
        end_dt = df_price.index[-1]
        
        all_days = df_price.index
        target_months = list(range(1, 13, rebalance_step))
        rebalance_dates = []
        
        for year in range(start_dt.year, end_dt.year + 1):
            for month in target_months:
                month_data = all_days[(all_days.year == year) & (all_days.month == month)]
                if not month_data.empty:
                    rebalance_dates.append(month_data[-1])
        
        rebalance_dates = sorted(list(set([d for d in rebalance_dates if start_dt <= d <= end_dt])))
        
        portfolio_returns = []
        history_records = []
        
        for i in range(len(rebalance_dates) - 1):
            curr_date = rebalance_dates[i]
            next_date = rebalance_dates[i+1]
            past_date_target = curr_date - pd.DateOffset(months=momentum_window)
            
            try:
                idx_loc = df_price.index.get_indexer([past_date_target], method='nearest')[0]
                past_date_real = df_price.index[idx_loc]
                
                price_curr = df_price.loc[curr_date]
                price_past = df_price.loc[past_date_real]
                
                # 수익률 계산 및 무한대/결측치 처리
                mom_score = (price_curr - price_past) / price_past
                mom_score = mom_score.replace([np.inf, -np.inf], np.nan).dropna()
                
                top_series = mom_score.nlargest(top_n)
                top_stocks = top_series.index.tolist()
                
                for stock in top_stocks:
                    history_records.append({
                        'Date': curr_date.strftime('%Y-%m-%d'),
                        'Code': stock,
                        'Name': code_map.get(stock, stock),
                        'Momentum': top_series[stock]
                    })
                
                price_period = df_price[top_stocks].loc[curr_date:next_date]
                if not price_period.empty:
                    # 기간 내 종목별 평균 수익률
                    period_ret = price_period.pct_change().fillna(0).mean(axis=1)
                    portfolio_returns.append(period_ret)
                    
            except Exception:
                continue
                
        if portfolio_returns:
            full_returns = pd.concat(portfolio_returns)
            full_returns = full_returns[~full_returns.index.duplicated(keep='first')]
            
            cum_returns = (1 + full_returns).cumprod()
            running_max = cum_returns.cummax()
            drawdown = (cum_returns / running_max) - 1
            mdd = drawdown.min()
            
            total_days = (cum_returns.index[-1] - cum_returns.index[0]).days
            cagr = cum_returns.iloc[-1]**(365/total_days) - 1 if total_days > 0 else 0
            
            # 벤치마크 (KOSPI 200)
            try:
                kospi_bm = fdr.DataReader('KS200', start=full_returns.index[0], end=full_returns.index[-1])['Close']
                bm_ret = kospi_bm.pct_change().fillna(0)
                bm_cum = (1 + bm_ret).cumprod()
                bm_cum = bm_cum / bm_cum.iloc[0]
            except:
                bm_cum = None

            # 결과 리포트 UI
            col1, col2, col3 = st.columns(3)
            col1.metric("총 수익률", f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
            col2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
            col3.metric("최대 낙폭 (MDD)", f"{mdd*100:.2f}%", delta_color="inverse")
            
            tab1, tab2, tab3, tab4 = st.tabs(["📊 차트", "🏆 현재 추천 종목", "📅 월별 수익률", "📝 매매 기록"])
            
            with tab1:
                fig, ax = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [2, 1]})
                ax[0].plot(cum_returns.index, cum_returns, label='Strategy', color='red')
                if bm_cum is not None:
                    ax[0].plot(bm_cum.index, bm_cum, label='KOSPI 200', color='gray', linestyle='--', alpha=0.7)
                ax[0].set_title("Growth Comparison")
                ax[0].legend()
                ax[0].grid(alpha=0.3)
                
                ax[1].fill_between(drawdown.index, drawdown*100, 0, color='blue', alpha=0.2)
                ax[1].set_title("Drawdown (%)")
                ax[1].grid(alpha=0.3)
                st.pyplot(fig)
                
            with tab2:
                latest_date = df_price.index[-1]
                p_curr = df_price.loc[latest_date]
                p_past = df_price.loc[df_price.index[df_price.index.get_indexer([latest_date - pd.DateOffset(months=momentum_window)], method='nearest')[0]]]
                curr_mom = (p_curr - p_past) / p_past
                curr_top = curr_mom.nlargest(top_n)
                
                picks_data = [{'종목명': code_map.get(c, c), '코드': c, '수익률': f"{s*100:.2f}%", '현재가': f"{p_curr[c]:,.0f}원"} for c, s in curr_top.items()]
                st.table(pd.DataFrame(picks_data))

            with tab3:
                monthly_ret = full_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
                monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
                st.dataframe(monthly_table.style.background_gradient(cmap='RdYlGn').format("{:.2%}"))

            with tab4:
                st.dataframe(pd.DataFrame(history_records))

            if export_excel_option:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    pd.DataFrame(history_records).to_excel(writer, sheet_name='History')
                st.download_button("💾 엑셀 다운로드", buffer.getvalue(), "strategy_result.xlsx")
        else:
            st.warning("분석 기간 내 수익률 데이터가 부족합니다.")