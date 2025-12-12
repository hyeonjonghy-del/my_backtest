import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import FinanceDataReader as fdr
import warnings

# 경고 메시지 무시
warnings.filterwarnings('ignore')

# -----------------------------------------------------------------------------
# 1. 페이지 설정
# -----------------------------------------------------------------------------
st.set_page_config(page_title="통합 모멘텀 전략 (Final)", page_icon="📈", layout="wide")

# -----------------------------------------------------------------------------
# 2. 데이터 수집 함수 (한국 시장은 원본 방식 그대로 복귀)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600*24)
def get_stock_list_safe(market_name, sample_size):
    """
    시장별로 가장 확실한 방법으로 종목 리스트를 가져옵니다.
    한국 시장은 fdr.StockListing('KOSPI') 등 전용 함수를 사용합니다.
    """
    try:
        # 1. 시장별 리스트 가져오기
        if market_name == "KOSPI 200":
            # [복구] 원본 파일과 동일한 방식
            df_list = fdr.StockListing('KOSPI')
        elif market_name == "KOSDAQ 150":
            # [복구] 원본 파일과 동일한 방식
            df_list = fdr.StockListing('KOSDAQ')
        elif market_name == "S&P 500":
            df_list = fdr.StockListing('S&P500')
        elif market_name == "NASDAQ 100":
            df_list = fdr.StockListing('NASDAQ')
            
        # 2. 컬럼명 통일 (Code/Symbol)
        if 'Code' not in df_list.columns and 'Symbol' in df_list.columns:
            df_list['Code'] = df_list['Symbol']
            
        # 3. 시가총액(Marcap) 정렬 - 데이터가 있는 경우만
        if 'Marcap' in df_list.columns:
            # 안전장치: 문자열로 된 숫자가 있을 경우 변환 (미국 주식 등 대비)
            if df_list['Marcap'].dtype == 'object':
                df_list['Marcap'] = df_list['Marcap'].astype(str).str.replace(',', '').replace('nan', '0')
                df_list['Marcap'] = pd.to_numeric(df_list['Marcap'], errors='coerce')
            
            df_list = df_list.sort_values(by='Marcap', ascending=False)
        
        # 4. 상위 N개 종목 코드 추출
        # 데이터 공백을 대비해 요청 수량보다 조금 더 가져옴 (1.2배)
        target_df = df_list.head(int(sample_size * 1.2))
        tickers = target_df['Code'].tolist()
        
        # 종목명 매핑
        if 'Name' in target_df.columns:
            code_map = target_df.set_index('Code')['Name'].to_dict()
        else:
            code_map = {t: t for t in tickers}
            
        return tickers, code_map, None

    except Exception as e:
        return [], {}, f"리스트 로딩 실패: {str(e)}"

@st.cache_data(ttl=3600*24)
def get_price_data(tickers, code_map, start_year, target_n):
    """
    주가 데이터를 다운로드합니다.
    """
    all_prices = []
    # 모멘텀 계산을 위해 시작년도 2년 전부터 데이터 요청
    fetch_year = start_year - 2
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    valid_count = 0
    
    for i, ticker in enumerate(tickers):
        # 진행상황 UI 업데이트 (속도 저하 방지를 위해 10번마다)
        if i % 10 == 0:
            name = code_map.get(ticker, ticker)
            status_text.text(f"데이터 수신 중.. ({valid_count}/{target_n}) - {name}")
            progress_bar.progress((i + 1) / len(tickers))
        
        try:
            # 데이터 요청
            df = fdr.DataReader(ticker, str(fetch_year))['Close']
            
            # 유효성 검사: 데이터가 너무 짧거나, 값이 변하지 않는(거래정지) 경우 제외
            if len(df) < 200 or df.nunique() <= 1:
                continue
                
            df.name = ticker
            all_prices.append(df)
            valid_count += 1
            
            # 사용자가 원하는 개수만큼 모이면 중단 (속도 최적화)
            if valid_count >= target_n:
                break
        except:
            continue
            
    status_text.empty()
    progress_bar.empty()
    
    if not all_prices:
        return pd.DataFrame(), "수집된 주가 데이터가 없습니다."
        
    # 데이터 병합 (ffill로 결측치 보완)
    price_df = pd.concat(all_prices, axis=1).fillna(method='ffill')
    return price_df, None

# -----------------------------------------------------------------------------
# 3. 사이드바 UI
# -----------------------------------------------------------------------------
st.title("🌏 글로벌 상대 모멘텀 전략")
st.markdown("한국(KOSPI, KOSDAQ) 및 미국(S&P, NASDAQ) 시장 지원")

with st.sidebar:
    st.header("⚙️ 전략 설정")
    
    # 시장 선택
    market_options = ["KOSPI 200", "KOSDAQ 150", "S&P 500", "NASDAQ 100"]
    target_market = st.selectbox("투자 시장", market_options)
    
    # 벤치마크 매핑
    bm_map = {
        "KOSPI 200": "KS200", "KOSDAQ 150": "KQ150", 
        "S&P 500": "US500", "NASDAQ 100": "IXIC"
    }
    
    start_year = st.number_input("시작 연도", value=2015, min_value=2000, max_value=2024)
    sample_size = st.slider("투자 유니버스 (상위 N개)", 50, 300, 150, step=50)
    top_n = st.number_input("보유 종목 수", value=10, min_value=1)
    
    rebal_term = st.selectbox("리밸런싱 주기", ["1개월", "3개월 (분기)", "6개월 (반기)", "12개월 (연간)"], index=1)
    rebal_month_map = {"1개월": 1, "3개월 (분기)": 3, "6개월 (반기)": 6, "12개월 (연간)": 12}
    rebalance_step = rebal_month_map[rebal_term]
    
    momentum_window = st.number_input("모멘텀 기간 (개월)", value=12)

    st.markdown("---")
    export_excel = st.checkbox("📥 엑셀 다운로드", value=True)
    
    run_btn = st.button("🚀 전략 실행", type="primary")

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if run_btn:
    with st.spinner(f"[{target_market}] 데이터를 불러오는 중입니다..."):
        
        # 1. 종목 리스트 확보
        raw_tickers, code_map, err = get_stock_list_safe(target_market, sample_size)
        if err:
            st.error(err)
            st.stop()
            
        # 2. 주가 데이터 확보
        df_price, err = get_price_data(raw_tickers, code_map, start_year, sample_size)
        if err:
            st.error(err)
            st.stop()

        # 3. 백테스트 수행
        start_dt = pd.to_datetime(f'{start_year}-01-01')
        if start_dt < df_price.index[0]: start_dt = df_price.index[0]
        end_dt = df_price.index[-1]
        
        # 리밸런싱 날짜 생성
        all_days = df_price.index
        target_months = list(range(1, 13, rebalance_step))
        rebalance_dates = []
        
        for year in range(start_dt.year, end_dt.year + 1):
            for month in target_months:
                month_data = all_days[(all_days.year == year) & (all_days.month == month)]
                if not month_data.empty:
                    rebalance_dates.append(month_data[-1])
        
        rebalance_dates = sorted(list(set([d for d in rebalance_dates if start_dt <= d <= end_dt])))
        
        if len(rebalance_dates) < 2:
            st.warning("데이터 기간이 짧아 전략을 실행할 수 없습니다. (시작 연도를 조정하세요)")
            st.stop()

        portfolio_returns = []
        history_records = []
        
        for i in range(len(rebalance_dates) - 1):
            curr_date = rebalance_dates[i]
            next_date = rebalance_dates[i+1]
            past_date_target = curr_date - pd.DateOffset(months=momentum_window)
            
            try:
                # 과거 기준일 찾기
                idx_loc = df_price.index.searchsorted(past_date_target)
                if idx_loc >= len(df_price): continue
                past_date_real = df_price.index[idx_loc]
                
                # asof를 사용하여 주말/휴일 문제 없이 데이터 조회
                p_curr = df_price.asof(curr_date)
                p_past = df_price.asof(past_date_real)
                
                # 모멘텀 계산
                mom = (p_curr - p_past) / p_past
                mom = mom.replace([np.inf, -np.inf], np.nan).dropna()
                
                if mom.empty: continue

                # 상위 N개 선정
                top_series = mom.nlargest(top_n)
                top_stocks = top_series.index.tolist()
                
                # 기록
                for s in top_stocks:
                    history_records.append({
                        'Date': curr_date.strftime('%Y-%m-%d'),
                        'Code': s,
                        'Name': code_map.get(s, s),
                        'Momentum': top_series[s]
                    })
                
                # 수익률 계산 (다음 리밸런싱 날짜까지)
                period_price = df_price[top_stocks].loc[curr_date:next_date]
                if not period_price.empty:
                    period_ret = period_price.pct_change().fillna(0).mean(axis=1)
                    portfolio_returns.append(period_ret)
                    
            except: continue
        
        # 4. 결과 출력
        if portfolio_returns:
            full_ret = pd.concat(portfolio_returns)
            full_ret = full_ret[~full_ret.index.duplicated(keep='first')]
            
            cum_ret = (1 + full_ret).cumprod()
            dd = (cum_ret / cum_ret.cummax()) - 1
            mdd = dd.min()
            
            days = (cum_ret.index[-1] - cum_ret.index[0]).days
            cagr = cum_ret.iloc[-1]**(365/days) - 1 if days > 0 else 0
            
            # 벤치마크 로드
            try:
                bm_ticker = bm_map[target_market]
                bm_df = fdr.DataReader(bm_ticker, start=full_ret.index[0], end=full_ret.index[-1])['Close']
                bm_ret = bm_df.pct_change().fillna(0)
                common_idx = full_ret.index.intersection(bm_ret.index)
                bm_cum = (1 + bm_ret.loc[common_idx]).cumprod()
                if not bm_cum.empty: bm_cum = bm_cum / bm_cum.iloc[0]
            except: bm_cum = None
            
            # 현재 추천 종목
            last_date = df_price.index[-1]
            last_past = df_price.index[min(df_price.index.searchsorted(last_date - pd.DateOffset(months=momentum_window)), len(df_price)-1)]
            curr_mom = ((df_price.iloc[-1] - df_price.loc[last_past]) / df_price.loc[last_past]).dropna()
            curr_top = curr_mom.nlargest(top_n)
            
            curr_data = []
            is_usd = "USD" in ("USD" if target_market in ["S&P 500", "NASDAQ 100"] else "KRW")
            for c, v in curr_top.items():
                curr_data.append({
                    'Code': c, 
                    'Name': code_map.get(c, c), 
                    'Return_1Y': v, 
                    'Price': df_price.iloc[-1][c]
                })
            df_picks = pd.DataFrame(curr_data)
            df_hist = pd.DataFrame(history_records)
            
            # 월별 수익률 표
            m_ret = full_ret.resample('M').apply(lambda x: (1 + x).prod() - 1)
            m_table = m_ret.groupby([m_ret.index.year, m_ret.index.month]).sum().unstack()
            m_table.columns = [f"{x}월" for x in m_table.columns]

            # --- 화면 표시 ---
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Return", f"{(cum_ret.iloc[-1]-1)*100:.2f}%")
            c2.metric("CAGR", f"{cagr*100:.2f}%")
            c3.metric("MDD", f"{mdd*100:.2f}%", delta_color="inverse")
            
            t1, t2, t3, t4 = st.tabs(["Chart", "Current Picks", "Monthly", "History"])
            
            with t1:
                fig, ax = plt.subplots(2, 1, figsize=(10, 10), gridspec_kw={'height_ratios':[2,1]})
                ax[0].plot(cum_ret.index, cum_ret, label='Strategy', color='red')
                if bm_cum is not None: ax[0].plot(bm_cum.index, bm_cum, label='Benchmark', color='gray', linestyle='--')
                ax[0].set_yscale('log'); ax[0].legend(); ax[0].grid(alpha=0.3)
                ax[1].plot(dd.index, dd*100, color='blue'); ax[1].fill_between(dd.index, dd*100, 0, color='blue', alpha=0.3)
                ax[1].grid(alpha=0.3); ax[1].set_title('Drawdown (%)')
                st.pyplot(fig)
                
            with t2:
                st.subheader(f"Current Top {top_n} ({last_date.strftime('%Y-%m-%d')})")
                disp = df_picks.copy()
                disp['Return_1Y'] = disp['Return_1Y'].apply(lambda x: f"{x*100:.2f}%")
                disp['Price'] = disp['Price'].apply(lambda x: f"{x:,.2f}" if is_usd else f"{x:,.0f}")
                st.table(disp)
                
            with t3:
                st.dataframe(m_table.style.background_gradient(cmap='RdYlGn', axis=None).format("{:.2%}"))
            
            with t4:
                st.dataframe(df_hist)
                
            # 엑셀 다운로드
            if export_excel:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as w:
                    df_hist.to_excel(w, sheet_name='History', index=False)
                    m_table.to_excel(w, sheet_name='Monthly')
                    df_picks.to_excel(w, sheet_name='Picks', index=False)
                st.download_button("Download Excel", buf.getvalue(), "Momentum_Result.xlsx", "application/vnd.ms-excel")
                
        else:
            st.error("결과를 계산할 수 없습니다. (모멘텀 기간을 줄이거나, 투자 유니버스를 늘려보세요)")