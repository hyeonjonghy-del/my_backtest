import streamlit as st
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import datetime
import io

# -----------------------------------------------------------------------------
# 1. 페이지 설정
# -----------------------------------------------------------------------------
st.set_page_config(page_title="올웨더 포트폴리오", page_icon="🛡️")

@st.cache_data(ttl=3600*24)
def get_asset_data(tickers, start, end):
    try:
        # end 날짜 포함을 위해 +1일
        end_dt = end + datetime.timedelta(days=1)
        data = yf.download(tickers, start=start, end=end_dt, progress=False, auto_adjust=True)
        
        # MultiIndex 컬럼 처리
        if isinstance(data.columns, pd.MultiIndex):
            if 'Close' in data.columns.levels[0]:
                df = data['Close'].copy()
            else:
                df = data.copy()
        else:
            df = data.copy()
            
        return df.dropna()
    except Exception as e:
        return pd.DataFrame()

# -----------------------------------------------------------------------------
# 2. 사이드바 UI
# -----------------------------------------------------------------------------
st.title("🛡️ 올웨더 포트폴리오 (All Weather)")
st.markdown("""
**"어떤 경제 상황에서도 살아남는다"**는 레이 달리오의 자산 배분 전략입니다.
주식, 채권, 원자재, 금에 분산 투자하여 **MDD(낙폭)를 최소화**합니다.
""")

with st.sidebar:
    st.header("⚙️ 포트폴리오 설정")
    
    # 날짜 설정
    start_date = st.date_input("시작일", datetime.date(2010, 1, 1))
    end_date = st.date_input("종료일", datetime.date.today())
    
    st.subheader("자산 비중 설정 (기본값: 레이 달리오)")
    w_stock = st.number_input("주식 (SPY)", value=30.0, step=5.0)
    w_long_bond = st.number_input("장기채 (TLT)", value=40.0, step=5.0)
    w_mid_bond = st.number_input("중기채 (IEF)", value=15.0, step=5.0)
    w_gold = st.number_input("금 (GLD)", value=7.5, step=2.5)
    w_commodity = st.number_input("원자재 (DBC)", value=7.5, step=2.5)
    
    # 비중 합계 검증
    total_w = w_stock + w_long_bond + w_mid_bond + w_gold + w_commodity
    if total_w != 100:
        st.warning(f"⚠️ 현재 비중 합계: {total_w:.1f}% (100%가 되도록 맞춰주세요)")
    
    st.markdown("---")
    export_excel = st.checkbox("📥 엑셀 다운로드 기능 활성화", value=True)
    
    run_btn = st.button("🚀 전략 실행", type="primary")

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
if run_btn:
    # 티커 정의 (알파벳순 정렬됨에 주의)
    tickers = ['SPY', 'TLT', 'IEF', 'GLD', 'DBC']
    
    with st.spinner("자산 데이터를 가져오는 중..."):
        df = get_asset_data(tickers, start_date, end_date)
        
        if df.empty:
            st.error("데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        else:
            # 1. 수익률 계산
            daily_ret = df.pct_change().fillna(0)
            
            # 2. 비중 적용 (입력값을 1.0 단위로 변환 및 정규화)
            user_weights = {
                'SPY': w_stock, 'TLT': w_long_bond, 'IEF': w_mid_bond,
                'GLD': w_gold, 'DBC': w_commodity
            }
            
            # yfinance 데이터 컬럼 순서에 맞춰 웨이트 리스트 생성
            # (데이터프레임 컬럼이 알파벳순일 수 있으므로 매핑 필요)
            ordered_weights = []
            for col in df.columns:
                # 컬럼명이 티커 이름과 일치하는지 확인
                if col in user_weights:
                    ordered_weights.append(user_weights[col])
                else:
                    ordered_weights.append(0) # 매칭 안되면 0
            
            # 합계가 100이 아닐 경우를 대비해 정규화 (비율대로 나눔)
            weight_sum = sum(ordered_weights)
            if weight_sum == 0: weight_sum = 1
            final_weights = [w / weight_sum for w in ordered_weights]
            
            # 3. 포트폴리오 수익률 (일일 리밸런싱 가정)
            df['Portfolio_Ret'] = daily_ret.dot(final_weights)
            
            # 비교군 (SPY 100%)
            if 'SPY' in df.columns:
                df['SPY_Ret'] = daily_ret['SPY']
            
            # 4. 자산 성장 및 MDD
            df['All_Weather'] = (1 + df['Portfolio_Ret']).cumprod()
            if 'SPY' in df.columns:
                df['SPY_Only'] = (1 + df['SPY_Ret']).cumprod()
            
            # 통계 함수
            def get_stats(series):
                total_days = len(series)
                cagr = series.iloc[-1] ** (252/total_days) - 1
                running_max = series.cummax()
                dd = (series / running_max) - 1
                mdd = dd.min()
                return cagr, mdd
            
            cagr_aw, mdd_aw = get_stats(df['All_Weather'])
            
            # 결과 출력
            col1, col2, col3 = st.columns(3)
            col1.metric("총 수익률", f"{(df['All_Weather'].iloc[-1]-1)*100:.2f}%")
            col2.metric("CAGR (연평균)", f"{cagr_aw*100:.2f}%")
            col3.metric("최대 낙폭 (MDD)", f"{mdd_aw*100:.2f}%", delta_color="inverse")
            
            # 탭 구성
            tab1, tab2, tab3 = st.tabs(["📊 차트 분석", "⚖️ 자산 비중", "💾 데이터"])
            
            with tab1:
                st.subheader("자산 성장 & MDD 비교")
                fig, ax = plt.subplots(2, 1, figsize=(10, 10), gridspec_kw={'height_ratios': [2, 1]})
                
                # 성장 그래프
                ax[0].plot(df.index, df['All_Weather'], label='All Weather', color='green', linewidth=2)
                if 'SPY_Only' in df.columns:
                    ax[0].plot(df.index, df['SPY_Only'], label='SPY (Stocks)', color='gray', linestyle='--', alpha=0.5)
                ax[0].set_title("Portfolio Growth (Log Scale)")
                ax[0].set_yscale('log')
                ax[0].legend()
                ax[0].grid(alpha=0.3)
                
                # MDD 그래프
                dd_aw = (df['All_Weather'] / df['All_Weather'].cummax()) - 1
                ax[1].fill_between(df.index, dd_aw, 0, color='green', alpha=0.1)
                ax[1].plot(df.index, dd_aw, label='All Weather MDD', color='green', linewidth=1)
                
                if 'SPY_Only' in df.columns:
                    dd_spy = (df['SPY_Only'] / df['SPY_Only'].cummax()) - 1
                    ax[1].plot(df.index, dd_spy, label='SPY MDD', color='gray', alpha=0.3, linewidth=1)
                    
                ax[1].set_title("Drawdown Risk")
                ax[1].legend()
                ax[1].grid(alpha=0.3)
                
                st.pyplot(fig)
                
            with tab2:
                st.subheader("설정된 자산 비중")
                w_df = pd.DataFrame(list(user_weights.items()), columns=['Ticker', 'Weight'])
                w_df['Weight'] = w_df['Weight'].apply(lambda x: f"{x:.1f}%")
                
                # 파이 차트
                fig2, ax2 = plt.subplots()
                ax2.pie(user_weights.values(), labels=user_weights.keys(), autopct='%1.1f%%', startangle=90)
                ax2.axis('equal')
                st.pyplot(fig2)
                st.table(w_df)
                
            with tab3:
                # 월별 수익률 계산
                monthly_ret = df['Portfolio_Ret'].resample('ME').apply(lambda x: (1 + x).prod() - 1)
                monthly_table = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).sum().unstack()
                monthly_table.columns = [f"{c}월" for c in monthly_table.columns]
                
                st.subheader("📅 월별 수익률")
                st.dataframe(monthly_table.style.background_gradient(cmap='RdYlGn', axis=None).format("{:.2%}"))

                # 엑셀 다운로드
                if export_excel:
                    st.markdown("---")
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        df.to_excel(writer, sheet_name='Daily_Data')
                        monthly_table.to_excel(writer, sheet_name='Monthly_Returns')
                        w_df.to_excel(writer, sheet_name='Weights', index=False)
                        
                        # 포맷팅
                        workbook = writer.book
                        fmt_pct = workbook.add_format({'num_format': '0.00%'})
                        writer.sheets['Monthly_Returns'].set_column('B:N', 10, fmt_pct)
                        
                    st.download_button(
                        label="📥 엑셀 파일 다운로드",
                        data=buffer.getvalue(),
                        file_name="All_Weather_Portfolio.xlsx",
                        mime="application/vnd.ms-excel"
                    )