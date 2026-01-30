import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot')
# 한글 폰트 설정 제거 (깨짐 방지)
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="K-Momentum Multi-Signal", page_icon="🇰🇷", layout="wide")

st.title("🇰🇷 K-Switch: Multi-Signal Strategy")
st.markdown("""
**Strategy Overview:**
The Korean market has a strong 'box-range' tendency. Timing of entry/exit is crucial.
Compare 3 different signals to find the best fit for your risk appetite.
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 데이터 미리 받기
# -----------------------------------------------------------------------------
K_TICKERS = {
    "KOSPI Index": "^KS11",
    "USD/KRW": "KRW=X",
    "US S&P500": "SPY",
    "KODEX 200": "069500.KS",            
    "KODEX KOSDAQ150": "229200.KS",      
    "KODEX Leverage": "122630.KS",       
    "KODEX KOSDAQ150 Leverage": "233740.KS", 
    "TIGER China EV": "371460.KS",    
    "KODEX KTB 10Y": "152380.KS",     
    "KODEX Short-term Bond": "153130.KS",       
    "KODEX KOFR": "423160.KS",       
}

@st.cache_data(ttl=3600*24)
def load_k_data_v4():
    tickers = list(K_TICKERS.values())
    df = yf.download(tickers, start="2010-01-01", progress=False, auto_adjust=True)
    
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
             df = df['Close'].copy()
        else:
             df = df.copy()
             if df.columns.nlevels > 1:
                 df.columns = df.columns.get_level_values(0)
    
    df = df.loc[~df.index.duplicated(keep='first')]
    return df.sort_index()

# -----------------------------------------------------------------------------
# 3. 사이드바 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. Portfolio Setup")
    
    st.subheader("⚔️ Aggressive Assets")
    att1_options = ["KODEX 200", "KODEX KOSDAQ150", "TIGER China EV"]
    att1_name = st.selectbox("Main Asset (1x)", att1_options, index=0)
    ticker_att1 = K_TICKERS.get(att1_name, "069500.KS")
    
    att2_options = ["KODEX Leverage", "KODEX KOSDAQ150 Leverage", "KODEX 200", "KODEX KOSDAQ150"]
    att2_name = st.selectbox("Sub Asset (Leverage)", att2_options, index=0)
    ticker_att2 = K_TICKERS.get(att2_name, "122630.KS")

    st.subheader("⚖️ Weights (Bull Market)")
    att1_weight = st.slider(f"{att1_name} Weight (%)", 0, 100, 100, 10)
    w1 = att1_weight / 100.0
    w2 = 1.0 - w1
    
    st.subheader("🛡️ Defensive Assets")
    def_name = st.selectbox("Safe Haven", ["KODEX KTB 10Y", "KODEX Short-term Bond", "KODEX KOFR"], index=0)
    ticker_def = K_TICKERS.get(def_name, "152380.KS")

    st.markdown("---")
    st.header("2. Signal Selection")
    
    # [수정] 기본값을 "미국 S&P500 (SPY)"로 설정 (index=2)
    signal_type = st.radio(
        "Which signal to use?",
        ("USD/KRW", "KOSPI", "S&P500 (SPY)"),
        index=2
    )
    
    if signal_type == "USD/KRW":
        ticker_sig = "KRW=X"
        sig_desc = "Buy when FX is below MA"
        is_inverted = True 
    elif signal_type == "KOSPI":
        ticker_sig = "^KS11"
        sig_desc = "Buy when KOSPI is above MA"
        is_inverted = False 
    else:
        ticker_sig = "SPY"
        sig_desc = "Buy when SPY is above MA"
        is_inverted = False

    st.info(f"💡 **Signal:** {sig_desc}")

    st.markdown("---")
    st.header("3. Options")
    initial_capital = st.number_input("Initial Capital (KRW)", value=100000000, step=1000000, format="%d")
    fee_rate = st.number_input("Trading Fee (%)", value=0.02, step=0.01, format="%.2f") / 100.0
    tax_rate = st.number_input("Tax (%)", value=0.0, step=1.0, format="%.1f") / 100.0
    
    # [수정] 기본 시작일을 2020년으로 설정
    start_date = st.date_input("Start Date", pd.to_datetime("2020-01-01"))
    ma_window = st.number_input("MA Window (Days)", value=120)

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 Run Backtest", type="primary", use_container_width=True):
    with st.spinner("Analyzing data..."):
        full_df = load_k_data_v4()
    
    use_tickers = [ticker_att1, ticker_att2, ticker_def, ticker_sig]
    missing = [t for t in use_tickers if t not in full_df.columns]
    if missing:
        st.error(f"Missing Data: {missing}. Please refresh or change tickers.")
        st.stop()
        
    df_raw = full_df[use_tickers].ffill()
    
    sim_start = pd.to_datetime(start_date)
    if sim_start < df_raw.index[0]: sim_start = df_raw.index[0]
    
    sig_series = df_raw[ticker_sig]
    ma_line = sig_series.rolling(window=ma_window).mean()
    
    df_price = df_raw.loc[sim_start:]
    ma_line = ma_line.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)
    
    dates = df_price.index
    equity = initial_capital
    curve, bench_curve = [], []
    bench_equity = initial_capital
    
    logs = []
    curr_w = {ticker_def: 1.0}
    prev_state = "Init"
    year_gain = 0
    
    for i in range(len(dates)):
        today = dates[i]
        if i > 0:
            bench_equity *= (1 + df_ret[ticker_att1].iloc[i])
        bench_curve.append(bench_equity)
        
        if i == 0:
            curve.append(equity)
            continue
            
        last_val = df_price[ticker_sig].iloc[i-1]
        last_ma = ma_line.iloc[i-1]
        
        target_w = {}
        state = ""
        is_bull = False
        
        if is_inverted: # FX
            if last_val < last_ma: is_bull = True
        else: # Index
            if last_val > last_ma: is_bull = True
            
        if is_bull:
            target_w = {ticker_att1: w1, ticker_att2: w2}
            target_w = {k:v for k,v in target_w.items() if v > 0}
            state = "Bull (Attack)"
        else:
            target_w = {ticker_def: 1.0}
            state = "Bear (Defense)"
            
        is_chg = (state != prev_state)
        is_month = (today.month != dates[i-1].month)
        
        if is_chg or is_month:
            turnover = 0
            all_keys = set(curr_w.keys()) | set(target_w.keys())
            for k in all_keys:
                turnover += abs(target_w.get(k,0) - curr_w.get(k,0))
            
            cost = (turnover / 2) * equity * fee_rate
            equity -= cost
            curr_w = target_w.copy()
            
            if cost > 0:
                logs.append({
                    "Date": today.strftime('%Y-%m-%d'), 
                    "Action": "Rebal", 
                    "State": state, 
                    "Cost": round(cost)
                })
        
        prev_state = state
        day_ret = sum(df_ret[t].iloc[i] * w for t, w in curr_w.items())
        
        profit = equity * day_ret
        equity += profit
        year_gain += profit
        curve.append(equity)
        
        if tax_rate > 0 and (i == len(dates)-1 or dates[i+1].year != today.year):
            tax = max(0, year_gain) * tax_rate
            if tax > 0:
                equity -= tax
                logs.append({"Date": today.strftime('%Y-%m-%d'), "Action": "Tax", "State": "-", "Cost": round(tax)})
            year_gain = 0

    res_df = pd.DataFrame({
        'Equity': curve,
        'Benchmark': bench_curve,
        'Signal_Val': df_price[ticker_sig],
        'Signal_MA': ma_line
    }, index=dates)
    
    final, final_b = curve[-1], bench_curve[-1]
    days = len(curve)
    cagr = (final/initial_capital)**(252/days) - 1
    cagr_b = (final_b/initial_capital)**(252/days) - 1
    
    peak = res_df['Equity'].cummax()
    mdd = ((res_df['Equity'] - peak) / peak).min()
    peak_b = res_df['Benchmark'].cummax()
    mdd_b = ((res_df['Benchmark'] - peak_b) / peak_b).min()
    
    # -------------------------------------------------------------------------
    # 결과 화면
    # -------------------------------------------------------------------------
    st.divider()
    c1, c2 = st.columns([1, 2])
    c1.metric("Final Balance", f"{final:,.0f} KRW", delta=f"vs Bench: {final - final_b:,.0f}")
    c1.metric("CAGR", f"{cagr*100:.2f} %", delta=f"{(cagr - cagr_b)*100:.2f}%p")
    c1.metric("MDD", f"{mdd*100:.2f} %", delta=f"Bench: {mdd_b*100:.2f}%")
    
    # 엑셀 다운로드 생성
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        res_df.to_excel(writer, sheet_name='EquityCurve')
        if logs: pd.DataFrame(logs).to_excel(writer, sheet_name='TradeLogs')
        st.info("Excel file prepared for download.")
    
    st.download_button(
        label="📥 Download Results (Excel)",
        data=output.getvalue(),
        file_name=f"Backtest_Result_{signal_type}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    with c2:
        last_val = res_df['Signal_Val'].iloc[-1]
        last_ma = res_df['Signal_MA'].iloc[-1]
        is_now_bull = (last_val < last_ma) if is_inverted else (last_val > last_ma)
            
        st.markdown(f"### 📢 Current Signal Status ({signal_type})")
        st.write(f"Current: **{last_val:,.2f}** / Baseline: **{last_ma:,.2f}**")
        
        if is_now_bull:
            st.success("📈 **Bull (Attack)**: Signal is positive. Allocate to stocks.")
        else:
            st.warning("🛡️ **Bear (Defense)**: Signal is negative. Move to safe assets.")

    tab1, tab2, tab3 = st.tabs(["📈 Chart", "📝 Trade Logs", "📅 Monthly Returns"])
    
    with tab1:
        fig, axes = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})
        
        # 1. Equity (한글 제거)
        axes[0].plot(res_df.index, res_df['Equity'], color='firebrick', label='Strategy')
        axes[0].plot(res_df.index, res_df['Benchmark'], color='gray', linestyle='--', alpha=0.6, label='Bench')
        axes[0].set_yscale('log')
        axes[0].set_title("1. Equity Curve (Log Scale)")
        axes[0].legend()
        
        # 2. MDD
        dd = (res_df['Equity'] - peak) / peak
        dd_b = (res_df['Benchmark'] - peak_b) / peak_b
        axes[1].plot(res_df.index, dd * 100, color='blue', label='Strategy MDD')
        axes[1].plot(res_df.index, dd_b * 100, color='gray', linestyle=':', alpha=0.5, label='Bench MDD')
        axes[1].fill_between(res_df.index, dd * 100, 0, color='blue', alpha=0.1)
        axes[1].set_title("2. Drawdown (%)")
        axes[1].legend()
        
        # 3. Signal (한글 제거)
        sig_color = 'green' if not is_inverted else 'purple'
        axes[2].plot(res_df.index, res_df['Signal_Val'], label='Signal Value', color=sig_color)
        axes[2].plot(res_df.index, res_df['Signal_MA'], label='MA Line', color='orange', linestyle='--')
        
        if is_inverted: # FX
            axes[2].fill_between(res_df.index, res_df['Signal_Val'], res_df['Signal_MA'], 
                                 where=(res_df['Signal_Val'] > res_df['Signal_MA']), color='red', alpha=0.3, label='Defensive Zone')
        else: # Index
            axes[2].fill_between(res_df.index, res_df['Signal_Val'], res_df['Signal_MA'], 
                                 where=(res_df['Signal_Val'] < res_df['Signal_MA']), color='red', alpha=0.3, label='Defensive Zone')
            
        axes[2].set_title(f"3. Signal Indicator ({signal_type})")
        axes[2].legend()
        
        plt.tight_layout()
        st.pyplot(fig)
        
    with tab2:
        if logs: st.dataframe(pd.DataFrame(logs), use_container_width=True)
        else: st.info("No logs found.")
        
    with tab3:
        m_eq = res_df['Equity'].resample('M').last()
        m_ret = m_eq.pct_change().fillna(0)
        m_df = pd.DataFrame(m_ret)
        m_df['Year'] = m_df.index.year
        m_df['Month'] = m_df.index.month
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Equity')
        m_pivot.columns = [calendar.month_abbr[i] for i in m_pivot.columns]
        
        yearly_ret = []
        for y in m_pivot.index:
            yd = res_df[res_df.index.year == y]['Equity']
            if len(yd) > 0:
                start_v = res_df[res_df.index.year == (y-1)]['Equity'].iloc[-1] if y > res_df.index.year.min() else yd.iloc[0]
                yearly_ret.append((yd.iloc[-1]/start_v) - 1)
            else: yearly_ret.append(0)
        m_pivot['Total'] = yearly_ret
        
        styler = m_pivot.style\
            .background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1)\
            .format("{:.2%}", na_rep="")
        st.dataframe(styler, use_container_width=True, height=600)