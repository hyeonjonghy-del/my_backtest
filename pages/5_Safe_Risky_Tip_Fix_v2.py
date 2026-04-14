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

ALL_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "069500.KS",
    "SSO", "UPRO", "QLD", "TQQQ", "UWM", "122630.KS",
    "BIL", "SGOV", "SHV", "IEF", "TLT", "GOVT", "BND",
    "TIP", "DBC", "VWO"
]

@st.cache_data(ttl=3600*24)
def load_all_data_cached():
    df = yf.download(ALL_TICKERS, start="2000-01-01", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        if 'Close' in df.columns.levels[0]:
            df = df['Close']
        elif df.columns.nlevels > 1:
            df = df.droplevel(0, axis=1)
    return df.ffill()

# -----------------------------------------------------------------------------
# 2. Sidebar (Settings)
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Strategy Parameters")

    with st.expander("Asset Selection (Tickers)", expanded=True):
        ticker_risky_base = st.selectbox("Risky 1 (Base)", ["SPY", "QQQ", "IWM", "069500.KS"], index=0)
        ticker_risky_lev  = st.selectbox("Risky 2 (Leverage)", ["UPRO", "TQQQ", "QLD", "122630.KS"], index=0)
        ticker_safe_cash  = st.selectbox("Safe 1 (Cash)", ["BIL", "SGOV", "SHV"], index=0)
        ticker_safe_bond  = st.selectbox("Safe 2 (Bond)", ["IEF", "TLT", "BND"], index=0)
        ticker_canary     = st.selectbox("Canary (Signal)", ["TIP", "DBC", "VWO"], index=0)

    with st.expander("Allocation & Capital", expanded=True):
        w_base          = st.slider("Bull Market: Risky 1 Weight (%)", 0, 100, 30, step=5) / 100.0
        w_def_atk       = st.slider("Bear Market: Risky 1 Hold (%)", 0, 100, 0, step=5) / 100.0
        initial_capital = st.number_input("Initial Capital (KRW)", value=100_000_000, step=1_000_000)
        start_date      = st.date_input("Start Date", pd.to_datetime("2016-01-01"))

        st.divider()
        apply_tax = st.checkbox("Apply 22% Tax (Annual)", value=True,
                                help="Deduct 22% tax on annual realized gains > 2.5M KRW.")

# -----------------------------------------------------------------------------
# 3. Main Logic
# -----------------------------------------------------------------------------
full_df = load_all_data_cached()
st.title("🛡️ HAA Strategy Report")

with st.expander("📌 Strategy Overview", expanded=False):
    st.markdown(f"""
    **Hybrid Asset Allocation (HAA):**
    1. **Canary Signal:** `{ticker_canary}` 모멘텀 스코어 > 0 → Bull, < 0 → Bear
    2. **Bull Market:** 공격 자산 (`{ticker_risky_base}` + `{ticker_risky_lev}`) 매수
    3. **Bear Market:** 방어 자산 (`{ticker_safe_cash}` or `{ticker_safe_bond}`) 전환
    4. **Momentum Score:** 1·3·6·12개월 수익률 가중 평균
    5. **리밸런싱:** 매월 1회 (월 첫 거래일 신호 기준)
    """)

if st.button("🚀 Run Simulation", type="primary", use_container_width=True):

    needed = list(set([ticker_risky_base, ticker_risky_lev,
                       ticker_safe_cash, ticker_safe_bond, ticker_canary]))

    # -------------------------------------------------------------------------
    # [FIX 1] dropna() → 각 티커의 첫 유효일 이후부터만 사용
    # 기존: dropna()는 하나라도 NaN이면 전체 행 삭제 → BIL 같은 신생 ETF 선택 시
    #       데이터 기간이 과도하게 단축됨.
    # 수정: 선택된 티커 중 가장 늦은 첫 데이터 날짜(max of first_valid_index)를
    #       기준으로 시작점을 명시적으로 설정.
    # -------------------------------------------------------------------------
    df_selected = full_df[needed].copy()

    first_valid_per_ticker = df_selected.apply(lambda col: col.first_valid_index())
    data_start = max(first_valid_per_ticker)          # 모든 티커가 데이터를 가진 첫 날
    df_clean = df_selected.loc[data_start:].ffill()   # 그 이후 ffill만 적용

    sim_start = pd.to_datetime(start_date)
    if sim_start < data_start:
        st.warning(f"⚠️ 선택된 티커 중 가장 늦은 시작일은 {data_start.date()}입니다. 시뮬레이션 기간을 조정합니다.")
        sim_start = data_start

    df_price = df_clean.loc[sim_start:]

    if df_price.empty:
        st.error("Error: 선택된 기간에 유효한 데이터가 없습니다.")
        st.stop()

    # Score 계산 (df_clean 전체 기간 기준으로 계산 후 sim_start 이후만 사용)
    def get_score(series):
        return ((series.pct_change(21) * 12) +
                (series.pct_change(63) * 4) +
                (series.pct_change(126) * 2) +
                (series.pct_change(252) * 1))

    scores = pd.DataFrame({t: get_score(df_clean[t]) for t in needed}, index=df_clean.index)
    scores = scores.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)

    # -------------------------------------------------------------------------
    # Backtest Loop
    # -------------------------------------------------------------------------
    cap   = float(initial_capital)
    b_cap = float(initial_capital)
    equity, b_equity = [], []
    trade_logs = []

    # [FIX 2] 매월 1회 리밸런싱: 월이 바뀌는 첫 거래일에만 target 갱신
    # 기존: 매일 신호를 체크해 즉시 포지션 변경 → 로그는 월별처럼 보이지만
    #       실제로는 매일 리밸런싱되는 구조 (거래비용 과소평가).
    # 수정: curr_w를 월 첫 거래일에만 업데이트하고 나머지 날은 유지.
    curr_w    = {ticker_safe_cash: 1.0}
    prev_mode = "Init"

    # [FIX 3] 세금 첫 해 기준 수정
    # 기존: year_start_cap = initial_capital로 루프 밖에서 초기화
    #       → 시뮬레이션이 1월이 아닌 중간에 시작해도 첫 연말에 전체 기간 이익에 세금 부과.
    # 수정: sim_start 연도를 첫 해로 취급, year_start_cap을 sim_start 시점 자본으로 초기화.
    year_start_cap = cap

    for i in range(len(df_price)):
        date = df_price.index[i]

        # 세금 처리 (새 해 첫 거래일)
        if i > 0 and date.year != df_price.index[i - 1].year:
            if apply_tax:
                year_profit = cap - year_start_cap
                if year_profit > 2_500_000:
                    tax_amount = (year_profit - 2_500_000) * 0.22
                    cap -= tax_amount
                    trade_logs.append({
                        "Date": date.strftime('%Y-%m-%d'),
                        "Mode": "Tax",
                        "Allocation": f"세금 납부 22% ({tax_amount:,.0f} KRW)",
                        "Balance": round(cap)
                    })
            year_start_cap = cap  # 다음 해 기준 자본 리셋

        if i == 0:
            equity.append(cap)
            b_equity.append(b_cap)
            continue

        # [FIX 2 계속] 월 첫 거래일에만 신호 체크 및 리밸런싱
        is_new_month = (date.month != df_price.index[i - 1].month)

        if is_new_month:
            try:
                # 전일(월말) 종가 기준 스코어 사용
                canary_score = scores[ticker_canary].iloc[i - 1]
                base_score   = scores[ticker_risky_base].iloc[i - 1]
                cash_score   = scores[ticker_safe_cash].iloc[i - 1]
                bond_score   = scores[ticker_safe_bond].iloc[i - 1]
            except Exception:
                equity.append(cap)
                b_equity.append(b_cap)
                continue

            target = {}
            mode   = ""

            if canary_score > 0 and base_score > 0:
                mode   = "Bull"
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
                    for t, w in s_alloc.items():
                        target[t] = w * (1.0 - w_def_atk)
                else:
                    target = s_alloc

            # 모드 변경 또는 배분 변경 시 로그 기록
            alloc_str = ", ".join([f"{t}({w:.0%})" for t, w in target.items() if w > 0])
            if mode != prev_mode or target != curr_w:
                trade_logs.append({
                    "Date": date.strftime('%Y-%m-%d'),
                    "Mode": mode,
                    "Allocation": alloc_str,
                    "Balance": round(cap)
                })
            prev_mode = mode
            curr_w    = target  # 월 첫 거래일에만 갱신

        # 일별 수익 적용 (curr_w는 해당 월 내내 유지)
        day_ret = sum(df_ret[t].iloc[i] * w for t, w in curr_w.items() if t in df_ret.columns)
        cap   *= (1 + day_ret)
        b_cap *= (1 + df_ret[ticker_risky_base].iloc[i])

        equity.append(cap)
        b_equity.append(b_cap)

    res = pd.DataFrame(
        {'Strategy': equity, 'Benchmark': b_equity},
        index=df_price.index[:len(equity)]
    )

    # -------------------------------------------------------------------------
    # Action Plan (오늘 기준)
    # -------------------------------------------------------------------------
    st.divider()
    last_canary = scores[ticker_canary].iloc[-2]
    last_base   = scores[ticker_risky_base].iloc[-2]
    last_cash   = scores[ticker_safe_cash].iloc[-2]
    last_bond   = scores[ticker_safe_bond].iloc[-2]

    st.markdown("### 🔔 Action Plan (Today)")

    final_target = {}
    action_msg   = ""
    msg_color    = ""

    if last_canary > 0 and last_base > 0:
        final_target = {ticker_risky_base: w_base, ticker_risky_lev: 1.0 - w_base}
        action_msg   = "🚀 **Bull Market**: 공격 자산 매수"
        msg_color    = "success"
    else:
        if last_cash > 0 and last_bond > 0:
            s_alloc = {ticker_safe_cash: 0.5, ticker_safe_bond: 0.5}
        elif last_bond > 0:
            s_alloc = {ticker_safe_bond: 1.0}
        else:
            s_alloc = {ticker_safe_cash: 1.0}

        if w_def_atk > 0:
            final_target[ticker_risky_base] = w_def_atk
            for t, w in s_alloc.items():
                final_target[t] = w * (1.0 - w_def_atk)
        else:
            final_target = s_alloc
        action_msg = "🛡️ **Defense Mode**: 방어 자산으로 전환"
        msg_color  = "warning"

    c1, c2 = st.columns([2, 1])
    with c1:
        if msg_color == "success":
            st.success(action_msg)
        else:
            st.warning(action_msg)
    with c2:
        st.markdown("**👇 Target Weights**")
        for t, w in final_target.items():
            if w > 0:
                st.markdown(f"- **{t}**: `{w * 100:.1f}%`")

    # -------------------------------------------------------------------------
    # Metrics
    # -------------------------------------------------------------------------
    st.divider()
    final_bal   = res['Strategy'].iloc[-1]
    profit      = final_bal - initial_capital
    total_yield = (profit / initial_capital) * 100
    days        = (res.index[-1] - res.index[0]).days
    cagr        = (final_bal / initial_capital) ** (365 / days) - 1

    res['peak'] = res['Strategy'].cummax()
    res['dd']   = (res['Strategy'] - res['peak']) / res['peak']
    mdd         = res['dd'].min()

    st.subheader("📊 Simulation Results")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Final Balance", f"{final_bal:,.0f} KRW", f"+{profit:,.0f}")
    m2.metric("Total Return",  f"{total_yield:.2f}%")
    m3.metric("CAGR",          f"{cagr * 100:.2f}%")
    m4.metric("MDD",           f"{mdd * 100:.2f}%")

    # -------------------------------------------------------------------------
    # Tabs
    # -------------------------------------------------------------------------
    st.markdown("---")
    tab1, tab2, tab3 = st.tabs(["📈 Charts", "📝 Trade Logs", "📅 Monthly Returns"])

    with tab1:
        fig, ax = plt.subplots(3, 1, figsize=(12, 14), gridspec_kw={'height_ratios': [2, 1, 1]})

        ax[0].plot(res.index, res['Strategy'],  label='Strategy (Net)', color='#d62728', lw=2)
        ax[0].plot(res.index, res['Benchmark'], label=f'Benchmark ({ticker_risky_base})',
                   color='gray', linestyle='--')
        ax[0].set_title("1. Cumulative Equity Curve (After Tax)")
        ax[0].legend()
        ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))
        ax[0].grid(True, alpha=0.3)

        ax[1].fill_between(res.index, res['dd'] * 100, 0, color='blue', alpha=0.3, label='Strategy DD')
        b_peak = res['Benchmark'].cummax()
        b_dd   = (res['Benchmark'] - b_peak) / b_peak
        ax[1].plot(res.index, b_dd * 100, color='black', alpha=0.5, linestyle=':', label='Benchmark DD')
        ax[1].set_title("2. Drawdown Comparison (%)")
        ax[1].legend()
        ax[1].grid(True, alpha=0.3)

        plot_scores = scores.reindex(res.index)
        ax[2].plot(plot_scores.index, plot_scores[ticker_canary],
                   color='purple', label=f'Canary ({ticker_canary}) Score')
        ax[2].axhline(0, color='red', linestyle='--', linewidth=1.5, label='Threshold (0)')
        ax[2].fill_between(plot_scores.index, plot_scores[ticker_canary], 0,
                           where=(plot_scores[ticker_canary] < 0), color='red',   alpha=0.1)
        ax[2].fill_between(plot_scores.index, plot_scores[ticker_canary], 0,
                           where=(plot_scores[ticker_canary] > 0), color='green', alpha=0.1)
        ax[2].set_title(f"3. Risk Signal ({ticker_canary})")
        ax[2].legend(loc='upper left')
        ax[2].grid(True, alpha=0.3)

        plt.tight_layout()
        st.pyplot(fig)

    with tab2:
        log_df = pd.DataFrame(trade_logs)
        st.dataframe(log_df, use_container_width=True, height=500)

    with tab3:
        m_ret = res['Strategy'].resample('ME').last().pct_change().fillna(0)
        m_df  = pd.DataFrame({'Return': m_ret})
        m_df['Year']  = m_df.index.year
        m_df['Month'] = m_df.index.month
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Return')

        # [FIX 4] 연간 수익률 연도 매핑 오류 수정
        # 기존: y_ret.values를 index 없이 덮어씌워 연도-수익률이 한 칸씩 밀리는 버그.
        # 수정: 연도(year)를 key로 직접 매핑.
        y_ret_series = res['Strategy'].resample('YE').last().pct_change()

        # 첫 해 수익률 보정 (pct_change는 첫 행을 NaN으로 반환)
        first_year     = res.index[0].year
        first_val      = res['Strategy'].iloc[0]
        first_year_end = res['Strategy'][res.index.year == first_year].iloc[-1]
        y_ret_series.iloc[0] = (first_year_end / first_val) - 1

        # 연도 기준으로 안전하게 매핑 (행 수 불일치 방지)
        m_pivot['Total (Year)'] = m_pivot.index.map(
            lambda yr: (
                y_ret_series[y_ret_series.index.year == yr].iloc[0]
                if any(y_ret_series.index.year == yr) else None
            )
        )

        cols = {i: pd.to_datetime(f"2000-{i}-01").strftime('%b') for i in range(1, 13)}
        m_pivot.rename(columns=cols, inplace=True)

        st.dataframe(
            m_pivot.style
                   .background_gradient(cmap='RdYlGn', axis=None, vmin=-0.1, vmax=0.1)
                   .format("{:.2%}"),
            use_container_width=True
        )

    # -------------------------------------------------------------------------
    # Excel Download
    # -------------------------------------------------------------------------
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