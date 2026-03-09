import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import io
import warnings
import xlsxwriter

# -----------------------------------------------------------------------------
# 1. Configuration & Data Loading
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot') 
st.set_page_config(page_title="HAA Strategy Report", page_icon="📈", layout="wide")

# Ticker List
ALL_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "069500.KS", 
    "SSO", "UPRO", "QLD", "TQQQ", "UWM", "122630.KS", 
    "BIL", "SGOV", "SHV", "IEF", "TLT", "GOVT", "BND", 
    "TIP", "DBC", "VWO"
]

@st.cache_data(ttl=3600*24) 
def load_all_data_cached():
    # Download Data
    df = yf.download(ALL_TICKERS, start="2000-01-01", progress=False, auto_adjust=True)
    
    # Handle MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
            df = df['Close']
        elif df.columns.nlevels > 1:
            df = df.droplevel(0, axis=1)
            
    # [Important] Only ffill to preserve history for older tickers (e.g. BIL)
    return df.ffill()

# -----------------------------------------------------------------------------
# 2. Sidebar (Settings)
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Strategy Parameters")
    
    with st.expander("Asset Selection (Tickers)", expanded=True):
        ticker_risky_base = st.selectbox("Risky 1 (Base)", ["SPY", "QQQ", "IWM", "069500.KS"], index=0)
        ticker_risky_lev = st.selectbox("Risky 2 (Leverage)", ["UPRO", "TQQQ", "QLD", "122630.KS"], index=0)
        ticker_safe_cash = st.selectbox("Safe 1 (Cash)", ["BIL", "SGOV", "SHV"], index=0) 
        ticker_safe_bond = st.selectbox("Safe 2 (Bond)", ["IEF", "TLT", "BND"], index=0)
        ticker_canary = st.selectbox("Canary (Signal)", ["TIP", "DBC", "VWO"], index=0)
    
    with st.expander("Allocation & Capital", expanded=True):
        w_base = st.slider("Bull Market: Risky 1 Weight (%)", 0, 100, 30, step=5) / 100.0
        w_def_atk = st.slider("Bear Market: Risky 1 Hold (%)", 0, 100, 0, step=5) / 100.0
        initial_capital = st.number_input("Initial Capital (KRW)", value=100_000_000, step=1_000_000)
        start_date = st.date_input("Start Date", pd.to_datetime("2016-01-01"))
        
        st.divider()
        # [NEW] Tax Option
        apply_tax = st.checkbox("Apply 22% Tax (Annual)", value=True, help="Deduct 22% tax on annual realized gains > 2.5M KRW.")

# -----------------------------------------------------------------------------
# 3. Main Logic
# -----------------------------------------------------------------------------
full_df = load_all_data_cached()

st.title("🛡️ HAA Strategy Report")

# [1] Strategy Summary
with st.expander("📌 Strategy Overview", expanded=False):
    st.markdown(f"""
    **Hybrid Asset Allocation (HAA):**
    1. **Canary Signal:** Checks `{ticker_canary}` momentum. Score > 0 is **Bull**, Score < 0 is **Bear**.
    2. **Bull Market:** Buy Aggressive Assets (`{ticker_risky_base}` + `{ticker_risky_lev}`).
    3. **Bear Market:** Switch to Defensive Assets (`{ticker_safe_cash}` or `{ticker_safe_bond}`).
    4. **Momentum Score:** Weighted average of 1, 3, 6, 12-month returns.
    """)

if st.button("🚀 Run Simulation", type="primary", use_container_width=True):
    # Prepare Data
    needed = list(set([ticker_risky_base, ticker_risky_lev, ticker_safe_cash, ticker_safe_bond, ticker_canary]))
    
    df_selected = full_df[needed].copy()
    df_clean = df_selected.dropna() # Drop NaN only for selected tickers
    
    sim_start = pd.to_datetime(start_date)
    
    if df_clean.empty:
        st.error("Error: No overlapping data found for the selected tickers.")
        st.stop()
        
    first_valid_idx = df_clean.index[0]
    if sim_start < first_valid_idx:
        st.warning(f"⚠️ Data starts from {first_valid_idx.date()}. Simulation adapted.")
        sim_start = first_valid_idx
        
    df_price = df_clean.loc[sim_start:]
    
    if df_price.empty:
        st.error("Error: No data available for the selected range.")
        st.stop()
    
    # Score Calculation
    def get_score(series):
        return (series.pct_change(21)*12) + (series.pct_change(63)*4) + (series.pct_change(126)*2) + (series.pct_change(252)*1)

    scores = pd.DataFrame({t: get_score(df_clean[t]) for t in needed}, index=df_clean.index)
    scores = scores.loc[sim_start:] 
    
    df_ret = df_price.pct_change().fillna(0)
    
    # Backtest Loop
    cap = initial_capital
    b_cap = initial_capital
    equity, b_equity = [], []
    trade_logs = []
    
    curr_w = {ticker_safe_cash: 1.0}
    prev_mode = "Init"
    
    # Tax variables
    year_start_cap = initial_capital
    
    for i in range(len(df_price)):
        date = df_price.index[i]
        
        # [NEW] Tax Logic (Check at the start of a new year)
        if i > 0 and date.year != df_price.index[i-1].year:
            if apply_tax:
                # Profit made during the previous year
                year_profit = cap - year_start_cap
                if year_profit > 2_500_000:
                    tax_amount = (year_profit - 2_500_000) * 0.22
                    cap -= tax_amount
                    trade_logs.append({
                        "Date": date.strftime('%Y-%m-%d'),
                        "Mode": "Tax",
                        "Allocation": "Tax Payment (22%)",
                        "Balance": round(cap)
                    })
            # Reset year start capital (Mark-to-Market for next year's tax base)
            year_start_cap = cap

        if i == 0:
            equity.append(cap); b_equity.append(b_cap); continue

        # Check Signal (Previous Day)
        try:
            canary_score = scores[ticker_canary].iloc[i-1]
            base_score = scores[ticker_risky_base].iloc[i-1]
            cash_score = scores[ticker_safe_cash].iloc[i-1]
            bond_score = scores[ticker_safe_bond].iloc[i-1]
        except:
            equity.append(cap); b_equity.append(b_cap); continue
            
        # Determine Position
        target = {}
        mode = ""
        
        if canary_score > 0 and base_score > 0:
            mode = "Bull"
            target = {ticker_risky_base: w_base, ticker_risky_lev: 1.0 - w_base}
        else:
            mode = "Defense"
            if cash_score > 0 and bond_score > 0:
                s_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
            elif bond_score > 0:
                s_alloc = {ticker_safe_bond: 1.0}
            else:
                s_alloc = {ticker_safe_cash: 1.0}
            
            if w_def_atk > 0:
                target[ticker_risky_base] = w_def_atk
                for t, w in s_alloc.items(): target[t] = w * (1.0 - w_def_atk)
            else:
                target = s_alloc
        
        # Logging
        if mode != prev_mode or date.month != df_price.index[i-1].month:
            alloc_str = ", ".join([f"{t}({w:.0%})" for t, w in target.items() if w > 0])
            trade_logs.append({
                "Date": date.strftime('%Y-%m-%d'),
                "Mode": mode,
                "Allocation": alloc_str,
                "Balance": round(cap)
            })
            prev_mode = mode
            
        curr_w = target
        day_ret = sum(df_ret[t].iloc[i] * w for t, w in curr_w.items())
        
        cap *= (1 + day_ret)
        b_cap *= (1 + df_ret[ticker_risky_base].iloc[i])
        
        equity.append(cap)
        b_equity.append(b_cap)

    # DataFrame creation
    res = pd.DataFrame({'Strategy': equity, 'Benchmark': b_equity}, index=df_price.index[:len(equity)])
    
    # [2] Action Plan
    st.divider()
    
    # 전일장 종료 기준(-2)의 스코어를 가져와 오늘의 포지션을 결정합니다.
    last_canary = scores[ticker_canary].iloc[-2]
    last_base = scores[ticker_risky_base].iloc[-2]
    last_cash = scores[ticker_safe_cash].iloc[-2]
    last_bond = scores[ticker_safe_bond].iloc[-2]
    
    st.markdown("### 🔔 Action Plan (Today)")
    
    final_target = {}
    action_msg = ""
    msg_color = ""
    
    if last_canary > 0 and last_base > 0:
        final_target = {ticker_risky_base: w_base, ticker_risky_lev: 1.0 - w_base}
        action_msg = f"🚀 **Bull Market**: Buy Aggressive Assets."
        msg_color = "success"
    else:
        if last_cash > 0 and last_bond > 0: s_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
        elif last_bond > 0: s_alloc = {ticker_safe_bond: 1.0}
        else: s_alloc = {ticker_safe_cash: 1.0}
        
        if w_def_atk > 0:
            final_target[ticker_risky_base] = w_def_atk
            for t, w in s_alloc.items(): final_target[t] = w * (1.0 - w_def_atk)
        else:
            final_target = s_alloc
        action_msg = f"🛡️ **Defense Mode**: Move to Defensive Assets."
        msg_color = "warning"

    c1, c2 = st.columns([2, 1])
    with c1:
        if msg_color == "success": st.success(action_msg)
        else: st.warning(action_msg)
    with c2:
        st.markdown("**👇 Target Weights**")
        for t, w in final_target.items():
            if w > 0: st.markdown(f"- **{t}**: `{w*100:.1f}%`")

    # [3] Metrics
    st.divider()
    final_bal = res['Strategy'].iloc[-1]
    profit = final_bal - initial_capital
    total_yield = (profit / initial_capital) * 100
    
    days = (res.index[-1] - res.index[0]).days
    cagr = (final_bal / initial_capital) ** (365 / days) - 1
    
    res['peak'] = res['Strategy'].cummax()
    res['dd'] = (res['Strategy'] - res['peak']) / res['peak']
    mdd = res['dd'].min()
    
    st.subheader("📊 Simulation Results")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Final Balance", f"{final_bal:,.0f} KRW", f"+{profit:,.0f}")
    m2.metric("Total Return", f"{total_yield:.2f}%")
    m3.metric("CAGR", f"{cagr*100:.2f}%")
    m4.metric("MDD", f"{mdd*100:.2f}%")

    # [4] Tabs (Charts / Logs / Monthly)
    st.markdown("---")
    tab1, tab2, tab3 = st.tabs(["📈 Charts", "📝 Trade Logs", "📅 Monthly Returns"])
    
    with tab1:
        fig, ax = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})
        
        # 1. Equity Curve
        ax[0].plot(res.index, res['Strategy'], label='Strategy (Net)', color='#d62728', lw=2)
        ax[0].plot(res.index, res['Benchmark'], label=f'Benchmark ({ticker_risky_base})', color='gray', linestyle='--')
        ax[0].set_title("1. Cumulative Equity Curve (After Tax)")
        ax[0].legend()
        ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))
        ax[0].grid(True, alpha=0.3)
        
        # 2. MDD
        ax[1].fill_between(res.index, res['dd']*100, 0, color='blue', alpha=0.3, label='Strategy DD')
        b_peak = res['Benchmark'].cummax()
        b_dd = (res['Benchmark'] - b_peak) / b_peak
        ax[1].plot(res.index, b_dd*100, color='black', alpha=0.5, linestyle=':', label='Benchmark DD')
        ax[1].set_title("2. Drawdown Comparison (%)")
        ax[1].legend()
        ax[1].grid(True, alpha=0.3)

        # 3. Signal
        plot_scores = scores.reindex(res.index)
        ax[2].plot(plot_scores.index, plot_scores[ticker_canary], color='purple', label=f'Canary ({ticker_canary}) Score')
        ax[2].axhline(0, color='red', linestyle='--', linewidth=1.5, label='Threshold (0)')
        ax[2].fill_between(plot_scores.index, plot_scores[ticker_canary], 0, where=(plot_scores[ticker_canary] < 0), color='red', alpha=0.1)
        ax[2].fill_between(plot_scores.index, plot_scores[ticker_canary], 0, where=(plot_scores[ticker_canary] > 0), color='green', alpha=0.1)
        ax[2].set_title(f"3. Risk Signal ({ticker_canary})")
        ax[2].legend(loc='upper left')
        ax[2].grid(True, alpha=0.3)
        
        plt.tight_layout()
        st.pyplot(fig)
        
    with tab2:
        log_df = pd.DataFrame(trade_logs)
        st.dataframe(log_df, use_container_width=True, height=500)
        
    with tab3:
        m_ret = res['Strategy'].resample('M').last().pct_change().fillna(0)
        m_df = pd.DataFrame({'Return': m_ret})
        m_df['Year'] = m_df.index.year
        m_df['Month'] = m_df.index.month
        
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Return')
        
        y_ret = res['Strategy'].resample('Y').last().pct_change().fillna(0)
        if len(y_ret) > 0:
            first_val = res['Strategy'].iloc[0]
            first_year_end = res['Strategy'][res.index.year == res.index[0].year].iloc[-1]
            y_ret.iloc[0] = (first_year_end / first_val) - 1
            
        m_pivot['Total (Year)'] = y_ret.values
        
        cols = {i: pd.to_datetime(f"2000-{i}-01").strftime('%b') for i in range(1, 13)}
        m_pivot.rename(columns=cols, inplace=True)
        
        st.dataframe(
            m_pivot.style.background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1).format("{:.2%}"),
            use_container_width=True
        )

    # [5] Excel Download
    st.markdown("---")
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        res.to_excel(writer, sheet_name='Daily Data')
        if not log_df.empty:
            log_df.to_excel(writer, sheet_name='Trade Logs', index=False)
        m_pivot.to_excel(writer, sheet_name='Monthly Returns')
        
    excel_data = output.getvalue()
    
    st.download_button(
        label="📥 Download Excel Report",
        data=excel_data,
        file_name="HAA_Strategy_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )