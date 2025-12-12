import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import FinanceDataReader as fdr
import warnings

warnings.filterwarnings('ignore')

# -----------------------------------------------------------------------------
# 1. 페이지 설정 및 시장 설정값
# -----------------------------------------------------------------------------
st.set_page_config(page_title="멀티 마켓 모멘텀 전략", page_icon="📈", layout="wide")

# 시장별 설정 매핑
MARKET_CONFIG = {
    "KOSPI 200":  {"listing": "KOSPI",  "benchmark": "KS200", "currency": "KRW"},
    "KOSDAQ 150": {"listing": "KOSDAQ", "benchmark": "KQ150", "currency": "KRW"},
    "NASDAQ 100": {"listing": "NASDAQ", "benchmark": "IXIC",  "currency": "USD"},
    "S&P 500":    {"listing": "S&P500", "benchmark": "US500", "currency": "USD"}
}

@st.cache_data(ttl=3600*24) # 24시간 캐싱
def get_market_data(market_name, start_year, sample_size):
    """
    선택한 시장의 시가총액 상위 종목 데이터를 다운로드합니다.
    """
    config = MARKET_CONFIG[market_name]
    listing_code = config['listing']
    
    # 1. 상장 종목 리스트 가져오기
    try:
        df_list = fdr.StockListing(listing_code)
        
        # 컬럼명 통일 (미국 주식은 Symbol, 한국은 Code)
        if 'Code' not in df_list.columns and 'Symbol' in df_list.columns:
            df_list['Code'] = df_list['Symbol']
            
        # 시가총액 순 정렬 (데이터가 있는 경우)
        if 'Marcap' in df_list.columns:
            df_list = df_list.sort_values(by='Marcap', ascending=False)
            
        # 상위 N개 선정
        target_df = df_list.head(sample_size)
        tickers = target_df['Code'].tolist()
        
        # 이름 매핑 (Name 컬럼이 없으면 티커를 이름으로 사용)
        if 'Name' in target_df.columns:
            code_map = target_df.set_index('Code')['Name'].to_dict()
        else:
            code_map = {t: t for t in tickers}
            
    except Exception as e:
        return pd.DataFrame(), {}, f"리스트 다운로드 실패: {str(e)}"
    
    # 2. 주가 데이터 다운로드
    all_prices = []
    fetch_year = start_year - 2
    
    # Streamlit 진행률 표시바
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, ticker in enumerate(tickers):
        try:
            # 진행상황 업데이트 (너무 빈번한 업데이트 방지)
            if i % 5 == 0 or i == len(tickers) - 1:
                status_text.text(f"[{market_name}] 데이터 다운로드 중.. ({i+1}/{len(tickers)}) - {code_map.get(ticker, ticker)}")
                progress_bar.progress((i + 1) / len(tickers))
            
            # 데이터 다운로드
            df = fdr.DataReader(ticker, str(fetch_year))['Close']
            df.name = ticker
            all_prices.append(df)
        except:
            continue
            
    status_text.empty()
    progress_bar.empty()
    
    if not all_prices:
        return pd.DataFrame(), {}, "가격 데이터를 가져올 수 없습니다."

    price_df = pd.concat(all_prices, axis=1).fillna(method='ffill')
    return price_df, code_map, None

# -----------------------------------------------------------------------------
# 2. 사이드바 UI
# -----------------------------------------------------------------------------
st.title("🌏 글로벌 상대 모멘텀 전략")
st.markdown("선택한 시장의 대형주 중 **모멘텀(추세)이 강한 종목**으로 포트폴리오를 운용하는 전략입니다.")

with st.sidebar:
    st.header("⚙️ 전략 설정")
    
    # [NEW] 시장 선택 옵션
    target_market = st.selectbox("투자 시장 선택", list(MARKET_CONFIG.keys()))
    
    start_year = st.number_input("시작 연도", value=2015, min_value=2000, max_value=2024)
    
    sample_size = st.slider("투자 유니버스 (시총 상위 N개)", 50, 300, 100, step=50,
                            help="미국 시장은 데이터가 많아 다운로드에 시간이 더 걸릴 수 있습니다.")
    
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
    with st.spinner(f"[{target_market}] 데이터를 분석하고 있습니다... (미국 시장은 조금 더 걸릴 수 있습니다)"):
        
        # 1. 데이터 로드 (시장 선택 적용)
        df_price, code_map, err_msg = get_market_data(target_market, start_year, sample_size)
        
        if df_price.empty:
            st.error(f"오류 발생: {err_msg}")
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
                # 과거 시점 찾기 (가장 가까운 날짜)
                idx_loc = df_price.index.searchsorted(past_date_target)
                if idx_loc >= len(df_price): continue
                past_date_real = df_price.index[idx_loc]
                
                # asof를 사용하여 해당 날짜 혹은 직전 날짜 데이터 안전하게 가져오기
                price_curr = df_price.asof(curr_date)
                price_past = df_price.asof(past_date_real)
                
                # 모멘텀 스코어 계산
                mom_score = (price_curr - price_past) / price_past
                mom_score = mom_score.replace([np.inf, -np.inf], np.nan).dropna()
                
                if mom_score.empty: continue

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
            
            # 벤치마크 데이터 로드 (동적 적용)
            bm_ticker = MARKET_CONFIG[target_market]['benchmark']
            try:
                bm_data = fdr.DataReader(bm_ticker, start=full_returns.index[0], end=full_returns.index[-1])['Close']
                bm_ret = bm_data.pct_change().fillna(0)
                # 인덱스 교집합만 사용하여 비교
                common_idx = full_returns.index.intersection(bm_ret.index)
                bm_cum = (1 + bm_ret.loc[common_idx]).cumprod()
                # 시작점을 1로 맞춤 (비교 용이성)
                if not bm_cum.empty:
                    bm_cum = bm_cum / bm_cum.iloc[0]
            except:
                bm_cum = None

            # ----------------------------------
            # 데이터 준비 (엑셀 및 탭 표시용)
            # ----------------------------------
            monthly_ret = full_returns.resample('M').apply(lambda x: (1 + x).prod() - 1)
            monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
            monthly_table.columns = [f"{c}월" for c in monthly_table.columns]
            
            # 현재 추천 종목
            latest_date = df_price.index[-1]
            past_target = latest_date - pd.DateOffset(months=momentum_window)
            
            idx_loc = df_price.index.searchsorted(past_target)
            past_real = df_price.index[min(idx_loc, len(df_price)-1)]
            
            p_curr = df_price.iloc[-1]
            p_past = df_price.loc[past_real]
            
            curr_mom = (p_curr - p_past) / p_past
            curr_mom = curr_mom.replace([np.inf, -np.inf], np.nan).dropna()
            curr_top = curr_mom.nlargest(top_n)
            
            picks_data = []
            currency_symbol = "USD" if "USD" in MARKET_CONFIG[target_market]['currency'] else "KRW"
            
            for code, score in curr_top.items():
                picks_data.append({
                    '종목명': code_map.get(code, code),
                    '종목코드': code,
                    '1년 수익률': score,
                    '현재가': p_curr[code]
                })
            df_picks = pd.DataFrame(picks_data)
            df_history = pd.DataFrame(history_records)

            # ----------------------------------
            # 결과 화면 출력
            # ----------------------------------
            col1, col2, col3 = st.columns(3)
            col1.metric("총 수익률", f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
            col2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
            col3.metric("최대 낙폭 (MDD)", f"{mdd*100:.2f}%", delta_color="inverse")
            
            tab1, tab2, tab3, tab4 = st.tabs(["📊 성과 차트", "🏆 현재 추천 종목", "📅 월별 수익률", "📝 매매 기록"])
            
            with tab1:
                fig, ax = plt.subplots(2, 1, figsize=(10, 10), gridspec_kw={'height_ratios': [2, 1]})
                
                # 수익률 곡선
                ax[0].plot(cum_returns.index, cum_returns, label=f'Momentum ({target_market})', color='#FF4B4B')
                if bm_cum is not None:
                    ax[0].plot(bm_cum.index, bm_cum, label=f'Benchmark ({bm_ticker})', color='gray', linestyle='--', alpha=0.6)
                
                ax[0].set_title(f"{target_market} Strategy vs Benchmark")
                ax[0].set_yscale('log')
                ax[0].legend()
                ax[0].grid(True, alpha=0.3)
                
                # 낙폭 곡선
                ax[1].fill_between(drawdown.index, drawdown*100, 0, color='#1F77B4', alpha=0.3)
                ax[1].plot(drawdown.index, drawdown*100, color='#1F77B4', linewidth=1)
                ax[1].set_title("Drawdown Risk (%)")
                ax[1].grid(True, alpha=0.3)
                
                st.pyplot(fig)
                
            with tab2:
                st.subheader(f"📢 [{target_market}] 오늘 기준 Top {top_n}")
                st.info(f"기준일: {latest_date.strftime('%Y-%m-%d')}")
                
                # 화면 표시용 포맷팅
                df_picks_display = df_picks.copy()
                df_picks_display['1년 수익률'] = df_picks_display['1년 수익률'].apply(lambda x: f"{x*100:.2f}%")
                df_picks_display['현재가'] = df_picks_display['현재가'].apply(lambda x: f"{x:,.2f}" if currency_symbol == "USD" else f"{x:,.0f}")
                st.table(df_picks_display)
                
            with tab3:
                st.subheader("📅 월별 수익률 Heatmap")
                st.dataframe(monthly_table.style.background_gradient(cmap='RdYlGn', axis=None).format("{:.2%}"))
                
            with tab4:
                st.subheader("📝 과거 리밸런싱 내역")
                st.dataframe(df_history)

            # ----------------------------------
            # 엑셀 다운로드 로직
            # ----------------------------------
            if export_excel_option:
                st.markdown("---")
                st.subheader("💾 분석 결과 저장")
                
                with st.spinner("엑셀 파일을 생성 중입니다..."):
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        # 1. 매매 이력
                        df_history.to_excel(writer, sheet_name='Trade_History', index=False)
                        # 2. 월별 성과
                        monthly_table.to_excel(writer, sheet_name='Monthly_Returns')
                        # 3. 현재 매수 종목
                        df_picks.to_excel(writer, sheet_name='Current_Picks', index=False)
                        
                        # 엑셀 포맷팅
                        workbook = writer.book
                        format_pct = workbook.add_format({'num_format': '0.00%'})
                        format_money = workbook.add_format({'num_format': '#,##0.00' if currency_symbol=="USD" else '#,##0'})
                        
                        # Current_Picks 시트 포맷
                        worksheet_picks = writer.sheets['Current_Picks']
                        worksheet_picks.set_column('C:C', 12, format_pct) # 수익률
                        worksheet_picks.set_column('D:D', 15, format_money) # 가격

                    st.success("파일 생성 완료!")
                    
                    file_name_clean = target_market.replace(" ", "")
                    st.download_button(
                        label="📥 엑셀 파일 다운로드 (Excel)",
                        data=buffer.getvalue(),
                        file_name=f"{file_name_clean}_Momentum_Result.xlsx",
                        mime="application/vnd.ms-excel"
                    )
        else:
            st.warning("수익률을 계산할 수 없습니다. 데이터 기간이 너무 짧거나 종목 수가 부족할 수 있습니다.")