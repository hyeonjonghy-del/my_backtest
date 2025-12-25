import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import datetime
import io
import FinanceDataReader as fdr
import requests

# -----------------------------------------------------------------------------
# 1. 페이지 설정 및 캐싱 함수
# -----------------------------------------------------------------------------
st.set_page_config(page_title="미국 주식 모멘텀 전략", page_icon="🇺🇸")

@st.cache_data(ttl=3600*24)
def get_nasdaq100_list():
    """
    위키피디아에서 NASDAQ-100 리스트를 가져옵니다.
    봇 차단을 피하기 위해 User-Agent 헤더를 사용합니다.
    """
    url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
    
    # 봇 차단 방지용 헤더 (브라우저인 척 속임)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers)
        # 'Ticker'라는 단어가 포함된 테이블을 찾습니다.
        dfs = pd.read_html(response.text, match='Ticker')
        
        if dfs:
            df = dfs[0]
            # 컬럼 이름 통일 (Ticker -> Code, Company -> Name)
            df = df.rename(columns={'Ticker': 'Code', 'Company': 'Name'})
            return df[['Code', 'Name']]
    except Exception as e:
        st.error(f"NASDAQ-100 리스트 가져오기 실패: {e}")
        return pd.DataFrame()
        
    return pd.DataFrame()

@st.cache_data(ttl=3600*24) 
def get_stock_data(market_type, start_year, sample_size):
    """선택한 시장의 종목 데이터를 다운로드합니다."""
    
    # 1. 종목 리스트 가져오기
    try:
        if market_type == "NASDAQ 100":
            df_list = get_nasdaq100_list()
            if df_list.empty:
                st.warning("위키피디아 접속 실패. 백업 데이터를 사용합니다.")
                # 만약 크롤링이 실패하면 주요 기술주 20개로 대체 (비상용)
                fallback_data = {
                    'Code': ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'PEP', 'COST', 
                             'CSCO', 'TMUS', 'ADBE', 'TXN', 'NFLX', 'AMD', 'QCOM', 'INTC', 'HON', 'AMGN'],
                    'Name': ['Apple', 'Microsoft', 'Nvidia', 'Amazon', 'Alphabet', 'Meta', 'Tesla', 'Broadcom', 'PepsiCo', 'Costco',
                             'Cisco', 'T-Mobile', 'Adobe', 'Texas Instruments', 'Netflix', 'AMD', 'Qualcomm', 'Intel', 'Honeywell', 'Amgen']
                }
                df_list = pd.DataFrame(fallback_data)
        else:
            # S&P 500은 라이브러리 내장 기능 사용
            df_list = fdr.StockListing('S&P500')
            
    except Exception as e:
        st.error(f"종목 리스트 오류: {e}")
        return pd.DataFrame(), {}
        
    # 컬럼명 통일 및 전처리
    mapper = {'Symbol': 'Code', 'Security': 'Name', 'Ticker': 'Code', 'Company': 'Name'}
    df_list = df_list.rename(columns=mapper)
    
    if 'Code' not in df_list.columns:
        df_list['Name'] = df_list['Code'] if 'Code' in df_list.columns else ''
        
    # 상위 N개 선정
    target_df = df_list.head(sample_size)
    tickers = target_df['Code'].tolist()
    code_map = target_df.set_index('Code')['Name'].to_dict()
    
    # 2. 주가 데이터 다운로드
    all_prices = []
    fetch_year = start_year - 2 # 모멘텀 계산을 위해 2년 전 데이터부터 확보
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_tickers = len(tickers)
    
    for i, ticker in enumerate(tickers):
        try:
            status_text.text(f"[{market_type}] 데이터 수집 중.. ({i+1}/{total_tickers}) - {code_map.get(ticker, ticker)}")
            progress_bar.progress((i + 1) / total_tickers)
            
            # 데이터 다운로드
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
st.title("🇺🇸 미국 주식 상대 모멘텀 전략")
st.markdown("S&P 500 또는 NASDAQ 100 종목 중 **최근 수익률이 좋은 종목**으로 교체하는 전략입니다.")

with st.sidebar:
    st.header("⚙️ 전략 설정")
    
    market_option = st.radio("투자 대상 (Market)", ["S&P 500", "NASDAQ 100"])
    
    start_year = st.number_input("시작 연도", value=2015, min_value=2000, max_value=2025)
    
    # 슬라이더 최대값 설정
    max_stocks = 505 if market_option == "S&P 500" else 105
    default_stocks = 100
        
    sample_size = st.slider("투자 유니버스 (종목 수)", 10, max_stocks, default_stocks, step=10)
    
    top_n = st.number_input("보유 종목 수 (Top N)", value=10, min_value=1)
    
    rebalance_map = {"1개월": 1, "3개월 (분기)": 3, "6개월": 6, "12개월": 12}
    rebal_label = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()), index=1)
    rebalance_step = rebalance_map[rebal_label]
    
    momentum_window = st.number_input("모멘텀 기간 (개월)", value=12)

    st.markdown("---")
    export_excel_option = st.checkbox("📥 엑셀 다운로드 기능 활성화", value=True)
    run_btn = st.button("🚀 전략 실행", type="primary")

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
if run_btn:
    with st.spinner(f"[{market_option}] 데이터를 분석하고 있습니다..."):
        # 1. 데이터 로드
        df_price, code_map = get_stock_data(market_option, start_year, sample_size)
        
        if df_price.empty:
            st.error("데이터를 가져오지 못했습니다. 잠시 후 다시 시도하거나 종목 수를 줄여보세요.")
            st.stop()

        # 2. 백테스트 시뮬레이션
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
        
        portfolio_returns = []
        history_records = []
        
        for i in range(len(rebalance_dates) - 1):
            curr_date = rebalance_dates[i]
            next_date = rebalance_dates[i+1]
            past_date_target = curr_date - pd.DateOffset(months=momentum_window)
            
            try:
                idx_loc = df_price.index.get_indexer([past_date_target], method='nearest')[0]
                past_date_real = df_price.index[idx_loc]
                
                price_curr = df_price.loc[:curr_date].iloc[-1]
                price_past = df_price.loc[past_date_real]
                
                mom_score = (price_curr - price_past) / price_past
                top_stocks = mom_score.nlargest(top_n).index.tolist()
                
                # 기록
                for stock in top_stocks:
                    history_records.append({
                        'Date': curr_date.strftime('%Y-%m-%d'),
                        'Code': stock,
                        'Name': code_map.get(stock, stock),
                        'Momentum': mom_score[stock]
                    })
                
                price_period = df_price[top_stocks].loc[curr_date:next_date]
                if not price_period.empty:
                    period_ret = price_period.pct_change().fillna(0).mean(axis=1)
                    portfolio_returns.append(period_ret)
            except:
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
            
            # 벤치마크 (QQQ 또는 SPY)
            try:
                bm_ticker = 'QQQ' if market_option == "NASDAQ 100" else 'US500'
                bm_label = 'NASDAQ 100 (QQQ)' if market_option == "NASDAQ 100" else 'S&P 500'
                bm_data = fdr.DataReader(bm_ticker, start=full_returns.index[0], end=full_returns.index[-1])['Close']
                bm_cum = (1 + bm_data.pct_change().fillna(0)).cumprod()
                bm_cum = bm_cum / bm_cum.iloc[0]
            except:
                bm_cum = None
                bm_label = 'Benchmark'

            # 결과 출력
            col1, col2, col3 = st.columns(3)
            col1.metric("총 수익률", f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
            col2.metric("CAGR", f"{cagr*100:.2f}%")
            col3.metric("MDD", f"{mdd*100:.2f}%", delta_color="inverse")
            
            tab1, tab2, tab3 = st.tabs(["📊 차트", "🏆 추천 종목", "📝 매매 기록"])
            
            with tab1:
                fig, ax = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [2, 1]})
                ax[0].plot(cum_returns.index, cum_returns, label=f'{market_option} Strategy')
                if bm_cum is not None:
                    ax[0].plot(bm_cum.index, bm_cum, label=bm_label, color='gray', linestyle='--', alpha=0.5)
                ax[0].set_yscale('log')
                ax[0].legend()
                ax[0].grid(alpha=0.3)
                ax[1].fill_between(drawdown.index, drawdown*100, 0, color='red', alpha=0.2)
                ax[1].set_title("Drawdown")
                ax[1].grid(alpha=0.3)
                st.pyplot(fig)
                
            with tab2:
                # 현재 추천 종목
                latest = df_price.iloc[-1]
                past = df_price.loc[df_price.index[df_price.index.get_indexer([df_price.index[-1] - pd.DateOffset(months=momentum_window)], method='nearest')[0]]]
                curr_mom = (latest - past) / past
                top_curr = curr_mom.nlargest(top_n)
                
                recs = []
                for c, s in top_curr.items():
                    recs.append({'종목': code_map.get(c, c), '코드': c, '수익률': f"{s*100:.2f}%", '현재가': f"${latest[c]:.2f}"})
                st.table(pd.DataFrame(recs))
                
            with tab3:
                st.dataframe(pd.DataFrame(history_records))
            
            # 엑셀 다운로드 (간소화)
            if export_excel_option:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    pd.DataFrame(history_records).to_excel(writer, sheet_name='History', index=False)
                    pd.DataFrame(recs).to_excel(writer, sheet_name='Current_Picks', index=False)
                st.download_button("📥 엑셀 다운로드", buffer, f"{market_option}_backtest.xlsx")