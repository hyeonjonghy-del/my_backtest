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
st.set_page_config(page_title="HAA Enhanced Strategy", page_icon="📈", layout="wide")

# 캐너리 후보 3개 고정 (방향 1: 복수 캐너리)
CANARY_TICKERS = ["TIP", "DBC", "VWO"]

# Bull 후보 Base 3개 (방향 2: 모멘텀 상위 자산 선택)
RISKY_BASE_CANDIDATES = ["SPY", "QQQ", "IWM"]

ALL_TICKERS = list(set(
    CANARY_TICKERS + RISKY_BASE_CANDIDATES +
    ["SPY", "QQQ", "IWM", "DIA", "069500.KS",
     "SSO", "UPRO", "QLD", "TQQQ", "UWM", "122630.KS",
     "BIL", "SGOV", "SHV", "IEF", "TLT", "GOVT", "BND",
     "TIP", "DBC", "VWO"]
))

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

    with st.expander("🐦 Canary Signal (방향 1)", expanded=True):
        st.markdown("TIP / DBC / VWO 3개 중 양수 개수로 Bull/Bear 판단")
        canary_threshold = st.slider(
            "Bull 진입 최소 캐너리 양수 개수", 1, 3, 2,
            help="2 권장: 3개 중 2개 이상 양수일 때만 Bull 진입"
        )

    with st.expander("🚀 Bull Market Assets (방향 2)", expanded=True):
        st.markdown("SPY / QQQ / IWM 중 모멘텀 1위 자산 자동 선택")
        ticker_risky_lev = st.selectbox("Leverage ETF", ["UPRO", "TQQQ", "QLD", "SSO", "122630.KS"], index=0)
        w_base = st.slider("Base Weight (비레버리지 비중 %)", 0, 100, 30, step=5) / 100.0

    with st.expander("🛡️ Defense Assets (방향 3)", expanded=True):
        ticker_safe_cash = st.selectbox("Safe 1 (Cash)", ["BIL", "SGOV", "SHV"], index=0)
        ticker_safe_bond = st.selectbox("Safe 2 (Bond)", ["IEF", "TLT", "BND"], index=0)
        w_def_atk = st.slider("Bear: 공격자산 잔존 비중 (%)", 0, 100, 0, step=5) / 100.0

    with st.expander("⚡ Dynamic Leverage (방향 4)", expanded=True):
        use_dynamic_lev = st.checkbox("변동성 기반 레버리지 비중 자동 조절", value=True)
        vol_window = st.slider("변동성 측정 기간 (일)", 10, 60, 20, step=5)
        st.markdown("""
        | 연율화 변동성 | 레버리지 비중 |
        |---|---|
        | < 12% | 80% |
        | 12~18% | 60% |
        | 18~25% | 40% |
        | > 25% | 20% |
        """)

    with st.expander("💰 Capital & Period", expanded=True):
        initial_capital = st.number_input("초기 자본 (KRW)", value=100_000_000, step=1_000_000)
        start_date      = st.date_input("시작일", pd.to_datetime("2016-01-01"))
        apply_tax       = st.checkbox("연 22% 세금 적용 (공제 250만원)", value=True)

# -----------------------------------------------------------------------------
# 3. Main Logic
# -----------------------------------------------------------------------------
full_df = load_all_data_cached()
st.title("🛡️ HAA Enhanced Strategy Report")

with st.expander("📌 Strategy Overview", expanded=False):
    st.markdown(f"""
    **개선된 HAA 전략 4가지 핵심 변경점:**

    1. **🐦 복수 캐너리 신호 (방향 1)**
       - TIP / DBC / VWO 3개 중 **{canary_threshold}개 이상 양수** → Bull
       - 단일 캐너리보다 오신호 대폭 감소

    2. **🚀 Bull 구간 동적 자산 선택 (방향 2)**
       - SPY / QQQ / IWM 중 **모멘텀 스코어 1위** 자산 자동 선택
       - 선택된 Base ETF에 맞는 레버리지 ETF({ticker_risky_lev}) 연동

    3. **🛡️ Defense 구간 세분화 (방향 3)**
       - 강한 Bear (캐너리 음수 2개 이상): {ticker_safe_cash} 100%
       - 약한 Bear (캐너리 음수 1개): {ticker_safe_bond} 50% + {ticker_safe_cash} 50%
       - 불확실 구간 (경계): 공격자산 소량 + 현금 혼합

    4. **⚡ 동적 레버리지 조절 (방향 4)**
       - {'✅ 활성화' if use_dynamic_lev else '❌ 비활성화'}: 최근 {vol_window}일 변동성으로 레버리지 비중 자동 조절
       - 변동성 폭등 시 레버리지 자동 축소 → Volatility Decay 방어
    """)

if st.button("🚀 Run Enhanced Simulation", type="primary", use_container_width=True):

    # 필요한 티커 목록
    needed = list(set(
        CANARY_TICKERS + RISKY_BASE_CANDIDATES +
        [ticker_risky_lev, ticker_safe_cash, ticker_safe_bond]
    ))

    # -------------------------------------------------------------------------
    # [FIX 1] 데이터 시작일 명시적 계산 (dropna 방지)
    # -------------------------------------------------------------------------
    df_selected = full_df[[t for t in needed if t in full_df.columns]].copy()
    missing = [t for t in needed if t not in full_df.columns]
    if missing:
        st.warning(f"⚠️ 다음 티커 데이터를 찾을 수 없습니다: {missing}")

    first_valid_per_ticker = df_selected.apply(lambda col: col.first_valid_index())
    data_start = max(first_valid_per_ticker)
    df_clean   = df_selected.loc[data_start:].ffill()

    sim_start = pd.to_datetime(start_date)
    if sim_start < data_start:
        st.warning(f"⚠️ 데이터 시작일 {data_start.date()}로 조정됩니다.")
        sim_start = data_start

    df_price = df_clean.loc[sim_start:]
    if df_price.empty:
        st.error("Error: 선택된 기간에 유효한 데이터가 없습니다.")
        st.stop()

    # -------------------------------------------------------------------------
    # 모멘텀 스코어 계산
    # -------------------------------------------------------------------------
    def get_score(series):
        return ((series.pct_change(21) * 12) +
                (series.pct_change(63) * 4)  +
                (series.pct_change(126) * 2) +
                (series.pct_change(252) * 1))

    all_needed = [t for t in needed if t in df_clean.columns]
    scores = pd.DataFrame({t: get_score(df_clean[t]) for t in all_needed}, index=df_clean.index)
    scores = scores.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)

    # -------------------------------------------------------------------------
    # [방향 4] 변동성 계산 함수
    # -------------------------------------------------------------------------
    def get_dynamic_lev_weight(base_ticker, idx_pos, base_w):
        """변동성에 따라 레버리지 비중을 동적으로 조절"""
        if not use_dynamic_lev:
            return base_w  # 동적 조절 비활성화 시 기본값 유지

        if idx_pos < vol_window:
            return base_w

        recent_rets = df_ret[base_ticker].iloc[idx_pos - vol_window:idx_pos]
        vol_annual  = recent_rets.std() * (252 ** 0.5)

        if vol_annual < 0.12:
            lev_w = 0.80
        elif vol_annual < 0.18:
            lev_w = 0.60
        elif vol_annual < 0.25:
            lev_w = 0.40
        else:
            lev_w = 0.20

        return 1.0 - lev_w  # base_w 자리에 반환 (레버리지 비중의 역)

    # -------------------------------------------------------------------------
    # Backtest Loop
    # -------------------------------------------------------------------------
    cap   = float(initial_capital)
    b_cap = float(initial_capital)  # 벤치마크: SPY
    equity, b_equity = [], []
    trade_logs = []

    curr_w         = {ticker_safe_cash: 1.0}
    prev_mode      = "Init"
    year_start_cap = cap

    for i in range(len(df_price)):
        date = df_price.index[i]

        # 세금 처리
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
                        "Balance": round(cap),
                        "Vol(Ann)": "-",
                        "Canary+": "-"
                    })
            year_start_cap = cap

        if i == 0:
            equity.append(cap)
            b_equity.append(b_cap)
            continue

        # [FIX 2] 월 첫 거래일에만 신호 체크 및 리밸런싱
        is_new_month = (date.month != df_price.index[i - 1].month)

        if is_new_month:
            try:
                # ─── [방향 1] 복수 캐너리 신호 ───────────────────────────
                canary_scores_now = {t: scores[t].iloc[i - 1] for t in CANARY_TICKERS if t in scores.columns}
                canary_positive   = sum(1 for v in canary_scores_now.values() if v > 0)
                is_bull           = (canary_positive >= canary_threshold)

                # ─── [방향 2] Bull 구간 모멘텀 상위 Base 자산 선택 ────────
                base_scores_now = {
                    t: scores[t].iloc[i - 1]
                    for t in RISKY_BASE_CANDIDATES if t in scores.columns
                }
                best_base = max(base_scores_now, key=base_scores_now.get)
                best_base_score = base_scores_now[best_base]

                cash_score = scores[ticker_safe_cash].iloc[i - 1] if ticker_safe_cash in scores.columns else 0
                bond_score = scores[ticker_safe_bond].iloc[i - 1] if ticker_safe_bond in scores.columns else 0

            except Exception:
                equity.append(cap)
                b_equity.append(b_cap)
                continue

            target = {}
            mode   = ""

            if is_bull and best_base_score > 0:
                mode = "Bull"

                # ─── [방향 4] 동적 레버리지 비중 계산 ───────────────────
                if use_dynamic_lev and i >= vol_window:
                    recent_rets = df_ret[best_base].iloc[i - vol_window:i]
                    vol_annual  = recent_rets.std() * (252 ** 0.5)
                    if vol_annual < 0.12:   dyn_lev_w = 0.80
                    elif vol_annual < 0.18: dyn_lev_w = 0.60
                    elif vol_annual < 0.25: dyn_lev_w = 0.40
                    else:                   dyn_lev_w = 0.20
                    dyn_base_w = 1.0 - dyn_lev_w
                else:
                    dyn_base_w = w_base
                    dyn_lev_w  = 1.0 - w_base
                    vol_annual = None

                target = {
                    best_base:       dyn_base_w,
                    ticker_risky_lev: dyn_lev_w
                }

            else:
                # ─── [방향 3] Defense 구간 세분화 ────────────────────────
                # 캐너리 양수 개수로 방어 강도 결정
                if canary_positive == 0:
                    # 강한 Bear: 전원 음수 → 현금 100%
                    mode    = "Defense (Strong)"
                    s_alloc = {ticker_safe_cash: 1.0}
                elif canary_positive == 1:
                    # 약한 Bear: 1개만 양수 → 채권/현금 50:50
                    mode    = "Defense (Weak)"
                    if bond_score > 0:
                        s_alloc = {ticker_safe_bond: 0.5, ticker_safe_cash: 0.5}
                    else:
                        s_alloc = {ticker_safe_cash: 1.0}
                else:
                    # 불확실 구간: 임계값 미달이지만 대부분 양수
                    # (canary_threshold=3일 때 2개 양수인 경우)
                    mode    = "Defense (Uncertain)"
                    s_alloc = {ticker_safe_bond: 0.7, ticker_safe_cash: 0.3}

                vol_annual = None

                if w_def_atk > 0:
                    target[best_base] = w_def_atk
                    for t, w in s_alloc.items():
                        target[t] = w * (1.0 - w_def_atk)
                else:
                    target = s_alloc

            # 로그 기록
            alloc_str  = ", ".join([f"{t}({w:.0%})" for t, w in target.items() if w > 0])
            vol_str    = f"{vol_annual:.1%}" if vol_annual is not None else "-"
            canary_str = f"{canary_positive}/{len(CANARY_TICKERS)}"

            if mode != prev_mode or target != curr_w:
                trade_logs.append({
                    "Date":      date.strftime('%Y-%m-%d'),
                    "Mode":      mode,
                    "Canary+":   canary_str,
                    "Best Base": best_base if is_bull else "-",
                    "Vol(Ann)":  vol_str,
                    "Allocation": alloc_str,
                    "Balance":   round(cap)
                })

            prev_mode = mode
            curr_w    = target

        # 일별 수익 적용
        day_ret = sum(
            df_ret[t].iloc[i] * w
            for t, w in curr_w.items()
            if t in df_ret.columns
        )
        cap   *= (1 + day_ret)
        b_cap *= (1 + df_ret['SPY'].iloc[i])  # 벤치마크 SPY 고정

        equity.append(cap)
        b_equity.append(b_cap)

    res = pd.DataFrame(
        {'Strategy': equity, 'Benchmark (SPY)': b_equity},
        index=df_price.index[:len(equity)]
    )

    # -------------------------------------------------------------------------
    # Action Plan (오늘 기준)
    # -------------------------------------------------------------------------
    st.divider()
    st.markdown("### 🔔 Action Plan (Today)")

    # 오늘 신호 계산
    last_canary_scores  = {t: scores[t].iloc[-2] for t in CANARY_TICKERS if t in scores.columns}
    last_canary_pos     = sum(1 for v in last_canary_scores.values() if v > 0)
    last_is_bull        = (last_canary_pos >= canary_threshold)

    last_base_scores    = {t: scores[t].iloc[-2] for t in RISKY_BASE_CANDIDATES if t in scores.columns}
    last_best_base      = max(last_base_scores, key=last_base_scores.get)
    last_best_score     = last_base_scores[last_best_base]

    last_cash_score = scores[ticker_safe_cash].iloc[-2] if ticker_safe_cash in scores.columns else 0
    last_bond_score = scores[ticker_safe_bond].iloc[-2] if ticker_safe_bond in scores.columns else 0

    # 캐너리 상태 표시
    canary_status_cols = st.columns(len(CANARY_TICKERS))
    for idx, t in enumerate(CANARY_TICKERS):
        sc = last_canary_scores.get(t, 0)
        with canary_status_cols[idx]:
            color = "🟢" if sc > 0 else "🔴"
            st.metric(f"{color} {t}", f"{sc:.3f}", delta="양수" if sc > 0 else "음수",
                      delta_color="normal" if sc > 0 else "inverse")

    st.caption(f"캐너리 양수: {last_canary_pos}/{len(CANARY_TICKERS)} (진입 기준: {canary_threshold}개 이상)")

    final_target = {}
    action_msg   = ""
    msg_color    = ""

    if last_is_bull and last_best_score > 0:
        # 오늘의 동적 레버리지
        if use_dynamic_lev and len(df_ret) >= vol_window:
            today_vol = df_ret[last_best_base].iloc[-vol_window:].std() * (252 ** 0.5)
            if today_vol < 0.12:   t_lev_w = 0.80
            elif today_vol < 0.18: t_lev_w = 0.60
            elif today_vol < 0.25: t_lev_w = 0.40
            else:                  t_lev_w = 0.20
            t_base_w = 1.0 - t_lev_w
        else:
            t_base_w = w_base
            t_lev_w  = 1.0 - w_base

        final_target = {last_best_base: t_base_w, ticker_risky_lev: t_lev_w}
        action_msg   = f"🚀 **Bull Market**: {last_best_base} (모멘텀 1위) + {ticker_risky_lev}"
        msg_color    = "success"
    else:
        if last_canary_pos == 0:
            s_alloc  = {ticker_safe_cash: 1.0}
            action_msg = f"🛡️ **Strong Defense**: 전 캐너리 음수 → 현금 100%"
        elif last_canary_pos == 1:
            s_alloc  = {ticker_safe_bond: 0.5, ticker_safe_cash: 0.5} if last_bond_score > 0 else {ticker_safe_cash: 1.0}
            action_msg = f"🛡️ **Weak Defense**: 채권/현금 혼합"
        else:
            s_alloc  = {ticker_safe_bond: 0.7, ticker_safe_cash: 0.3}
            action_msg = f"⚠️ **Uncertain**: 불확실 구간 → 채권 위주"

        if w_def_atk > 0:
            final_target[last_best_base] = w_def_atk
            for t, w in s_alloc.items():
                final_target[t] = w * (1.0 - w_def_atk)
        else:
            final_target = s_alloc
        msg_color = "warning"

    c1, c2 = st.columns([2, 1])
    with c1:
        if msg_color == "success": st.success(action_msg)
        else:                       st.warning(action_msg)
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

    # 샤프 비율 계산
    daily_rets  = res['Strategy'].pct_change().dropna()
    sharpe      = (daily_rets.mean() / daily_rets.std()) * (252 ** 0.5)

    st.subheader("📊 Simulation Results")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Final Balance",  f"{final_bal:,.0f} KRW", f"+{profit:,.0f}")
    m2.metric("Total Return",   f"{total_yield:.2f}%")
    m3.metric("CAGR",           f"{cagr * 100:.2f}%")
    m4.metric("MDD",            f"{mdd * 100:.2f}%")
    m5.metric("Sharpe Ratio",   f"{sharpe:.2f}")

    # -------------------------------------------------------------------------
    # Tabs
    # -------------------------------------------------------------------------
    st.markdown("---")
    tab1, tab2, tab3 = st.tabs(["📈 Charts", "📝 Trade Logs", "📅 Monthly Returns"])

    with tab1:
        fig, ax = plt.subplots(4, 1, figsize=(12, 18),
                               gridspec_kw={'height_ratios': [2, 1, 1, 1]})

        # 1. Equity Curve
        ax[0].plot(res.index, res['Strategy'],        label='Strategy (Net)', color='#d62728', lw=2)
        ax[0].plot(res.index, res['Benchmark (SPY)'], label='Benchmark (SPY)', color='gray', linestyle='--')
        ax[0].set_title("1. Cumulative Equity Curve (After Tax)")
        ax[0].legend()
        ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))
        ax[0].grid(True, alpha=0.3)

        # 2. MDD
        ax[1].fill_between(res.index, res['dd'] * 100, 0, color='blue', alpha=0.3, label='Strategy DD')
        b_peak = res['Benchmark (SPY)'].cummax()
        b_dd   = (res['Benchmark (SPY)'] - b_peak) / b_peak
        ax[1].plot(res.index, b_dd * 100, color='black', alpha=0.5, linestyle=':', label='Benchmark DD')
        ax[1].set_title("2. Drawdown Comparison (%)")
        ax[1].legend()
        ax[1].grid(True, alpha=0.3)

        # 3. 복수 캐너리 신호 (양수 개수)
        canary_pos_series = pd.Series(index=scores.index, dtype=float)
        for idx in scores.index:
            cnt = sum(1 for t in CANARY_TICKERS if t in scores.columns and scores.loc[idx, t] > 0)
            canary_pos_series[idx] = cnt
        canary_plot = canary_pos_series.reindex(res.index)

        ax[2].fill_between(canary_plot.index, canary_plot, 0, alpha=0.4,
                           color='green', label='Canary Positive Count')
        ax[2].axhline(canary_threshold, color='red', linestyle='--', lw=1.5,
                      label=f'Bull Threshold ({canary_threshold})')
        ax[2].set_yticks([0, 1, 2, 3])
        ax[2].set_title("3. Canary Signal (Positive Count / 3)")
        ax[2].legend(loc='upper left')
        ax[2].grid(True, alpha=0.3)

        # 4. 동적 레버리지 비중 (변동성)
        if use_dynamic_lev and 'SPY' in df_ret.columns:
            vol_series = df_ret['SPY'].rolling(vol_window).std() * (252 ** 0.5)
            vol_plot   = vol_series.reindex(res.index)
            ax[3].plot(vol_plot.index, vol_plot * 100, color='orange', lw=1.5, label=f'SPY {vol_window}d Vol (%)')
            ax[3].axhline(12, color='green', linestyle=':', lw=1, alpha=0.7, label='12% (Lev 80%)')
            ax[3].axhline(18, color='gold',  linestyle=':', lw=1, alpha=0.7, label='18% (Lev 60%)')
            ax[3].axhline(25, color='red',   linestyle=':', lw=1, alpha=0.7, label='25% (Lev 20%)')
            ax[3].set_title(f"4. Realized Volatility & Leverage Threshold")
            ax[3].legend(loc='upper left', fontsize=8)
            ax[3].grid(True, alpha=0.3)
        else:
            ax[3].set_visible(False)

        plt.tight_layout()
        st.pyplot(fig)

    with tab2:
        log_df = pd.DataFrame(trade_logs)
        st.dataframe(log_df, use_container_width=True, height=500)

    with tab3:
        m_ret   = res['Strategy'].resample('ME').last().pct_change().fillna(0)
        m_df    = pd.DataFrame({'Return': m_ret})
        m_df['Year']  = m_df.index.year
        m_df['Month'] = m_df.index.month
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Return')

        # 연간 수익률 연도 매핑 (FIX 4 유지)
        y_ret_series = res['Strategy'].resample('YE').last().pct_change()
        first_year   = res.index[0].year
        first_val    = res['Strategy'].iloc[0]
        first_yr_end = res['Strategy'][res.index.year == first_year].iloc[-1]
        y_ret_series.iloc[0] = (first_yr_end / first_val) - 1

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
        file_name="HAA_Enhanced_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )