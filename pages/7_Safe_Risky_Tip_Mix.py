import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import numpy as np
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정 (Basic Setup)
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot') 

st.set_page_config(page_title="Custom HAA Simulator (Pro)", page_icon="🛡️", layout="wide")

st.title("🛡️ Custom HAA 전략 시뮬레이터 (Pro)")
st.markdown("""
**전략 로직 (사용자 정의):**
1. **공격 모드:** (TIP 모멘텀 > 0) AND (공격자산1 모멘텀 > 0)
   - 공격자산1 가격 > 이평선 : **공격자산2 (레버리지) 100%**
   - 공격자산1 가격 <= 이평선 : **공격자산1 (기초자산) 100%**
2. **방어 모드:** (TIP 모멘텀 <= 0) OR (공격자산1 모멘텀 <= 0)
   - 방어1(현금성), 방어2(채권) 모멘텀 둘 다 양수: **50% : 50% 혼합**
   - 하나만 양수: **양수인 자산 100%**
   - 둘 다 음수: **방어1(현금성) 100%**
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 사이드바 (설정)
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. 자산 선택 (Portfolio)")
    
    st.subheader("⚔️ 공격 자산 (Attack)")
    ticker_risky_base = st.selectbox(
        "공격 1 (기준/1배수)", 
        ["SPY", "QQQ", "IWM", "DIA"], 
        index=0,
        help="추세 판단의 기준이 되며, 약한 상승장에서 보유합니다."
    )
    
    risky_lev_options = {
        "SSO (S&P500 2배)": "SSO",
        "UPRO (S&P500 3배)": "UPRO",
        "QLD (나스닥 2배)": "QLD",
        "TQQQ (나스닥 3배)": "TQQQ",
        "UWM (러셀2000 2배)": "UWM"
    }
    r_lev_choice = st.selectbox("공격 2 (강세장/레버리지)", list(risky_lev_options.keys()), index=0)
    ticker_risky_lev = risky_lev_options[r_lev_choice]

    st.subheader("🛡️ 방어 자산 (Defense)")
    safe_cash_options = ["BIL", "SGOV", "SHV"]
    ticker_safe_cash = st.selectbox("방어 1 (현금성/초단기채)", safe_cash_options, index=0)
    
    safe_bond_options = ["IEF", "TLT", "GOVT", "BND"]
    ticker_safe_bond = st.selectbox("방어 2 (국채/중장기채)", safe_bond_options, index=0)

    st.subheader("🐥 카나리아 (Canary)")
    ticker_canary = st.selectbox("시장 위험 감지", ["TIP", "DBC", "VWO"], index=0)

    st.markdown("---")
    st.header("2. 자금 및 세금 설정")
    initial_capital = st.number_input("초기 투자금 (원화)", value=100000000, step=1000000, format="%d")
    commission_rate = st.number_input("매매 수수료율 (%)", value=0.07, step=0.01, format="%.2f") / 100.0
    apply_tax = st.checkbox("양도소득세 적용 (22%, 250만 공제)", value=True)

    st.markdown("---")
    st.header("3. 전략 파라미터")
    start_date = st.date_input("시작일 설정", pd.to_datetime("2010-01-01"))
    ma_window = st.number_input("추세 판단 이평선(일)", value=120)

# -----------------------------------------------------------------------------
# 3. 데이터 로딩 및 함수
# -----------------------------------------------------------------------------
@st.cache_data
def load_data(tickers, start):
    all_tickers = list(set(tickers + ["BIL"]))
    # 최근 데이터 확보를 위해 end 날짜 지정 없이 호출
    df = yf.download(all_tickers, start=start, progress=False, auto_adjust=True)
    
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
             df = df['Close'].copy()
        else:
             df = df.copy()
             if df.columns.nlevels > 1:
                 df.columns = df.columns.get_level_values(0)
    
    return df.sort_index()

def calculate_haa_score(series):
    """13612 모멘텀 스코어"""
    r1 = series.pct_change(21)
    r3 = series.pct_change(63)
    r6 = series.pct_change(126)
    r12 = series.pct_change(252)
    score = (r1 * 12) + (r3 * 4) + (r6 * 2) + (r12 * 1)
    return score

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 전략 실행 및 분석", type="primary", use_container_width=True):
    with st.spinner('데이터 분석 및 백테스팅 진행 중...'):
        
        # 1. 데이터 준비
        target_tickers = [ticker_risky_base, ticker_risky_lev, ticker_safe_cash, ticker_safe_bond, ticker_canary]
        try:
            raw_df = load_data(target_tickers, start_date)
            df_price = raw_df.dropna() 
            df_ret = df_price.pct_change().fillna(0)
            
        except Exception as e:
            st.error(f"데이터 로딩 실패: {e}")
            st.stop()

        # 2. 지표 계산
        score_df = pd.DataFrame(index=df_price.index)
        assets_to_score = [ticker_canary, ticker_risky_base, ticker_safe_cash, ticker_safe_bond]
        
        for t in assets_to_score:
            score_df[f'{t}_Score'] = calculate_haa_score(df_price[t])
        
        ma_line = df_price[ticker_risky_base].rolling(window=ma_window).mean()
        
        # 3. 백테스트 루프
        dates = df_price.index
        current_capital = initial_capital
        equity_curve = []
        weights_history = [] 
        
        current_weights = {} 
        trade_logs = []
        position_changes = []
        
        start_idx = 252
        year_realized_gain = 0
        
        for i in range(start_idx, len(dates)):
            today = dates[i]
            prev_date = dates[i-1]
            yesterday_idx = i - 1
            
            # --- [A] 시그널 판단 (전일 종가 기준) ---
            canary_score = score_df[f'{ticker_canary}_Score'].iloc[yesterday_idx]
            base_score = score_df[f'{ticker_risky_base}_Score'].iloc[yesterday_idx]
            cash_score = score_df[f'{ticker_safe_cash}_Score'].iloc[yesterday_idx]
            bond_score = score_df[f'{ticker_safe_bond}_Score'].iloc[yesterday_idx]
            
            base_price = df_price[ticker_risky_base].iloc[yesterday_idx]
            base_ma = ma_line.iloc[yesterday_idx]
            
            target_weights = {}
            mode_desc = ""
            
            # 로직 적용
            if (canary_score > 0) and (base_score > 0):
                if base_price > base_ma:
                    target_weights = {ticker_risky_lev: 1.0}
                    mode_desc = f"Bull Aggressive ({ticker_risky_lev})"
                else:
                    target_weights = {ticker_risky_base: 1.0}
                    mode_desc = f"Bull Moderate ({ticker_risky_base})"
            else:
                if (cash_score > 0) and (bond_score > 0):
                    target_weights = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
                    mode_desc = "Defense Mix (50:50)"
                elif cash_score > 0:
                    target_weights = {ticker_safe_cash: 1.0}
                    mode_desc = f"Defense ({ticker_safe_cash})"
                elif bond_score > 0:
                    target_weights = {ticker_safe_bond: 1.0}
                    mode_desc = f"Defense ({ticker_safe_bond})"
                else:
                    target_weights = {ticker_safe_cash: 1.0}
                    mode_desc = f"Defense Cash ({ticker_safe_cash})"
            
            # --- [B] 리밸런싱 ---
            if set(target_weights.keys()) != set(current_weights.keys()):
                 fee = current_capital * commission_rate
                 current_capital -= fee
                 trade_logs.append({
                     "Date": today.strftime('%Y-%m-%d'),
                     "Type": "Rebalance",
                     "Desc": mode_desc,
                     "Weights": str(target_weights),
                     "Amount": round(current_capital),
                     "Fee": round(fee)
                 })
                 position_changes.append({
                     "Date": today,
                     "Desc": mode_desc.split('(')[0].strip(),
                     "Detail": mode_desc
                 })
            
            current_weights = target_weights

            # --- [C] 수익률 적용 ---
            daily_ret = 0
            for ticker, weight in current_weights.items():
                daily_ret += df_ret[ticker].iloc[i] * weight
            
            profit = current_capital * daily_ret
            current_capital += profit
            year_realized_gain += profit
            
            equity_curve.append(current_capital)
            weights_history.append(str(current_weights)) # 딕셔너리를 문자열로 저장
            
            # --- [D] 세금 ---
            if apply_tax and (today.year != prev_date.year):
                taxable = max(0, year_realized_gain - 2500000)
                tax = taxable * 0.22
                if tax > 0:
                    current_capital -= tax
                    trade_logs.append({
                        "Date": today.strftime('%Y-%m-%d'),
                        "Type": "Tax",
                        "Desc": f"{prev_date.year}년 귀속 양도세",
                        "Weights": "-",
                        "Amount": -round(tax),
                        "Fee": 0
                    })
                year_realized_gain = 0

        # 결과 정리
        res_index = dates[start_idx:]
        res_df = pd.DataFrame({
            'Equity': equity_curve,
            'Price': df_price[ticker_risky_base].iloc[start_idx:],
            'MA': ma_line.iloc[start_idx:],
            'Strategy_Pos': weights_history
        }, index=res_index)

        for col in score_df.columns:
            res_df[col] = score_df.loc[res_index, col]

        # ---------------------------------------------------------------------
        # [✨ NEW] 오늘의 투자 가이드 (Action Plan)
        # ---------------------------------------------------------------------
        st.divider()
        st.subheader("📢 오늘의 투자 가이드 (Action Plan)")

        # 마지막 날짜(데이터 기준 가장 최신)의 포지션 확인
        last_date = res_index[-1]
        last_pos_str = weights_history[-1]
        last_pos_dict = eval(last_pos_str) # 문자열을 다시 딕셔너리로 변환

        # 어제(그 전날) 포지션 확인 (변경 여부 확인용)
        prev_pos_str = weights_history[-2] if len(weights_history) > 1 else "{}"
        prev_pos_dict = eval(prev_pos_str)

        # 1. 포지션 변경 여부 확인
        is_changed = (last_pos_dict != prev_pos_dict)
        
        # 2. 보유해야 할 자산 텍스트 생성
        if len(last_pos_dict) > 1:
            target_asset_str = " + ".join([f"{k} ({v*100:.0f}%)" for k, v in last_pos_dict.items()])
            target_ticker_only = ", ".join(last_pos_dict.keys())
        else:
            target_ticker_only = list(last_pos_dict.keys())[0]
            target_asset_str = f"{target_ticker_only} (100%)"

        # 3. UI 표시
        # 컨테이너로 묶어서 강조
        with st.container():
            col_act1, col_act2 = st.columns([1, 2])
            
            with col_act1:
                st.markdown(f"**기준 데이터 날짜:** {last_date.date()}")
                if is_changed:
                    st.error("🚨 **매매 신호 발생 (Trade Required)**", icon="🚨")
                else:
                    st.success("✅ **포지션 유지 (Hold)**", icon="✅")
            
            with col_act2:
                if is_changed:
                    st.markdown(f"#### 👉 **[{target_asset_str}]** 로 교체 매수하세요.")
                    st.info(f"기존 보유 종목을 전량 매도하고, **{target_ticker_only}** 매수를 진행하시면 됩니다.")
                else:
                    st.markdown(f"#### 👉 **[{target_asset_str}]** 계속 보유하세요.")
                    st.info("포지션 변경 사항이 없습니다. 편안하게 관망하시면 됩니다.")

        st.divider()

        # ---------------------------------------------------------------------
        # [기존 결과 리포팅]
        # ---------------------------------------------------------------------
        final_cap = equity_curve[-1]
        cagr = (final_cap / initial_capital) ** (1 / ((len(res_index)/252))) - 1
        peak_series = res_df['Equity'].cummax()
        mdd_series = (res_df['Equity'] - peak_series) / peak_series
        mdd = mdd_series.min()
        
        st.subheader("📊 백테스트 결과 요약")
        col1, col2, col3 = st.columns(3)
        col1.metric("최종 자산 (Final Equity)", f"{final_cap:,.0f} 원")
        col2.metric("CAGR (연평균 수익률)", f"{cagr*100:.2f} %")
        col3.metric("MDD (최대 낙폭)", f"{mdd*100:.2f} %")
        
        # ---------------------------------------------------------------------
        # [차트 및 엑셀 다운로드 (기존 코드 유지)]
        # ---------------------------------------------------------------------
        st.subheader("📈 상세 분석 차트")
        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. Equity
        axes[0, 0].plot(res_df.index, res_df['Equity'], label='Strategy', color='firebrick')
        axes[0, 0].set_yscale('log')
        axes[0, 0].set_title('1. Equity Curve (Log Scale)')
        axes[0, 0].legend()
        axes[0, 0].grid(True, which='both', alpha=0.3)
        
        for change in position_changes:
            c_date = change['Date']
            if c_date in res_df.index:
                c_price = res_df.loc[c_date, 'Equity']
                axes[0, 0].annotate('', xy=(c_date, c_price), xytext=(c_date, c_price * 1.15),
                                    arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=5))

        # 2. MDD
        axes[0, 1].plot(res_df.index, mdd_series * 100, label='Drawdown', color='blue')
        axes[0, 1].fill_between(res_df.index, mdd_series * 100, 0, color='blue', alpha=0.1)
        axes[0, 1].set_title('2. Drawdown (%)')
        axes[0, 1].grid(True, alpha=0.3)
        
        # 3. Market Trend
        axes[1, 0].plot(res_df.index, res_df['Price'], label=f'{ticker_risky_base} Price', color='black', alpha=0.6)
        axes[1, 0].plot(res_df.index, res_df['MA'], label=f'MA ({ma_window})', color='orange', linestyle='--')
        axes[1, 0].set_title(f'3. Market Trend ({ticker_risky_base} vs MA)')
        axes[1, 0].legend()
        axes[1, 0].grid(True, alpha=0.3)
        
        # 4. Canary
        axes[1, 1].plot(res_df.index, res_df[f'{ticker_canary}_Score'], color='purple', label=f'{ticker_canary} Momentum')
        axes[1, 1].axhline(0, color='red', linestyle='--')
        axes[1, 1].fill_between(res_df.index, res_df[f'{ticker_canary}_Score'], 0, where=(res_df[f'{ticker_canary}_Score'] < 0), color='red', alpha=0.2)
        axes[1, 1].set_title(f'4. Risk Signal ({ticker_canary})')
        axes[1, 1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        st.pyplot(fig)

        # 엑셀 다운로드 로직
        m_equity = res_df['Equity'].resample('M').last()
        m_ret = m_equity.pct_change().fillna(0)
        monthly_df = pd.DataFrame(m_ret)
        monthly_df['Year'] = monthly_df.index.year
        monthly_df['Month'] = monthly_df.index.month
        monthly_pivot = monthly_df.pivot(index='Year', columns='Month', values='Equity')
        monthly_pivot.columns = [calendar.month_abbr[i] for i in monthly_pivot.columns]
        
        yearly_ret_list = []
        for y in monthly_pivot.index:
            yr_data = res_df[res_df.index.year == y]['Equity']
            if len(yr_data) > 0:
                start_val = yr_data.iloc[0]
                prev_year_data = res_df[res_df.index.year == (y - 1)]['Equity']
                if not prev_year_data.empty:
                    start_val = prev_year_data.iloc[-1]
                end_val = yr_data.iloc[-1]
                yearly_ret_list.append((end_val / start_val) - 1)
            else:
                yearly_ret_list.append(0)
        
        monthly_pivot['Year_Total'] = yearly_ret_list

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            res_df.to_excel(writer, sheet_name='Daily_Data')
            pd.DataFrame(trade_logs).to_excel(writer, sheet_name='Trade_Log', index=False)
            monthly_pivot.to_excel(writer, sheet_name='Monthly_Returns')
            workbook = writer.book
            worksheet_m = writer.sheets['Monthly_Returns']
            pct_fmt = workbook.add_format({'num_format': '0.00%'})
            worksheet_m.set_column(1, 13, 10, pct_fmt)

        st.download_button(
            "📥 분석 리포트 다운로드 (Excel)",
            data=output.getvalue(),
            file_name=f"HAA_Action_Plan_{res_index[-1].date()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )