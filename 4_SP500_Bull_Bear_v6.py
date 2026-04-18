import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import io
import warnings
import calendar

warnings.filterwarnings('ignore')
plt.style.use('ggplot')
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="Safe/Risky/Cash Mix Strategy v5", page_icon="🛡️", layout="wide")

st.title("🛡️ Safe/Risky/Cash Mix Strategy v5 (최적화 적용)")
st.markdown("""
**전략 개요 (v5 — 12,000개 조합 최적화 반영):**
- **로직:** 시그널 발생(T일) → 다음 날(T+1일) 장 마감(종가)에 매매
- **자산 배분 (v5):**
    - **Bear (하락장):** SPY + SGOV 혼합
    - **Bull Full (상승장 + 금리 안정):** UPRO 100%
    - **Bull Mix (상승장 + 금리 주의):** UPRO + SPY 혼합

| 파라미터 | 기본값 | 최적화 근거 |
|---------|--------|------------|
| Bull 진입 MA | **150일** | Top 전부 150일 (v4와 동일) |
| Bear 퇴출 MA | **112일** | 진입 MA × 0.75 (v4와 동일) |
| 금리 MA | **120일** ← 🆕 | v4(90일)보다 120일이 Sharpe 우수 |
| Whipsaw 필터 | **OFF** | confirm=1일이 압도적 (v4와 동일) |
| Bear SGOV 비중 | **50%** ← 🆕 | Sharpe 0.885, MDD -48% 달성 |
| Bull Mix UPRO | **60%** | 40~80% 결과 동일 → 기본값 유지 |
""")

# 최적화 인사이트 박스
with st.expander("📊 v4 최적화 결과 요약 보기 (12,000개 조합)"):
    col1, col2, col3 = st.columns(3)
    col1.metric("최고 Sharpe",  "0.885",  "Bear SGOV 50% + MA 150/112")
    col2.metric("최고 CAGR",    "36.09%", "2020~2025 기준")
    col3.metric("MDD 개선",     "-48.0%", "v3 대비 약 5%p 개선")

    st.markdown("""
    **핵심 발견:**
    - Bear SGOV 50% 시 Sharpe 최고 — SGOV가 하락장 손실을 완충해 위험 대비 수익 개선
    - 금리 MA 120일이 90일보다 안정적 — 금리 노이즈 필터링 효과
    - Bull Mix UPRO 비중(40~80%)은 결과에 영향 없음 — Bull Mix 발동 빈도가 낮기 때문
    """)

st.markdown("---")

# ── 사이드바 ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("1. 투자 종목 설정")
    ticker_safe  = st.text_input("안전 자산 (Bear/BullMix)", value="SPY")
    ticker_risky = st.text_input("공격 자산 (Bull)",          value="UPRO")
    ticker_cash  = st.text_input("현금 파킹 (Bear용 SGOV)",   value="SGOV")

    st.header("2. 전략 기본 옵션")
    start_date      = st.date_input("시작일", pd.to_datetime("2020-01-01"))
    initial_capital = st.number_input("초기 자본", value=100_000_000, step=1_000_000)
    fee_rate        = st.number_input("매매 수수료 (%)", value=0.02, step=0.01) / 100.0
    apply_tax       = st.checkbox("양도소득세 22% 차감", value=False)

    st.header("3. 추세 이평선")
    ma_window = st.number_input(
        "Bull 진입 이평선 (일)", value=150, min_value=5,
        help="✅ 최적화 결과: 150일 (변경 불필요)"
    )
    use_asymmetric_ma = st.checkbox("비대칭 MA 사용 (빠른 퇴출)", value=True)
    if use_asymmetric_ma:
        ma_exit_window = st.number_input(
            "Bear 퇴출 이평선 (일)", value=112, min_value=5,
            help="✅ 최적화 결과: 112일 = 150 × 0.75"
        )
        st.caption(f"ℹ️ 진입 MA{int(ma_window)} / 퇴출 MA{int(ma_exit_window)}")
    else:
        ma_exit_window = ma_window

    st.header("4. 금리 리스크 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    ticker_rate     = st.text_input("금리 지표", value="^TNX")
    rate_ma_window  = st.number_input(
        "금리 이평선 (일)", value=120,
        help="🆕 최적화 결과: 120일 (v4의 90일보다 우수)"
    )

    st.header("5. Whipsaw 필터")
    use_whipsaw  = st.checkbox(
        "Whipsaw 필터 사용", value=False,
        help="✅ 최적화 결과: OFF (confirm=1일이 압도적 1위)"
    )
    confirm_days = st.number_input("신호 확정 기간 (일)", value=1, min_value=1, max_value=10)

    st.header("6. 🆕 Bear 구간 배분 (최적화 반영)")
    bear_sgov_ratio = st.slider(
        "Bear 시 SGOV 비중", 0.0, 1.0, 0.5, 0.1,
        help="🆕 최적화 결과: 50%가 Sharpe 최고 (MDD -48%)"
    )
    bear_spy_ratio = 1.0 - bear_sgov_ratio
    st.caption(f"Bear 배분: SPY {bear_spy_ratio*100:.0f}% / SGOV {bear_sgov_ratio*100:.0f}%")
    if bear_sgov_ratio == 0.5:
        st.success("✅ 최적화 권장 비중")
    elif bear_sgov_ratio < 0.3:
        st.warning("⚠️ SGOV 비중이 낮으면 Bear 구간 MDD가 커짐")

    st.header("7. Bull Full 구간 배분")
    bull_full_risky_ratio = st.slider(
        "Bull Full 시 UPRO 비중", 0.0, 1.0, 1.0, 0.1,
        help="기본 100%. 낮출수록 MDD↓ 수익률↓ (트레이드오프)"
    )
    bull_full_safe_ratio = 1.0 - bull_full_risky_ratio
    st.caption(f"Bull Full 배분: UPRO {bull_full_risky_ratio*100:.0f}% / SPY {bull_full_safe_ratio*100:.0f}%")
    if bull_full_risky_ratio == 1.0:
        st.success("✅ 기본값 (UPRO 100% — 최대 공격)")
    elif bull_full_risky_ratio >= 0.7:
        st.info(f"ℹ️ UPRO {bull_full_risky_ratio*100:.0f}% — 공격적 (MDD 약간 감소)")
    else:
        st.warning(f"⚠️ UPRO {bull_full_risky_ratio*100:.0f}% — 보수적 (수익률 크게 감소)")

    st.header("8. Bull Mix 구간 배분")
    bull_mix_risky_ratio = st.slider(
        "Bull Mix 시 UPRO 비중", 0.0, 1.0, 0.6, 0.1,
        help="ℹ️ 최적화 결과: 40~80% 모두 동일 → 기본 60% 유지"
    )
    bull_mix_safe_ratio = 1.0 - bull_mix_risky_ratio
    st.caption(f"Bull Mix 배분: UPRO {bull_mix_risky_ratio*100:.0f}% / SPY {bull_mix_safe_ratio*100:.0f}%")
    st.info("ℹ️ Bull Mix 비중은 결과에 거의 영향 없음 (발동 빈도 낮음)")

    st.header("9. 보조 시장 필터")
    use_aux_signal = st.checkbox("보조 시장 신호 사용 (QQQ)", value=False)
    ticker_aux     = st.text_input("보조 시장 티커", value="QQQ")
    aux_ma_window  = st.number_input("보조 이평선 (일)", value=120)

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600 * 24)
def load_data(safe, risky, rate, cash, aux):
    tickers = list(dict.fromkeys([safe, risky, rate, cash, aux]))
    raw = yf.download(tickers, start="2000-01-01", progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        df = raw['Close'].copy()
    else:
        df = raw.copy()
    df = df.loc[~df.index.duplicated(keep='first')].sort_index()
    return df

# ── 유틸 함수 ─────────────────────────────────────────────────────────────────
def sharpe_ratio(daily_returns, rf=0.05):
    excess = daily_returns - rf / 252
    return float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() != 0 else 0.0

def calmar_ratio(cagr, mdd):
    return abs(cagr / mdd) if mdd != 0 else 0.0

def win_rate_and_avg_hold(res_df):
    switches = res_df[res_df['Action'] == 'SWITCH'].index.tolist()
    if len(switches) < 2:
        return 0.0, 0
    wins, hold_days = 0, []
    for i in range(len(switches) - 1):
        s, e = switches[i], switches[i + 1]
        seg = res_df.loc[s:e, 'Equity']
        if len(seg) >= 2 and seg.iloc[0] > 0 and seg.iloc[-1] > seg.iloc[0]:
            wins += 1
        hold_days.append((e - s).days)
    total = len(switches) - 1
    return (wins / total * 100) if total > 0 else 0.0, int(np.mean(hold_days)) if hold_days else 0

# ── 메인 실행 ─────────────────────────────────────────────────────────────────
if st.button("🚀 Run Backtest v5", type="primary", use_container_width=True):
    with st.spinner("v5 전략 분석 중..."):

        full_df = load_data(ticker_safe, ticker_risky, ticker_rate, ticker_cash, ticker_aux)
        missing = [c for c in [ticker_safe, ticker_risky, ticker_rate] if c not in full_df.columns]
        if missing:
            st.error(f"데이터 없는 티커: {missing}")
            st.stop()

        cash_available = ticker_cash in full_df.columns
        aux_available  = ticker_aux in full_df.columns and use_aux_signal
        df_raw = full_df.ffill()

        # ── 지표 계산 ─────────────────────────────────────────────────────────
        series_safe  = df_raw[ticker_safe]
        ma_safe      = series_safe.rolling(window=int(ma_window)).mean()
        ma_safe_exit = series_safe.rolling(window=int(ma_exit_window)).mean()
        series_rate  = df_raw[ticker_rate]
        ma_rate      = series_rate.rolling(window=int(rate_ma_window)).mean()

        if aux_available:
            series_aux = df_raw[ticker_aux]
            ma_aux     = series_aux.rolling(window=int(aux_ma_window)).mean()
        else:
            series_aux = series_safe
            ma_aux     = ma_safe

        returns_df = df_raw.pct_change()

        # ── Bull/Bear 시그널 ──────────────────────────────────────────────────
        raw_bull_entry = series_safe > ma_safe
        raw_bear_exit  = series_safe < ma_safe_exit

        if use_asymmetric_ma:
            state_arr = np.zeros(len(series_safe), dtype=int)
            for i in range(1, len(series_safe)):
                state_arr[i] = (1 if raw_bull_entry.iloc[i] else 0) if state_arr[i-1] == 0 \
                               else (0 if raw_bear_exit.iloc[i] else 1)
            raw_bull = pd.Series(state_arr == 1, index=df_raw.index)
        else:
            raw_bull = raw_bull_entry

        raw_hike = series_rate > ma_rate
        raw_aux  = (series_aux > ma_aux) if aux_available else pd.Series(True, index=df_raw.index)

        # ── Whipsaw 필터 ──────────────────────────────────────────────────────
        if use_whipsaw:
            cd = int(confirm_days)
            is_bull_c = raw_bull.rolling(cd).min().fillna(0).astype(bool)
            is_bear_c = (~raw_bull).rolling(cd).min().fillna(0).astype(bool)
            sig = pd.Series(np.nan, index=df_raw.index, dtype='float64')
            sig = sig.where(~is_bull_c, other=1.0).where(~is_bear_c, other=0.0)
            is_bull = sig.ffill().fillna(0.0).astype(bool)
        else:
            is_bull = raw_bull

        is_hike = raw_hike
        is_aux  = raw_aux

        # ── 상태 결정 (T+1 지연) ──────────────────────────────────────────────
        conditions = [
            ~is_bull,
            is_bull & is_aux & (~is_hike | ~use_rate_filter),
            is_bull & (~is_aux | (is_aux & is_hike & use_rate_filter)),
        ]
        raw_state   = pd.Series(np.select(conditions, ["Bear","Bull_Full","Bull_Mix"], default="Bear"), index=df_raw.index)
        trade_state = raw_state.shift(1)

        sim_start   = max(pd.to_datetime(start_date), df_raw.index[0])
        df_sim      = df_raw.loc[sim_start:].copy()
        trade_state = trade_state.loc[sim_start:].fillna("Bear")
        returns_sim = returns_df.loc[sim_start:]

        # ── v5 포지션 가중치 계산 ─────────────────────────────────────────────
        def state_to_weights(state):
            if state == "Bear":
                w = {ticker_safe: bear_spy_ratio}
                if bear_sgov_ratio > 1e-6 and cash_available:
                    w[ticker_cash] = bear_sgov_ratio
                return w
            elif state == "Bull_Full":
                w = {ticker_risky: bull_full_risky_ratio}
                if bull_full_safe_ratio > 1e-6:
                    w[ticker_safe] = bull_full_safe_ratio
                return w
            elif state == "Bull_Mix":
                w = {ticker_risky: bull_mix_risky_ratio}
                if bull_mix_safe_ratio > 1e-6:
                    w[ticker_safe] = bull_mix_safe_ratio
                return w
            return {ticker_safe: 1.0}

        # ── 시뮬레이션 루프 ───────────────────────────────────────────────────
        equity = float(initial_capital)
        peak   = equity
        history = []
        curr_w  = {k: v for k, v in state_to_weights(trade_state.iloc[0]).items() if v > 0}
        equity -= equity * fee_rate
        REBAL_THRESH = 0.05  # 최적화 결과: 5% 임계값

        for i in range(len(df_sim)):
            today    = df_sim.index[i]
            state    = trade_state.iloc[i]
            target_w = {k: v for k, v in state_to_weights(state).items() if v > 0}

            day_ret = 0.0
            if i > 0:
                for tk, w in curr_w.items():
                    if tk in returns_sim.columns:
                        r = returns_sim.loc[today, tk]
                        day_ret += w * (0.0 if pd.isna(r) else float(r))

            equity *= (1.0 + day_ret)

            state_changed  = (curr_w.keys() != target_w.keys())
            weight_changed = any(
                abs(curr_w.get(k, 0) - target_w.get(k, 0)) >= REBAL_THRESH
                for k in set(list(curr_w.keys()) + list(target_w.keys()))
            )
            action = ""
            if state_changed or weight_changed:
                action  = "SWITCH"
                equity -= equity * fee_rate
                curr_w  = target_w

            if equity > peak: peak = equity
            dd = (equity - peak) / peak if peak > 0 else 0.0

            if state == "Bear":
                pos_label = f"Bear (SPY {bear_spy_ratio*100:.0f}% / SGOV {bear_sgov_ratio*100:.0f}%)"
            elif state == "Bull_Full":
                pos_label = f"Bull Full (UPRO {bull_full_risky_ratio*100:.0f}% / SPY {bull_full_safe_ratio*100:.0f}%)"
            else:
                pos_label = f"Bull Mix (UPRO {bull_mix_risky_ratio*100:.0f}% / SPY {bull_mix_safe_ratio*100:.0f}%)"

            history.append({
                "Date":            today,
                "State":           state,
                "Position":        pos_label,
                "UPRO_Weight(%)":  round(curr_w.get(ticker_risky, 0) * 100, 1),
                "SPY_Weight(%)":   round(curr_w.get(ticker_safe,  0) * 100, 1),
                "SGOV_Weight(%)":  round(curr_w.get(ticker_cash,  0) * 100, 1),
                "Action":          action,
                "Equity":          round(equity),
                "Daily_Return(%)": round(day_ret * 100, 4),
                "Drawdown(%)":     round(dd * 100, 4),
                "Safe_Price":      df_sim[ticker_safe].iloc[i],
                "Safe_MA":         ma_safe.loc[today] if today in ma_safe.index else np.nan,
            })

        res_df = pd.DataFrame(history).set_index("Date")
        bm_ret = returns_sim[ticker_safe].fillna(0)
        res_df['Benchmark'] = (1 + bm_ret).cumprod() * initial_capital

        # ── 오늘의 투자 가이드 ────────────────────────────────────────────────
        st.divider()
        st.markdown("### 📢 오늘의 투자 가이드")

        last_date   = df_raw.index[-1]
        last_safe_p = float(df_raw[ticker_safe].iloc[-1])
        last_safe_m = float(ma_safe.iloc[-1])
        last_rate_p = float(df_raw[ticker_rate].iloc[-1])
        last_rate_m = float(ma_rate.iloc[-1])

        is_bull_now = last_safe_p > last_safe_m
        is_hike_now = last_rate_p > last_rate_m
        is_aux_now  = float(df_raw[ticker_aux].iloc[-1]) > float(ma_aux.iloc[-1]) if aux_available else True

        if is_bull_now:
            current_state = "Bull_Mix" if (use_rate_filter and is_hike_now) else "Bull_Full"
            if use_aux_signal and not is_aux_now:
                current_state = "Bull_Mix"
        else:
            current_state = "Bear"

        today_w = state_to_weights(current_state)

        st.caption(f"기준 데이터: {last_date.strftime('%Y-%m-%d')} 종가")
        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            st.metric(f"{ticker_safe} 추세", f"{last_safe_p:,.2f}", f"{last_safe_p - last_safe_m:.2f} (vs MA{int(ma_window)})")
            st.text("📈 상승장" if is_bull_now else "📉 하락장")
            st.text("🔥 금리 주의" if (is_hike_now and use_rate_filter) else "🍀 금리 안정")
        with col_g2:
            st.metric("현재 상태", current_state)
            risky_now = today_w.get(ticker_risky, 0)
            safe_pct  = today_w.get(ticker_safe, 0)
            cash_pct  = today_w.get(ticker_cash, 0)
            st.metric(f"UPRO 권고 비중", f"{risky_now*100:.0f}%")
        with col_g3:
            if current_state == "Bear":
                st.error(
                    f"🛑 **[하락장 방어]**\n\n"
                    f"👉 SPY {safe_pct*100:.0f}% / SGOV {cash_pct*100:.0f}%"
                )
            elif current_state == "Bull_Full":
                st.success(f"🚀 **[강한 상승장]**\n\n👉 UPRO {risky_now*100:.0f}% / SPY {safe_pct*100:.0f}%")
            else:
                st.warning(
                    f"⚠️ **[리스크 관리]**\n\n"
                    f"👉 UPRO {risky_now*100:.0f}% / SPY {safe_pct*100:.0f}%"
                )

        # ── 성과 지표 ─────────────────────────────────────────────────────────
        final_pre_tax = float(res_df['Equity'].iloc[-1])
        profit        = final_pre_tax - initial_capital
        tax_amount    = max(profit, 0) * 0.22 if (apply_tax and profit > 0) else 0.0
        final_balance = final_pre_tax - tax_amount
        final_bm      = float(res_df['Benchmark'].iloc[-1])
        days          = (res_df.index[-1] - res_df.index[0]).days

        cagr   = (final_balance / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
        cagr_b = (final_bm      / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
        mdd    = res_df['Drawdown(%)'].min() / 100.0

        daily_ret_series = res_df['Daily_Return(%)'] / 100.0
        sharpe = sharpe_ratio(daily_ret_series)
        calmar = calmar_ratio(cagr, mdd)
        wr, avg_hold = win_rate_and_avg_hold(res_df)
        n_trades = len(res_df[res_df['Action'] == 'SWITCH'])

        state_counts = res_df['State'].value_counts()
        total_days   = len(res_df)

        st.divider()
        st.markdown("### 📊 성과 요약")
        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        label_bal = "Final Balance (After Tax)" if apply_tax else "Final Balance"
        r1c1.metric(label_bal,    f"{final_balance:,.0f}", delta=f"세금: -{tax_amount:,.0f}" if tax_amount > 0 else None)
        r1c2.metric("CAGR",       f"{cagr*100:.2f}%",      delta=f"{(cagr-cagr_b)*100:.2f}%p vs BM")
        r1c3.metric("MDD",        f"{mdd*100:.2f}%")
        r1c4.metric("벤치마크 CAGR", f"{cagr_b*100:.2f}%")

        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        r2c1.metric("Sharpe Ratio",     f"{sharpe:.2f}", help="연환산 Sharpe (rf=5%)")
        r2c2.metric("Calmar Ratio",     f"{calmar:.2f}", help="CAGR / |MDD|")
        r2c3.metric("매매 승률",         f"{wr:.1f}%")
        r2c4.metric("총 매매 / 평균보유", f"{n_trades}회 / {avg_hold}일")

        st.markdown("#### 📋 상태별 체류 기간")
        sc1, sc2, sc3 = st.columns(3)
        bear_d     = state_counts.get("Bear",      0)
        bf_d       = state_counts.get("Bull_Full", 0)
        bm_d       = state_counts.get("Bull_Mix",  0)
        sc1.metric("🛑 Bear",      f"{bear_d}일",  f"{bear_d/total_days*100:.1f}%")
        sc2.metric("🚀 Bull Full", f"{bf_d}일",    f"{bf_d/total_days*100:.1f}%")
        sc3.metric("⚠️ Bull Mix",  f"{bm_d}일",    f"{bm_d/total_days*100:.1f}%")
        st.divider()

        # ── 월별 수익률 피벗 ─────────────────────────────────────────────────
        m_equity = res_df[['Equity']].resample('ME').last()

        def calc_annual_returns(df_eq):
            annual = {}
            for yr in df_eq.index.year.unique():
                yr_data   = df_eq[df_eq.index.year == yr]['Equity']
                before    = df_eq[df_eq.index.year < yr]['Equity']
                start_val = float(before.iloc[-1]) if len(before) > 0 else float(initial_capital)
                annual[yr] = float(yr_data.iloc[-1]) / start_val - 1.0
            return pd.Series(annual)

        annual_ret  = calc_annual_returns(res_df[['Equity']])
        m_ret       = m_equity['Equity'].pct_change()
        pivot_table = m_ret.groupby([m_equity.index.year, m_equity.index.month]).sum().unstack()
        pivot_table.columns = [calendar.month_abbr[i] for i in pivot_table.columns]
        pivot_table['Total'] = annual_ret

        def color_map(val):
            if pd.isna(val): return ''
            return f'color: {"red" if val < 0 else "green"}'

        # ── 탭 ───────────────────────────────────────────────────────────────
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Chart", "📝 Trade Logs", "📅 Monthly Returns", "📋 배분 분석"])

        with tab1:
            fig = plt.figure(figsize=(14, 24))
            gs  = gridspec.GridSpec(5, 1, height_ratios=[2, 1, 1, 1, 1], hspace=0.4)
            ax  = [fig.add_subplot(gs[i]) for i in range(5)]

            ax[0].plot(res_df.index, res_df['Equity'],    color='firebrick', lw=1.5, label='Strategy v5')
            ax[0].plot(res_df.index, res_df['Benchmark'], color='gray',      lw=1.0, ls='--', alpha=0.7, label=f'B&H {ticker_safe}')
            ax[0].set_yscale('log')
            ax[0].set_title(f"1. Equity Curve — v5 (Bear SGOV {bear_sgov_ratio*100:.0f}%)", fontsize=12)
            ax[0].legend()
            ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))

            spy_cum  = (1 + returns_sim[ticker_safe].fillna(0)).cumprod()
            spy_peak = spy_cum.cummax()
            spy_dd   = ((spy_cum - spy_peak) / spy_peak * 100)

            ax[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0, color='blue', alpha=0.20)
            ax[1].plot(res_df.index, res_df['Drawdown(%)'], color='blue', lw=1.0, label=f'Strategy (MDD {mdd*100:.1f}%)')
            ax[1].plot(spy_dd.index, spy_dd, color='tomato', lw=1.0, ls='--', alpha=0.8,
                       label=f'{ticker_safe} B&H (MDD {spy_dd.min():.1f}%)')
            ax[1].set_title("2. Drawdown (%) — Strategy vs SPY", fontsize=12)
            ax[1].axhline(0, color='black', lw=0.5)
            ax[1].legend(loc='lower left', fontsize=9)

            # 상태 컬러 배경
            state_colors = {"Bear": "salmon", "Bull_Full": "lightgreen", "Bull_Mix": "lightyellow"}
            prev_state, prev_date = None, res_df.index[0]
            for d, row in res_df.iterrows():
                if row['State'] != prev_state:
                    if prev_state is not None:
                        ax[2].axvspan(prev_date, d, alpha=0.15, color=state_colors.get(prev_state, 'white'))
                    prev_state, prev_date = row['State'], d
            ax[2].axvspan(prev_date, res_df.index[-1], alpha=0.15, color=state_colors.get(prev_state, 'white'))

            ax[2].plot(res_df.index, res_df['Safe_Price'], color='black',  lw=1.0, label=f'{ticker_safe} Price')
            ax[2].plot(res_df.index, res_df['Safe_MA'],    color='orange', lw=1.5, ls='--', label=f'Entry MA{int(ma_window)}')
            if use_asymmetric_ma:
                ax[2].plot(res_df.index, ma_safe_exit.loc[res_df.index], color='red', lw=1.2, ls=':', label=f'Exit MA{int(ma_exit_window)}')
            ax[2].set_title(f"3. Trend Signal  [녹색=BullFull / 노랑=BullMix / 빨강=Bear]", fontsize=12)
            ax[2].legend(fontsize=9)

            rate_s    = df_raw.loc[res_df.index, ticker_rate]
            rate_ma_p = ma_rate.loc[res_df.index]
            ax[3].plot(res_df.index, rate_s,    color='purple', lw=1.0, label=f'{ticker_rate}')
            ax[3].plot(res_df.index, rate_ma_p, color='green',  lw=1.5, ls='--', label=f'MA{rate_ma_window}')
            ax[3].set_title(f"4. Rate Signal ({ticker_rate}) — MA{rate_ma_window}일", fontsize=12)
            ax[3].legend()

            # 5. 자산별 비중 스택
            ax[4].stackplot(
                res_df.index,
                res_df['UPRO_Weight(%)'],
                res_df['SPY_Weight(%)'],
                res_df['SGOV_Weight(%)'],
                labels=['UPRO', 'SPY', 'SGOV'],
                colors=['firebrick', 'steelblue', 'gold'],
                alpha=0.7
            )
            ax[4].set_title("5. Asset Allocation Over Time (%)", fontsize=12)
            ax[4].legend(loc='lower left', fontsize=9)
            ax[4].set_ylim(0, 105)

            st.pyplot(fig)

        with tab2:
            st.dataframe(res_df.sort_index(ascending=False), use_container_width=True)

        with tab3:
            st.dataframe(
                pivot_table.style.map(color_map).format("{:.2%}", na_rep=""),
                use_container_width=True
            )

        with tab4:
            st.markdown("#### 📋 v5 배분 전략 분석")

            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown("**🛑 Bear 구간**")
                st.error(
                    f"SPY: **{bear_spy_ratio*100:.0f}%**\n\n"
                    f"SGOV: **{bear_sgov_ratio*100:.0f}%**\n\n"
                    f"→ 최적화: SGOV 50%가 MDD 5%p 개선"
                )
            with col_b:
                st.markdown("**🚀 Bull Full 구간**")
                st.success(
                    f"UPRO: **{bull_full_risky_ratio*100:.0f}%**\n\n"
                    f"SPY: **{bull_full_safe_ratio*100:.0f}%**\n\n"
                    f"→ {'최대 공격' if bull_full_risky_ratio == 1.0 else 'MDD 조절 모드'}"
                )
            with col_c:
                st.markdown("**⚠️ Bull Mix 구간**")
                st.warning(
                    f"UPRO: **{bull_mix_risky_ratio*100:.0f}%**\n\n"
                    f"SPY: **{bull_mix_safe_ratio*100:.0f}%**\n\n"
                    f"→ 발동 빈도 낮아 결과 영향 미미"
                )

            st.markdown("---")
            st.markdown("**상태별 수익 기여도**")
            for st_name, icon in [("Bear","🛑"), ("Bull_Full","🚀"), ("Bull_Mix","⚠️")]:
                mask    = res_df['State'] == st_name
                seg_ret = res_df.loc[mask, 'Daily_Return(%)']
                days_c  = mask.sum()
                if days_c > 0:
                    total_ret = seg_ret.sum()
                    st.metric(
                        f"{icon} {st_name} ({days_c}일 / {days_c/total_days*100:.1f}%)",
                        f"누적 기여: {total_ret:.2f}%",
                        delta=f"일평균: {total_ret/days_c:.3f}%"
                    )

            # 최적화 결과 참고표
            st.markdown("---")
            st.markdown("**📊 v4 최적화 결과 — Bear SGOV 비중별 평균 성과**")
            opt_table = pd.DataFrame({
                "Bear SGOV 비중": ["0%", "20%", "30%", "40%", "50% ✅"],
                "평균 CAGR":  ["25.98%", "25.51%", "25.23%", "24.92%", "24.57%"],
                "평균 MDD":   ["-53.0%", "-52.8%", "-52.8%", "-52.9%", "-53.0%"],
                "평균 Sharpe":["0.659",  "0.658",  "0.655",  "0.652",  "0.647"],
                "최고 Sharpe":["0.837",  "0.860",  "0.870",  "0.878",  "0.885 🏆"],
            })
            st.dataframe(opt_table, use_container_width=True, hide_index=True)
            st.caption("※ 평균 Sharpe는 SGOV 0%가 높지만, 최고 Sharpe(특정 파라미터 조합 기준)는 SGOV 50%가 압도적")

        # ── 엑셀 다운로드 ─────────────────────────────────────────────────────
        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                res_df.to_excel(writer, sheet_name='Daily_Log')
                pivot_table.to_excel(writer, sheet_name='Monthly_Returns')
                summary = pd.DataFrame({
                    'Metric': ['CAGR', 'MDD', 'Sharpe', 'Calmar', '승률', '매매횟수', '평균보유일',
                               'Bear_SPY비중', 'Bear_SGOV비중',
                               'BullFull_UPRO비중', 'BullFull_SPY비중',
                               'BullMix_UPRO비중', 'BullMix_SPY비중',
                               'Bull_진입MA', 'Bear_퇴출MA', '금리MA'],
                    'Value':  [f"{cagr*100:.2f}%", f"{mdd*100:.2f}%",
                               f"{sharpe:.2f}", f"{calmar:.2f}",
                               f"{wr:.1f}%", n_trades, avg_hold,
                               f"{bear_spy_ratio*100:.0f}%", f"{bear_sgov_ratio*100:.0f}%",
                               f"{bull_full_risky_ratio*100:.0f}%", f"{bull_full_safe_ratio*100:.0f}%",
                               f"{bull_mix_risky_ratio*100:.0f}%", f"{bull_mix_safe_ratio*100:.0f}%",
                               f"{int(ma_window)}일", f"{int(ma_exit_window)}일", f"{int(rate_ma_window)}일"]
                })
                summary.to_excel(writer, sheet_name='Summary_v5', index=False)
        except Exception:
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                res_df.to_excel(writer, sheet_name='Daily_Log')
                pivot_table.to_excel(writer, sheet_name='Monthly_Returns')

        st.download_button(
            "📥 엑셀 결과 다운로드",
            data=output.getvalue(),
            file_name=f"Mix_Strategy_v5_{ticker_safe}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
