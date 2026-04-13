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

st.set_page_config(page_title="Safe/Risky/Cash Mix Strategy v3", page_icon="🛡️", layout="wide")

st.title("🛡️ Safe/Risky/Cash Mix Strategy v3 (최적화 적용)")
st.markdown("""
**전략 개요 (v3 — 5,400개 조합 최적화 반영):**
- **로직:** 시그널 발생(T일) → 다음 날(T+1일) 장 마감(종가)에 매매
- **자산 배분:**
    - **Bear (하락장):** 안전자산 100%
    - **Bull Full (상승장 + 금리 안정):** 공격자산 100%
    - **Bull Mix (상승장 + 금리 주의):** 공격자산 + 현금 혼합

| 파라미터 | 기본값 | 최적화 근거 |
|---------|--------|------------|
| Bull 진입 MA | **150일** | Top 100 전부 150일 |
| Bear 퇴출 MA | **112일** | 진입 MA × 0.75 (Top 1) |
| 금리 MA | **90일** | 60~150일 차이 미미 |
| Whipsaw 필터 | **OFF** | confirm=1일이 Top 100 독점 |
""")
st.markdown("---")

# ── 사이드바 ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("1. 투자 종목 설정")
    ticker_safe  = st.text_input("안전 자산 (Bear)",   value="SPY")
    ticker_risky = st.text_input("공격 자산 (Bull)",   value="UPRO")
    ticker_cash  = st.text_input("현금 파킹 (Cash)",   value="SGOV")

    st.header("2. 전략 기본 옵션")
    start_date      = st.date_input("시작일", pd.to_datetime("2020-01-01"))
    initial_capital = st.number_input("초기 자본", value=100_000_000, step=1_000_000)
    fee_rate        = st.number_input("매매 수수료 (%)", value=0.02, step=0.01) / 100.0
    apply_tax       = st.checkbox("양도소득세 22% 차감", value=False)
    # ★ 최적화 결과 반영: 진입 MA=150 (Top 100 전부 150일)
    ma_window         = st.number_input("Bull 진입 이평선 (일)", value=150, min_value=5,
                                        help="최적화 결과: 150일이 압도적 1위 (Top 100 전부 150일)")
    use_asymmetric_ma = st.checkbox("비대칭 MA 사용 (빠른 퇴출)", value=True,
                                     help="Bear 전환 시 더 짧은 이평선 사용 → 폭락 시 빠른 손실 차단")
    if use_asymmetric_ma:
        # ★ 최적화 결과 반영: 퇴출 MA = 진입 MA × 0.75 = 112일
        ma_exit_window = st.number_input("Bear 퇴출 이평선 (일)", value=112, min_value=5,
                                          help="최적화 결과: 진입 MA의 75% (150×0.75=112일)가 최적")
        st.caption(f"ℹ️ 진입 MA{int(ma_window)} / 퇴출 MA{int(ma_exit_window)}  →  천천히 사고 빠르게 판다")
    else:
        ma_exit_window = ma_window

    st.header("3. 금리 리스크 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    ticker_rate     = st.text_input("금리 지표", value="^TNX")
    # ★ 최적화 결과 반영: 금리 MA 90일 (60~150일은 결과 차이 거의 없음)
    rate_ma_window  = st.number_input("금리 이평선 (일)", value=90,
                                       help="최적화 결과: 60~150일 차이 미미 — 90일 권장")

    st.header("4. 🆕 Whipsaw 필터")
    # ★ 최적화 결과 반영: confirm_days=1이 Top 100 전부 차지 → 기본 OFF
    use_whipsaw     = st.checkbox("Whipsaw 필터 사용", value=False,
                                   help="최적화 결과: confirm=1일(=사실상 OFF)이 압도적 1위 → 기본 해제")
    confirm_days    = st.number_input("신호 확정 기간 (일)", value=1, min_value=1, max_value=10,
                                       help="1일 = 필터 없음 (최적화 결과 기준)")
    if use_whipsaw:
        st.caption(f"ℹ️ {confirm_days}일 연속 조건 충족 시에만 신호 발생 → 잦은 매매 방지")

    st.header("5. 🆕 변동성 기반 포지션 사이징")
    use_vol_sizing  = st.checkbox("변동성 기반 비중 조절 사용", value=True)
    vol_window      = st.number_input("변동성 계산 기간 (일)", value=20, min_value=5)
    target_vol      = st.number_input("목표 변동성 (%)", value=25.0, step=1.0) / 100.0
    max_risky_w     = st.slider("공격자산 최대 비중", 0.3, 1.0, 1.0, 0.1)
    vol_rebal_threshold = st.number_input("리밸런싱 최소 비중 변화 (%)", value=5.0, step=1.0,
                                          help="비중 변화가 이 값 미만이면 거래 안 함 → 과도한 매매 방지") / 100.0
    apply_vol_on_bull_full = st.checkbox("Bull_Full에도 변동성 사이징 적용", value=False,
                                          help="체크 해제 시 Bull_Full은 항상 최대 비중 유지 (CAGR 보호)")
    if use_vol_sizing:
        st.caption("ℹ️ 변동성 높으면 UPRO 비중 자동 축소 / 낮으면 확대 (최대 비중 제한)")

    st.header("6. 🆕 보조 시장 필터")
    use_aux_signal  = st.checkbox("보조 시장 신호 사용 (QQQ)", value=False)
    ticker_aux      = st.text_input("보조 시장 티커", value="QQQ")
    aux_ma_window   = st.number_input("보조 이평선 (일)", value=120)
    if use_aux_signal:
        st.caption("ℹ️ SPY+QQQ 모두 이평선 위일 때만 Bull_Full 진입")

    st.header("7. 기본 Bull 비중 설정")
    exposure_ratio = st.slider("리스크 시 공격비중 (Bull_Mix)", 0.0, 1.0, 0.6, 0.1)
    st.caption(f"나머지 {100 - exposure_ratio*100:.0f}%는 {ticker_cash} 보유")

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
def sharpe_ratio(daily_returns: pd.Series, rf: float = 0.05) -> float:
    """연환산 Sharpe Ratio (무위험수익률 기본 5%)"""
    excess = daily_returns - rf / 252
    if excess.std() == 0:
        return 0.0
    return float(excess.mean() / excess.std() * np.sqrt(252))

def calmar_ratio(cagr: float, mdd: float) -> float:
    return abs(cagr / mdd) if mdd != 0 else 0.0

def win_rate_and_avg_hold(res_df: pd.DataFrame) -> tuple:
    """매매 로그에서 승률과 평균 보유 기간 계산"""
    switches = res_df[res_df['Action'] == 'SWITCH'].index.tolist()
    if len(switches) < 2:
        return 0.0, 0
    wins, hold_days = 0, []
    for i in range(len(switches) - 1):
        s, e = switches[i], switches[i + 1]
        seg = res_df.loc[s:e, 'Equity']
        if len(seg) >= 2 and seg.iloc[0] > 0:
            if seg.iloc[-1] > seg.iloc[0]:
                wins += 1
        hold_days.append((e - s).days)
    total = len(switches) - 1
    return (wins / total * 100) if total > 0 else 0.0, int(np.mean(hold_days)) if hold_days else 0

# ── 메인 로직 ─────────────────────────────────────────────────────────────────
if st.button("🚀 Run Enhanced Backtest", type="primary", use_container_width=True):
    with st.spinner("향상된 전략 분석 중..."):

        # 1. 데이터 준비
        full_df = load_data(ticker_safe, ticker_risky, ticker_rate, ticker_cash, ticker_aux)

        missing = [c for c in [ticker_safe, ticker_risky, ticker_rate] if c not in full_df.columns]
        if missing:
            st.error(f"데이터 없는 티커: {missing}")
            st.stop()

        cash_available = ticker_cash in full_df.columns
        aux_available  = ticker_aux  in full_df.columns and use_aux_signal

        if not cash_available:
            st.warning(f"⚠️ {ticker_cash} 데이터 없음 → 현금 수익률 0% 처리")
        if use_aux_signal and not aux_available:
            st.warning(f"⚠️ {ticker_aux} 데이터 없음 → 보조 신호 비활성화")

        df_raw = full_df.ffill()

        # 2. 지표 계산
        series_safe  = df_raw[ticker_safe]
        ma_safe      = series_safe.rolling(window=int(ma_window)).mean()       # 진입용 (느린 MA)
        ma_safe_exit = series_safe.rolling(window=int(ma_exit_window)).mean()  # 퇴출용 (빠른 MA)
        series_rate  = df_raw[ticker_rate]
        ma_rate      = series_rate.rolling(window=int(rate_ma_window)).mean()

        # [보조 시장] QQQ 이평선
        if aux_available:
            series_aux = df_raw[ticker_aux]
            ma_aux     = series_aux.rolling(window=int(aux_ma_window)).mean()
        else:
            series_aux = series_safe
            ma_aux     = ma_safe

        # [변동성] SPY 20일 실현 변동성 (연환산)
        spy_vol = series_safe.pct_change().rolling(window=int(vol_window)).std() * np.sqrt(252)

        # 3. 수익률 시계열 (루프 밖 사전 계산)
        returns_df = df_raw.pct_change()

        # 4. 기본 Bull/Bear 시그널 — 비대칭 MA 적용
        # 진입: 현재가 > 느린MA(120일)  →  Bull
        # 퇴출: 현재가 < 빠른MA(60일)   →  Bear  (비대칭 MA 사용 시)
        raw_bull_entry = series_safe > ma_safe       # Bull 진입 조건
        raw_bear_exit  = series_safe < ma_safe_exit  # Bear 전환 조건 (빠른 MA 기준)

        # 비대칭 상태 머신: 한 번 Bull 진입하면 빠른 MA 아래로 내려올 때만 Bear 전환
        if use_asymmetric_ma:
            state_arr = np.zeros(len(series_safe), dtype=int)  # 0=Bear, 1=Bull
            for i in range(1, len(series_safe)):
                if state_arr[i-1] == 0:   # 현재 Bear
                    state_arr[i] = 1 if raw_bull_entry.iloc[i] else 0  # 느린 MA로 진입 판단
                else:                      # 현재 Bull
                    state_arr[i] = 0 if raw_bear_exit.iloc[i]  else 1  # 빠른 MA로 퇴출 판단
            raw_bull = pd.Series(state_arr == 1, index=df_raw.index)
        else:
            raw_bull = raw_bull_entry

        raw_hike = series_rate > ma_rate
        raw_aux  = (series_aux > ma_aux) if aux_available else pd.Series(True, index=df_raw.index)

        # 5. [Whipsaw 필터] N일 연속 조건 충족 시에만 신호 확정
        if use_whipsaw:
            cd = int(confirm_days)
            # rolling min: 최근 N일이 모두 True인 경우만 True
            is_bull_confirmed = raw_bull.rolling(cd).min().fillna(0).astype(bool)
            # Bear는 반대: 최근 N일 모두 False
            is_bear_confirmed = (~raw_bull).rolling(cd).min().fillna(0).astype(bool)
            # [pandas 2.x 수정] float 타입으로 생성 후 1.0/0.0 할당 → bool 직접 할당 오류 방지
            bull_signal = pd.Series(np.nan, index=df_raw.index, dtype='float64')
            bull_signal = bull_signal.where(~is_bull_confirmed, other=1.0)
            bull_signal = bull_signal.where(~is_bear_confirmed, other=0.0)
            is_bull = bull_signal.ffill().fillna(0.0).astype(bool)
        else:
            is_bull = raw_bull

        is_hike = raw_hike
        is_aux  = raw_aux

        # 6. 상태 결정
        conditions = [
            ~is_bull,
            is_bull & is_aux & (~is_hike | ~use_rate_filter),
            is_bull & (~is_aux | (is_aux & is_hike & use_rate_filter)),
        ]
        choices   = ["Bear", "Bull_Full", "Bull_Mix"]
        raw_state = pd.Series(
            np.select(conditions, choices, default="Bear"),
            index=df_raw.index
        )

        # 7. T+1 매매 지연
        trade_state = raw_state.shift(1)

        # 8. 시뮬레이션 범위
        sim_start   = max(pd.to_datetime(start_date), df_raw.index[0])
        df_sim      = df_raw.loc[sim_start:].copy()
        trade_state = trade_state.loc[sim_start:].fillna("Bear")
        returns_sim = returns_df.loc[sim_start:]
        spy_vol_sim = spy_vol.loc[sim_start:]

        # 9. 포지션 가중치 계산 함수
        def state_to_weights(state: str, today_vol: float) -> dict:
            """
            [v3.1 수정]
            - Bull_Full: apply_vol_on_bull_full=False(기본)이면 max_risky_w 그대로 사용
              → CAGR 보호. True이면 변동성 사이징 적용.
            - Bull_Mix: 항상 변동성 사이징 적용 (리스크 관리 구간이므로)
            - 리밸런싱 임계값(vol_rebal_threshold)은 루프에서 별도 처리
            """
            def calc_vol_weight(base_w: float) -> float:
                if use_vol_sizing and not np.isnan(today_vol) and today_vol > 0:
                    vol_upro = today_vol * 3.0
                    return min(target_vol / vol_upro, base_w)
                return base_w

            if state == "Bear":
                return {ticker_safe: 1.0}

            elif state == "Bull_Full":
                if use_vol_sizing and apply_vol_on_bull_full:
                    risky_w = calc_vol_weight(max_risky_w)
                else:
                    risky_w = max_risky_w          # 변동성 사이징 미적용 → 항상 최대치
                cash_w = 1.0 - risky_w
                w = {ticker_risky: risky_w}
                if cash_w > 1e-6 and cash_available:
                    w[ticker_cash] = cash_w
                return w

            elif state == "Bull_Mix":
                base_w  = max_risky_w * exposure_ratio
                risky_w = calc_vol_weight(base_w)  # Bull_Mix는 항상 변동성 사이징
                cash_w  = 1.0 - risky_w
                w = {ticker_risky: risky_w}
                if cash_w > 1e-6 and cash_available:
                    w[ticker_cash] = cash_w
                return w

            return {ticker_safe: 1.0}

        # 10. 시뮬레이션 루프
        equity = float(initial_capital)
        peak   = equity
        history = []

        first_vol = spy_vol_sim.iloc[0] if not spy_vol_sim.empty else np.nan
        curr_w    = {k: v for k, v in state_to_weights(trade_state.iloc[0], first_vol).items() if v > 0}
        equity   -= equity * fee_rate  # 첫 진입 수수료

        for i in range(len(df_sim)):
            today     = df_sim.index[i]
            state     = trade_state.iloc[i]
            today_vol = spy_vol_sim.iloc[i] if i < len(spy_vol_sim) else np.nan
            target_w  = {k: v for k, v in state_to_weights(state, today_vol).items() if v > 0}

            # 수익률 반영
            day_ret = 0.0
            if i > 0:
                for tk, w in curr_w.items():
                    if tk in returns_sim.columns:
                        r = returns_sim.loc[today, tk]
                        day_ret += w * (0.0 if pd.isna(r) else float(r))

            equity *= (1.0 + day_ret)

            # 리밸런싱 판단: State 변경 OR 비중 변화가 임계값 이상일 때만 거래
            state_changed = (curr_w.keys() != target_w.keys())
            weight_changed = any(
                abs(curr_w.get(k, 0) - target_w.get(k, 0)) >= vol_rebal_threshold
                for k in set(list(curr_w.keys()) + list(target_w.keys()))
            )
            action = ""
            if state_changed or weight_changed:
                action  = "SWITCH"
                equity -= equity * fee_rate
                curr_w  = target_w

            # MDD
            if equity > peak: peak = equity
            dd = (equity - peak) / peak if peak > 0 else 0.0

            pos_map = {
                "Bear":      f"Bear ({ticker_safe})",
                "Bull_Full": f"Bull Full ({ticker_risky})",
                "Bull_Mix":  f"Bull Mix ({ticker_risky}+{ticker_cash})",
            }
            risky_w_now = curr_w.get(ticker_risky, 0.0)

            history.append({
                "Date":             today,
                "State":            state,
                "Position":         pos_map.get(state, state),
                "Risky_Weight(%)":  round(risky_w_now * 100, 1),
                "Action":           action,
                "Equity":           round(equity),
                "Daily_Return(%)":  round(day_ret * 100, 4),
                "Drawdown(%)":      round(dd * 100, 4),
                "SPY_Vol(ann,%)":   round(today_vol * 100, 2) if not np.isnan(today_vol) else np.nan,
                "Safe_Price":       df_sim[ticker_safe].iloc[i],
                "Safe_MA":          ma_safe.loc[today] if today in ma_safe.index else np.nan,
            })

        res_df = pd.DataFrame(history).set_index("Date")

        # 벤치마크
        bm_ret = returns_sim[ticker_safe].fillna(0)
        res_df['Benchmark'] = (1 + bm_ret).cumprod() * initial_capital

        # ── 오늘의 투자 가이드 ───────────────────────────────────────────────
        st.divider()
        st.markdown("### 📢 오늘의 투자 가이드")

        last_date   = df_raw.index[-1]
        last_safe_p = float(df_raw[ticker_safe].iloc[-1])
        last_safe_m = float(ma_safe.iloc[-1])
        last_rate_p = float(df_raw[ticker_rate].iloc[-1])
        last_rate_m = float(ma_rate.iloc[-1])
        last_vol    = float(spy_vol.iloc[-1]) if not np.isnan(spy_vol.iloc[-1]) else 0.15

        is_bull_now = last_safe_p > last_safe_m
        is_hike_now = last_rate_p > last_rate_m
        is_aux_now  = float(df_raw[ticker_aux].iloc[-1]) > float(ma_aux.iloc[-1]) if aux_available else True

        if is_bull_now:
            current_state = "Bull_Mix" if (use_rate_filter and is_hike_now) else "Bull_Full"
            if use_aux_signal and not is_aux_now:
                current_state = "Bull_Mix"
        else:
            current_state = "Bear"

        today_w = state_to_weights(current_state, last_vol)

        st.caption(f"기준 데이터: {last_date.strftime('%Y-%m-%d')} 종가")
        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            st.metric(f"{ticker_safe} 추세", f"{last_safe_p:,.2f}", f"{last_safe_p - last_safe_m:.2f} (vs MA)")
            st.text("📈 상승장" if is_bull_now else "📉 하락장")
            st.text("🔥 금리 주의" if (is_hike_now and use_rate_filter) else "🍀 금리 안정")
        with col_g2:
            st.metric("현재 시장 변동성", f"{last_vol*100:.1f}%", delta="연환산")
            risky_now = today_w.get(ticker_risky, 0)
            cash_now  = today_w.get(ticker_cash,  0)
            safe_now  = today_w.get(ticker_safe,  0)
            st.metric(f"{ticker_risky} 권고 비중", f"{risky_now*100:.0f}%")
        with col_g3:
            if current_state == "Bear":
                st.error(f"🛑 **[하락장 방어]**\n\n👉 {ticker_safe} {safe_now*100:.0f}%")
            elif current_state == "Bull_Full":
                st.success(f"🚀 **[강한 상승장]**\n\n👉 {ticker_risky} {risky_now*100:.0f}% / {ticker_cash} {cash_now*100:.0f}%")
            elif current_state == "Bull_Mix":
                st.warning(f"⚠️ **[리스크 관리]**\n\n👉 {ticker_risky} {risky_now*100:.0f}% / {ticker_cash} {cash_now*100:.0f}%")

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

        st.divider()
        st.markdown("### 📊 성과 요약")
        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        label_bal = "Final Balance (After Tax)" if apply_tax else "Final Balance"
        r1c1.metric(label_bal,    f"{final_balance:,.0f}",    delta=f"세금: -{tax_amount:,.0f}" if tax_amount > 0 else None)
        r1c2.metric("CAGR",       f"{cagr*100:.2f}%",         delta=f"{(cagr-cagr_b)*100:.2f}%p vs BM")
        r1c3.metric("MDD",        f"{mdd*100:.2f}%")
        r1c4.metric("벤치마크 CAGR", f"{cagr_b*100:.2f}%")

        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        r2c1.metric("Sharpe Ratio",   f"{sharpe:.2f}",    help="연환산 Sharpe (rf=5%)")
        r2c2.metric("Calmar Ratio",   f"{calmar:.2f}",    help="CAGR / |MDD|, 높을수록 좋음")
        r2c3.metric("매매 승률",       f"{wr:.1f}%",       help="전환 구간 중 수익 구간 비율")
        r2c4.metric("총 매매 / 평균보유", f"{n_trades}회 / {avg_hold}일")
        st.divider()

        # ── 월별 수익률 피벗 ──────────────────────────────────────────────────
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
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Chart", "📝 Trade Logs", "📅 Monthly Returns", "⚖️ Vol Sizing"])

        with tab1:
            fig = plt.figure(figsize=(14, 24))
            gs  = gridspec.GridSpec(5, 1, height_ratios=[2, 1, 1, 1, 1], hspace=0.4)
            ax  = [fig.add_subplot(gs[i]) for i in range(5)]

            # 1. Equity
            ax[0].plot(res_df.index, res_df['Equity'],    color='firebrick', lw=1.5, label='Strategy')
            ax[0].plot(res_df.index, res_df['Benchmark'], color='gray',      lw=1.0, ls='--', alpha=0.7, label=f'B&H {ticker_safe}')
            ax[0].set_yscale('log')
            ax[0].set_title("1. Equity Curve (Log Scale)", fontsize=12)
            ax[0].legend()
            ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))

            # 2. Drawdown — Strategy vs SPY 비교
            # SPY Drawdown 계산
            spy_cum  = (1 + returns_sim[ticker_safe].fillna(0)).cumprod()
            spy_peak = spy_cum.cummax()
            spy_dd   = ((spy_cum - spy_peak) / spy_peak * 100)

            ax[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0,
                                color='blue', alpha=0.20, label='Strategy DD')
            ax[1].plot(res_df.index, res_df['Drawdown(%)'],
                       color='blue', lw=1.0, label=f'Strategy (MDD {mdd*100:.1f}%)')
            ax[1].plot(spy_dd.index, spy_dd,
                       color='tomato', lw=1.0, ls='--', alpha=0.8,
                       label=f'{ticker_safe} B&H (MDD {spy_dd.min():.1f}%)')
            ax[1].set_title("2. Drawdown (%) — Strategy vs SPY", fontsize=12)
            ax[1].axhline(0, color='black', lw=0.5)
            ax[1].legend(loc='lower left', fontsize=9)

            # 3. Trend Signal (English only)
            ax[2].plot(res_df.index, res_df['Safe_Price'], color='black',  lw=1.0, label=f'{ticker_safe} Price')
            ax[2].plot(res_df.index, res_df['Safe_MA'],    color='orange', lw=1.5, ls='--', label=f'Entry MA{int(ma_window)}')
            if use_asymmetric_ma:
                ma_exit_plot = ma_safe_exit.loc[res_df.index]
                ax[2].plot(res_df.index, ma_exit_plot, color='red', lw=1.2, ls=':', label=f'Exit MA{int(ma_exit_window)}')
            asym_label = f"Asymmetric MA ON  (Entry MA{int(ma_window)} / Exit MA{int(ma_exit_window)})" \
                         if use_asymmetric_ma else f"Asymmetric MA OFF  (MA{int(ma_window)})"
            ax[2].set_title(f"3. Trend Signal — {asym_label}", fontsize=12)
            ax[2].legend()

            # 4. 금리 시그널
            rate_s  = df_raw.loc[res_df.index, ticker_rate]
            rate_ma = ma_rate.loc[res_df.index]
            ax[3].plot(res_df.index, rate_s,  color='purple', lw=1.0, label=f'{ticker_rate}')
            ax[3].plot(res_df.index, rate_ma, color='green',  lw=1.5, ls='--', label=f'MA{rate_ma_window}')
            ax[3].set_title(f"4. Rate Signal ({ticker_rate})", fontsize=12)
            ax[3].legend()

            # 5. 변동성 & 공격비중
            vol_s = res_df['SPY_Vol(ann,%)'].dropna()
            ax[4].plot(vol_s.index, vol_s, color='teal', lw=1.0, label='SPY Ann. Vol (%)')
            ax4b = ax[4].twinx()
            ax4b.fill_between(res_df.index, res_df['Risky_Weight(%)'], 0, color='firebrick', alpha=0.15, label='Risky Weight (%)')
            ax4b.set_ylabel('Risky Weight (%)', color='firebrick')
            ax[4].set_title("5. Volatility & Risky Asset Weight", fontsize=12)
            ax[4].legend(loc='upper left')
            ax4b.legend(loc='upper right')

            st.pyplot(fig)

        with tab2:
            st.dataframe(res_df.sort_index(ascending=False), use_container_width=True)

        with tab3:
            st.dataframe(
                pivot_table.style.map(color_map).format("{:.2%}", na_rep=""),
                use_container_width=True
            )

        with tab4:
            st.markdown("#### 변동성 기반 포지션 사이징 분석")
            vol_df = res_df[['SPY_Vol(ann,%)', 'Risky_Weight(%)']].dropna()
            st.line_chart(vol_df)
            st.caption("변동성 상승 → Risky Weight 자동 감소 / 변동성 하락 → Risky Weight 자동 증가")

            if use_vol_sizing:
                st.markdown(f"""
                **사이징 공식:**
                ```
                UPRO 비중 = min(목표변동성({target_vol*100:.0f}%) / (SPY변동성 × 3), 최대비중({max_risky_w*100:.0f}%))
                ```
                **적용 범위:**
                - Bull_Full: {'변동성 사이징 적용' if apply_vol_on_bull_full else '최대 비중 고정 (CAGR 보호)'}
                - Bull_Mix: 항상 변동성 사이징 적용
                - 리밸런싱 임계값: 비중 변화 {vol_rebal_threshold*100:.0f}% 이상 시에만 매매
                """)

        # ── 엑셀 다운로드 ─────────────────────────────────────────────────────
        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                res_df.to_excel(writer, sheet_name='Daily_Log')
                pivot_table.to_excel(writer, sheet_name='Monthly_Returns')
        except Exception:
            # xlsxwriter 없으면 openpyxl로 대체
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                res_df.to_excel(writer, sheet_name='Daily_Log')
                pivot_table.to_excel(writer, sheet_name='Monthly_Returns')
            summary = pd.DataFrame({
                'Metric': ['CAGR', 'MDD', 'Sharpe', 'Calmar', '승률', '매매횟수', '평균보유일'],
                'Value':  [f"{cagr*100:.2f}%", f"{mdd*100:.2f}%",
                           f"{sharpe:.2f}", f"{calmar:.2f}",
                           f"{wr:.1f}%", n_trades, avg_hold]
            })
            summary.to_excel(writer, sheet_name='Summary', index=False)

        st.download_button(
            "📥 엑셀 결과 다운로드",
            data=output.getvalue(),
            file_name=f"Mix_Strategy_v3_{ticker_safe}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
