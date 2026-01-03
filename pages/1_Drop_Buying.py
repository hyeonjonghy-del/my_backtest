import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import datetime

# --- 페이지 설정 ---
st.set_page_config(page_title="무한매수법/변형 백테스트", layout="wide")

st.title("💰 Buy the Dip (Scale Trading) Backtest")
st.markdown("하락장에서 분할 매수하는 전략(변형 무한매수/VR 등)을 백테스트합니다.")

# --- 사이드바: 설정 입력 ---
st.sidebar.header("1. 종목 및 기간 설정")
ticker_base = st.sidebar.text_input("기준 종목 (Base)", value="QQQ")
ticker_leveraged = st.sidebar.text_input("투자 종목 (Leveraged)", value="TQQQ")

# [수정됨] 시작일을 2016년 1월 1일로 변경
start_date = st.sidebar.date_input("시작일", value=datetime.date(2016, 1, 1))
end_date = st.sidebar.date_input("종료일", value=datetime.date.today())

st.sidebar.header("2. 하락폭 기준 (지수 대비)")
col1, col2 = st.sidebar.columns(2)
# [수정됨] 이미지 2에 맞춰 하락폭 기준값 변경 (-20, -25, -30, -35, -40)
drop_level_1 = st.sidebar.number_input("1단계 하락 (%)", value=-20.0, step=1.0)
drop_level_2 = st.sidebar.number_input("2단계 하락 (%)", value=-25.0, step=1.0)
drop_level_3 = st.sidebar.number_input("3단계 하락 (%)", value=-30.0, step=1.0)
drop_level_4 = st.sidebar.number_input("4단계 하락 (%)", value=-35.0, step=1.0)
drop_level_5 = st.sidebar.number_input("5단계 하락 (%)", value=-40.0, step=1.0)

st.sidebar.header("3. 구간별 공격형 비중 (%)")
# [수정됨] 이미지 1에 맞춰 비중값 변경 (0, 40, 60, 80, 100, 100)
upro_ratio_0 = st.sidebar.slider("평상시 (0구간)", 0, 100, 0)
upro_ratio_1 = st.sidebar.slider("1단계 (L1~L2)", 0, 100, 40)
upro_ratio_2 = st.sidebar.slider("2단계 (L2~L3)", 0, 100, 60)
upro_ratio_3 = st.sidebar.slider("3단계 (L3~L4)", 0, 100, 80)
upro_ratio_4 = st.sidebar.slider("4단계 (L4~L5)", 0, 100, 100)
upro_ratio_5 = st.sidebar.slider("5단계 (L5~)", 0, 100, 100)

# --- 백테스트 실행 버튼 ---
if st.sidebar.button("🚀 백테스트 실행"):
    with st.spinner('데이터를 불러오고 계산 중입니다...'):
        try:
            # 1. 데이터 다운로드
            tickers = [ticker_base, ticker_leveraged]
            # yfinance는 end 날짜를 포함하지 않으므로 +1일 처리
            end_date_adj = end_date + datetime.timedelta(days=1)
            
            data = yf.download(tickers, start=start_date, end=end_date_adj, auto_adjust=True, progress=False)
            
            if data.empty:
                st.error("데이터를 가져올 수 없습니다. 티커를 확인해주세요.")
                st.stop()

            # 데이터 구조 처리
            if isinstance(data.columns, pd.MultiIndex):
                if 'Close' in data.columns.levels[0]:
                     df = data['Close'].copy()
                else:
                     df = data.xs('Close', level=0, axis=1, drop_level=True)
            else:
                df = data.copy()
            df = df.dropna()

            # 2. 지표 계산
            df['Base_ATH'] = df[ticker_base].cummax()
            df['Base_DD'] = (df[ticker_base] - df['Base_ATH']) / df['Base_ATH']
            df['Base_DD_PCT'] = df['Base_DD'] * 100

            # 3. 비중 계산
            df['W_Risky'] = 0.0
            
            # 조건 설정
            L1, L2, L3, L4, L5 = drop_level_1, drop_level_2, drop_level_3, drop_level_4, drop_level_5
            R0, R1, R2, R3, R4, R5 = upro_ratio_0/100, upro_ratio_1/100, upro_ratio_2/100, upro_ratio_3/100, upro_ratio_4/100, upro_ratio_5/100

            conditions = [
                (df['Base_DD_PCT'] > L1),
                (df['Base_DD_PCT'] <= L1) & (df['Base_DD_PCT'] > L2),
                (df['Base_DD_PCT'] <= L2) & (df['Base_DD_PCT'] > L3),
                (df['Base_DD_PCT'] <= L3) & (df['Base_DD_PCT'] > L4),
                (df['Base_DD_PCT'] <= L4) & (df['Base_DD_PCT'] > L5),
                (df['Base_DD_PCT'] <= L5)
            ]
            choices = [R0, R1, R2, R3, R4, R5]
            
            df['W_Risky'] = np.select(conditions, choices, default=0.0)
            df['W_Safe'] = 1 - df['W_Risky']

            # 4. 수익률 계산
            daily_ret = df[[ticker_base, ticker_leveraged]].pct_change().fillna(0)
            df['Strategy_Ret'] = (daily_ret[ticker_base] * df['W_Safe'].shift(1)) + \
                                 (daily_ret[ticker_leveraged] * df['W_Risky'].shift(1))
            df['Strategy_Ret'] = df['Strategy_Ret'].fillna(0)

            # 5. 자산 성장 및 MDD
            df['My_Asset'] = (1 + df['Strategy_Ret']).cumprod()
            df['Base_Hold'] = (1 + daily_ret[ticker_base]).cumprod()
            
            running_max = df['My_Asset'].cummax()
            drawdown = (df['My_Asset'] / running_max) - 1
            mdd_min = drawdown.min()
            
            total_days = len(df)
            final_return = df['My_Asset'].iloc[-1]
            cagr = final_return ** (252/total_days) - 1

            # --- 결과 표시 ---
            st.success("분석 완료!")
            
            # 주요 지표 (Metric)
            m1, m2, m3 = st.columns(3)
            m1.metric("최종 수익률 (Total)", f"{(final_return-1)*100:.2f}%")
            m2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
            m3.metric("최대 낙폭 (MDD)", f"{mdd_min*100:.2f}%")

            # 차트 그리기
            fig, ax = plt.subplots(2, 1, figsize=(10, 10))
            
            # 상단: 자산 추이
            ax[0].plot(df.index, df['My_Asset'], label='My Strategy', color='red')
            ax[0].plot(df.index, df['Base_Hold'], label=f'{ticker_base} Buy&Hold', color='gray', linestyle='--')
            ax[0].set_yscale('log')
            ax[0].set_title(f'Asset Growth (Log Scale)')
            ax[0].legend()
            ax[0].grid(True, alpha=0.3)
            
            # 하단: MDD 및 구간
            ax[1].plot(df.index, df['Base_DD_PCT'], color='black', alpha=0.5, label='Drawdown')
            ax[1].fill_between(df.index, 0, -100, where=(df['W_Risky'] >= 0.5), color='red', alpha=0.2, label='Aggressive Zone')
            ax[1].axhline(drop_level_1, color='green', linestyle=':', label='L1')
            ax[1].axhline(drop_level_4, color='orange', linestyle=':', label='L4')
            ax[1].axhline(drop_level_5, color='red', linestyle=':', label='L5')
            ax[1].set_ylim(-60, 5)
            ax[1].legend(loc='lower left')
            ax[1].grid(True, alpha=0.3)
            
            st.pyplot(fig)

            # 데이터 보기 옵션
            with st.expander("상세 데이터 보기"):
                st.dataframe(df.tail(100))

        except Exception as e:
            st.error(f"오류가 발생했습니다: {e}")