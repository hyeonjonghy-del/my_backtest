import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import warnings
import calendar

# -----------------------------------------------------------------------------
# 1. 기본 설정
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot')
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(
    page_title="Safe/Risky/Cash Mix Strategy",
    page_icon="🛡️",
    layout="wide"
)

st.title("🛡️ Safe/Risky/Cash Mix Strategy v2")
st.markdown("""
**전략 개요 (v2 - Verified & Fixed):**
- **로직:** 시그널 발생(T일) → 다음 날(T+1일) 장 마감(종가)에 매매
- **자산 배분:**
    - **Bear (하락장):** 안전자산 100%
    - **Bull (상승장):**
        - **Low Risk (금리 안정):** 공격자산 100%
        - **High Risk (금리 급등):** 공격자산 + 현금 혼합 (리스크 관리)
- **v2 개선 사항:** 수정주가(배당 포함) 적용, 수익률 계산 최적화, 연간 수익률 보정
""")
st.markdown("---")

# -----------------------------------------------------------------------------
# 2. 사이드바 설정
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. 투자 종목 설정")
    ticker_safe  = st.text_input("안전 자산 (Bear)", value="SPY")
    ticker_risky = st.text_input("공격 자산 (Bull)", value="UPRO")
    ticker_cash  = st.text_input("현금 파킹 (Cash)", value="SGOV")

    st.header("2. 전략 옵션")
    start_date      = st.date_input("시작일", pd.to_datetime("2020-01-01"))
    initial_capital = st.number_input("초기 자본", value=100_000_000, step=1_000_000)
    fee_rate        = st.number_input("매매 수수료 (%)", value=0.02, step=0.01) / 100.0

    apply_tax = st.checkbox("양도소득세 22% 차감 (수익 발생 시)", value=False)
    if apply_tax:
        st.caption("ℹ️ 최종 총 수익금의 22%를 세금으로 제하고 계산합니다.")

    ma_window = st.number_input("추세 판단 이평선 (일)", value=120, min_value=5)

    st.header("3. 리스크(금리) 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    ticker_rate     = st.text_input("금리 지표", value="^TNX")
    rate_ma_window  = st.number_input("금리 이평선 (일)", value=120)
    exposure_ratio  = st.slider("리스크 시 공격비중", 0.0, 1.0, 0.6, 0.1)
    st.caption(f"나머지 {100 - exposure_ratio*100:.0f}%는 현금({ticker_cash}) 보유")

# -----------------------------------------------------------------------------
# 3. 데이터 로드
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600 * 24)
def load_data(safe, risky, rate, cash):
    """
    [v2 수정] auto_adjust=True 사용:
    - SGOV 같은 고배당 현금 ETF는 배당이 핵심 수익원 → 수정주가 필수
    - UPRO 같은 레버리지 ETF도 리밸런싱 비용이 주가에 반영 → 수정주가가 실제 수익률에 근접
    - ^TNX (금리)는 배당 없으므로 영향 없음
    """
    tickers = list(dict.fromkeys([safe, risky, rate, cash]))  # 중복 제거, 순서 유지
    raw = yf.download(tickers, start="2000-01-01", progress=False, auto_adjust=True)

    # 멀티인덱스: 'Close' 레벨 추출
    if isinstance(raw.columns, pd.MultiIndex):
        close_df = raw['Close'].copy()
    else:
        close_df = raw.copy()

    # 인덱스 중복 제거 및 정렬
    close_df = close_df.loc[~close_df.index.duplicated(keep='first')].sort_index()
    return close_df

# -----------------------------------------------------------------------------
# 4. 메인 로직
# -----------------------------------------------------------------------------
if st.button("🚀 Run Backtest", type="primary", use_container_width=True):
    with st.spinner("분석 중..."):

        # ── 데이터 준비 ────────────────────────────────────────────────────────
        full_df = load_data(ticker_safe, ticker_risky, ticker_rate, ticker_cash)

        # 필수 컬럼 확인
        missing = [c for c in [ticker_safe, ticker_risky, ticker_rate] if c not in full_df.columns]
        if missing:
            st.error(f"데이터를 가져오지 못한 티커: {missing}")
            st.stop()

        # SGOV 누락 확인 (경고만, 중단 않음)
        cash_available = ticker_cash in full_df.columns
        if not cash_available:
            st.warning(f"⚠️ {ticker_cash} 데이터를 가져오지 못했습니다. Bull_Mix 시 현금 수익률이 0%로 처리됩니다.")

        df_raw = full_df.ffill()  # 결측치 보완

        # ── 지표 계산 (전체 기간) ───────────────────────────────────────────────
        series_safe = df_raw[ticker_safe]
        ma_safe     = series_safe.rolling(window=int(ma_window)).mean()

        series_rate = df_raw[ticker_rate]
        ma_rate     = series_rate.rolling(window=int(rate_ma_window)).mean()

        # [v2 핵심 수정] 수익률 시계열을 루프 밖에서 미리 계산 → 성능 및 정확도 향상
        returns_df = df_raw.pct_change()

        # ── 시그널 판단 (T일 기준) ──────────────────────────────────────────────
        is_bull = series_safe > ma_safe
        is_hike = series_rate > ma_rate

        conditions = [
            ~is_bull,
            is_bull & (~is_hike | ~use_rate_filter),
            is_bull & is_hike & use_rate_filter,
        ]
        choices   = ["Bear", "Bull_Full", "Bull_Mix"]
        raw_state = pd.Series(
            np.select(conditions, choices, default="Bear"),
            index=df_raw.index
        )

        # ── T+1 매매 지연 ────────────────────────────────────────────────────────
        trade_state = raw_state.shift(1)

        # ── 시뮬레이션 범위 설정 ────────────────────────────────────────────────
        sim_start   = pd.to_datetime(start_date)
        sim_start   = max(sim_start, df_raw.index[0])

        df_sim      = df_raw.loc[sim_start:].copy()
        trade_state = trade_state.loc[sim_start:].fillna("Bear")
        returns_sim = returns_df.loc[sim_start:]

        # ── 포지션 결정 함수 ─────────────────────────────────────────────────────
        def state_to_weights(state):
            if state == "Bear":
                return {ticker_safe: 1.0}
            elif state == "Bull_Full":
                return {ticker_risky: 1.0}
            elif state == "Bull_Mix":
                w = {ticker_risky: exposure_ratio}
                if cash_available:
                    w[ticker_cash] = 1.0 - exposure_ratio
                return w
            return {ticker_safe: 1.0}

        # ── 시뮬레이션 루프 ──────────────────────────────────────────────────────
        equity  = float(initial_capital)
        peak    = equity
        history = []

        # 초기 포지션 및 첫 진입 수수료
        curr_w  = state_to_weights(trade_state.iloc[0])
        curr_w  = {k: v for k, v in curr_w.items() if v > 0}
        equity -= equity * fee_rate

        for i in range(len(df_sim)):
            today    = df_sim.index[i]
            state    = trade_state.iloc[i]
            target_w = {k: v for k, v in state_to_weights(state).items() if v > 0}

            # [B] 수익률 반영 (i=0은 첫 날이므로 전일 대비 수익률 없음 → 0)
            day_ret = 0.0
            if i > 0:
                for ticker, weight in curr_w.items():
                    if ticker in returns_sim.columns:
                        r = returns_sim.loc[today, ticker]
                        day_ret += weight * (0.0 if pd.isna(r) else r)

            equity *= (1.0 + day_ret)

            # [C] 리밸런싱 여부 확인 (키 집합 + 비중 모두 동일할 때만 유지)
            is_same = (curr_w.keys() == target_w.keys()) and all(
                abs(curr_w.get(k, 0) - target_w.get(k, 0)) < 1e-9 for k in target_w
            )
            action = ""
            if not is_same:
                action  = "SWITCH"
                equity -= equity * fee_rate
                curr_w  = target_w

            # MDD 추적
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak if peak > 0 else 0.0

            # 포지션 레이블
            pos_label_map = {
                "Bear":      f"Bear ({ticker_safe})",
                "Bull_Full": f"Bull Full ({ticker_risky})",
                "Bull_Mix":  f"Bull Mix ({ticker_risky}+{ticker_cash})"
            }

            history.append({
                "Date":          today,
                "State":         state,
                "Position":      pos_label_map.get(state, state),
                "Action":        action,
                "Equity":        round(equity),
                "Daily_Return(%)": round(day_ret * 100, 4),
                "Drawdown(%)":   round(dd * 100, 4),
                "Safe_Price":    df_sim[ticker_safe].iloc[i],
                "Safe_MA":       ma_safe.loc[today] if today in ma_safe.index else np.nan,
            })

        res_df = pd.DataFrame(history).set_index("Date")

        # 벤치마크 (안전자산 Buy & Hold)
        bm_ret = returns_sim[ticker_safe].fillna(0) if ticker_safe in returns_sim.columns else pd.Series(0, index=df_sim.index)
        res_df['Benchmark'] = (1 + bm_ret).cumprod() * initial_capital

        # ── 오늘의 투자 가이드 ───────────────────────────────────────────────────
        st.divider()
        st.markdown("### 📢 오늘의 투자 가이드 (Today's Action)")

        last_date    = df_raw.index[-1]
        last_safe_p  = df_raw[ticker_safe].iloc[-1]
        last_safe_m  = ma_safe.iloc[-1]
        last_rate_p  = df_raw[ticker_rate].iloc[-1]
        last_rate_m  = ma_rate.iloc[-1]

        is_bull_now  = last_safe_p > last_safe_m
        is_hike_now  = last_rate_p > last_rate_m

        if is_bull_now:
            current_state = "Bull_Mix" if (use_rate_filter and is_hike_now) else "Bull_Full"
        else:
            current_state = "Bear"

        st.caption(f"기준 데이터: {last_date.strftime('%Y-%m-%d')} 종가")

        col_g1, col_g2 = st.columns([1, 2])
        with col_g1:
            st.metric(
                label=f"{ticker_safe} 추세",
                value=f"{last_safe_p:,.2f}",
                delta=f"{last_safe_p - last_safe_m:.2f} (vs 이평선)",
                delta_color="normal"
            )
            st.text("📈 상승장 (Bull)" if is_bull_now else "📉 하락장 (Bear)")
            st.text("🔥 금리 주의!" if (is_hike_now and use_rate_filter) else "🍀 금리 안정")

        with col_g2:
            if current_state == "Bear":
                st.error(f"🛑 **[하락장 방어]** 현재 시장이 좋지 않습니다.\n\n👉 **{ticker_safe} (안전자산)** 100% 보유")
            elif current_state == "Bull_Full":
                st.success(f"🚀 **[강한 상승장]** 시장과 금리가 모두 우호적입니다.\n\n👉 **{ticker_risky} (공격자산)** 100% 보유")
            elif current_state == "Bull_Mix":
                mix_pct  = int(exposure_ratio * 100)
                cash_pct = 100 - mix_pct
                st.warning(f"⚠️ **[리스크 관리]** 상승장이지만 금리가 높습니다.\n\n👉 **{ticker_risky}**: {mix_pct}% / **{ticker_cash}**: {cash_pct}% 리밸런싱")

        # ── 세금 계산 및 성과 지표 ───────────────────────────────────────────────
        final_pre_tax = float(res_df['Equity'].iloc[-1])
        profit        = final_pre_tax - initial_capital
        tax_amount    = max(profit, 0) * 0.22 if (apply_tax and profit > 0) else 0.0
        final_balance = final_pre_tax - tax_amount

        final_bm  = float(res_df['Benchmark'].iloc[-1])
        days      = (res_df.index[-1] - res_df.index[0]).days

        cagr   = (final_balance / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
        cagr_b = (final_bm      / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
        mdd    = res_df['Drawdown(%)'].min() / 100.0

        # 거래 횟수 및 승률
        trades = res_df[res_df['Action'] == 'SWITCH'].copy()
        n_trades = len(trades)

        st.divider()
        c1, c2, c3, c4 = st.columns(4)
        label_balance = "Final Balance (After Tax)" if apply_tax else "Final Balance"
        c1.metric(label_balance,    f"{final_balance:,.0f} KRW", delta=f"세금: -{tax_amount:,.0f}" if tax_amount > 0 else None)
        c2.metric("CAGR (전략)",    f"{cagr*100:.2f} %",         delta=f"{(cagr - cagr_b)*100:.2f}%p vs BM")
        c3.metric("MDD",            f"{mdd*100:.2f} %")
        c4.metric("총 매매 횟수",   f"{n_trades} 회")
        st.divider()

        # ── 월별 수익률 피벗 ─────────────────────────────────────────────────────
        m_equity = res_df[['Equity']].resample('ME').last()

        # [v2 수정] 연간 수익률: 각 연도의 첫 거래일 equity와 마지막 equity 비교
        def calc_annual_returns(df_equity):
            annual = {}
            for yr in df_equity.index.year.unique():
                yr_data = df_equity[df_equity.index.year == yr]['Equity']
                if len(yr_data) == 0:
                    continue
                # 해당 연도 시작 직전 equity (없으면 initial_capital)
                before = df_equity[df_equity.index.year < yr]['Equity']
                start_val = float(before.iloc[-1]) if len(before) > 0 else float(initial_capital)
                end_val   = float(yr_data.iloc[-1])
                annual[yr] = (end_val / start_val) - 1.0
            return pd.Series(annual)

        annual_ret = calc_annual_returns(res_df[['Equity']])

        # 월별 수익률 피벗
        m_ret = m_equity['Equity'].pct_change()
        pivot_table = m_ret.groupby([m_equity.index.year, m_equity.index.month]).sum().unstack()
        pivot_table.columns = [calendar.month_abbr[i] for i in pivot_table.columns]
        pivot_table['Total'] = annual_ret

        def color_map(val):
            if pd.isna(val): return ''
            return f'color: {"red" if val < 0 else "green"}'

        # ── 탭 출력 ──────────────────────────────────────────────────────────────
        tab1, tab2, tab3 = st.tabs(["📊 Chart", "📝 Trade Logs", "📅 Monthly Returns"])

        with tab1:
            st.subheader("Strategy Performance & Indicators")
            fig, ax = plt.subplots(4, 1, figsize=(14, 20), gridspec_kw={'height_ratios': [2, 1, 1, 1]})

            # 1. Equity Curve
            ax[0].plot(res_df.index, res_df['Equity'],    color='firebrick', linewidth=1.5, label='Strategy')
            ax[0].plot(res_df.index, res_df['Benchmark'], color='gray',      linewidth=1.0, linestyle='--', alpha=0.7, label=f'B&H {ticker_safe}')
            ax[0].set_yscale('log')
            ax[0].set_title("1. Equity Curve (Log Scale, Pre-Tax)", fontsize=12)
            ax[0].legend()
            ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))

            # 2. Drawdown
            ax[1].plot(res_df.index, res_df['Drawdown(%)'], color='blue', linewidth=1.0)
            ax[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0, color='blue', alpha=0.1)
            ax[1].set_title("2. Drawdown (%)", fontsize=12)
            ax[1].axhline(0, color='black', linewidth=0.5)

            # 3. 추세 시그널
            ax[2].plot(res_df.index, res_df['Safe_Price'], color='black',  linewidth=1.0, label=f'{ticker_safe} Price')
            ax[2].plot(res_df.index, res_df['Safe_MA'],    color='orange', linewidth=1.5, linestyle='--', label=f'MA ({ma_window})')
            ax[2].set_title(f"3. Trend Signal ({ticker_safe} vs MA{ma_window})", fontsize=12)
            ax[2].legend()

            # 4. 금리 시그널
            plot_rate    = df_raw.loc[res_df.index, ticker_rate]
            plot_rate_ma = ma_rate.loc[res_df.index]
            ax[3].plot(res_df.index, plot_rate,    color='purple', linewidth=1.0, label=f'{ticker_rate}')
            ax[3].plot(res_df.index, plot_rate_ma, color='green',  linewidth=1.5, linestyle='--', label=f'MA ({rate_ma_window})')
            ax[3].set_title(f"4. Rate Risk Signal ({ticker_rate})", fontsize=12)
            ax[3].legend()

            plt.tight_layout()
            st.pyplot(fig)

        with tab2:
            st.dataframe(res_df.sort_index(ascending=False), use_container_width=True)

        with tab3:
            st.dataframe(
                pivot_table.style
                    .map(color_map)
                    .format("{:.2%}", na_rep=""),
                use_container_width=True
            )

        # ── 엑셀 다운로드 ────────────────────────────────────────────────────────
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            res_df.to_excel(writer, sheet_name='Daily_Log')
            pivot_table.to_excel(writer, sheet_name='Monthly_Returns')

        st.download_button(
            "📥 엑셀 결과 다운로드",
            data=output.getvalue(),
            file_name=f"Mix_Strategy_{ticker_safe}_v2.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
