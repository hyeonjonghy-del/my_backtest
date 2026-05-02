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

st.set_page_config(page_title="Safe/Risky/Cash Mix Strategy v6.1", page_icon="🛡️", layout="wide")

# ── 종목 옵션 정의 ────────────────────────────────────────────────────────────
SAFE_OPTIONS  = {
    "SPY  — S&P500 ETF (기본)":     "SPY",
    "QQQ  — 나스닥100 ETF":         "QQQ",
    "SOXX — 반도체 ETF":            "SOXX",
}
RISKY_OPTIONS = {
    "UPRO — S&P500 3× (기본)":      "UPRO",
    "TQQQ — 나스닥100 3×":          "TQQQ",
    "SOXL — 반도체 3×":             "SOXL",
    "SSO  — S&P500 2×":             "SSO",
    "QLD  — 나스닥100 2×":          "QLD",
    "UDOW — 다우존스 3×":           "UDOW",
}
CASH_TICKER   = "SGOV"

LEVERAGE_INFO = {
    "SPY": "1×", "QQQ": "1×", "SOXX": "1×",
    "UPRO": "3×", "TQQQ": "3×", "SOXL": "3×",
    "SSO": "2×", "QLD": "2×", "UDOW": "3×",
}

st.title("🛡️ Safe/Risky/Cash Mix Strategy v6.1 — 매매시점 비교")
st.markdown("""
**v6.1 신규 기능: 매매시점(T+1 vs T) 비교 모드**

- **T+1 모드 (기존 v6):** 시그널 확인(T) → 다음 영업일 종가에 매매. 가장 보수적·안전한 방식.
- **T 모드 (코스피식 즉시매매):** 시그널 확인(T) → 같은 날 종가에 매매. 하루 일찍 반응.
    - 실행 룰: 종가 5~10분 전(15:50) 임시 신호 확정 → MOC(Market-On-Close) 주문으로 종가 체결.
    - 한국 KRX 동시호가에서 종가 매매하는 것과 동일한 구조 (코스피 Bull/Bear 전략과 일치).
- **비교 모드:** 두 방식을 한 화면에서 동시 백테스트하여 CAGR·MDD·Sharpe 차이를 직접 확인.
""")

st.markdown("---")

# ── 사이드바 ──────────────────────────────────────────────────────────────────
with st.sidebar:

    # ── 0. 매매 시점 모드 (신규) ──────────────────────────────────────────────
    st.header("0. ⏱️ 매매 시점 모드")
    exec_mode = st.radio(
        "신호 → 매매 시점",
        options=["T+1 종가 (기존, 보수적)", "T 종가 (즉시, 코스피식)", "두 모드 비교"],
        index=2,
        help=(
            "T+1: T일 종가 신호 → T+1일 종가 매매 (look-ahead bias 절대 없음)\n"
            "T:   T일 종가 신호 → T일 종가 매매 (15:50 MOC 주문 가정)\n"
            "비교: 두 모드를 동시에 백테스트하여 차이 시각화"
        )
    )
    if exec_mode == "T+1 종가 (기존, 보수적)":
        st.info("📌 기존 v6와 동일한 보수적 방식")
    elif exec_mode == "T 종가 (즉시, 코스피식)":
        st.warning("⚠️ 종가 5~10분 전 임시 신호 확정 후 MOC 주문 필요")
    else:
        st.success("📊 두 모드 동시 백테스트 (CAGR/MDD/Sharpe 비교)")

    st.markdown("---")

    # ── 1. 종목 선택 ──────────────────────────────────────────────────────────
    st.header("1. 📌 종목 선택")
    safe_label  = st.selectbox("안전 자산 (Bear/BullMix)", list(SAFE_OPTIONS.keys()),  index=0)
    risky_label = st.selectbox("공격 자산 (BullFull/Mix)", list(RISKY_OPTIONS.keys()), index=0)

    ticker_safe  = SAFE_OPTIONS[safe_label]
    ticker_risky = RISKY_OPTIONS[risky_label]
    ticker_cash  = CASH_TICKER

    lev_safe  = LEVERAGE_INFO.get(ticker_safe,  "")
    lev_risky = LEVERAGE_INFO.get(ticker_risky, "")

    st.info(
        f"**선택된 조합:**\n\n"
        f"- 안전자산: **{ticker_safe}** ({lev_safe})\n"
        f"- 공격자산: **{ticker_risky}** ({lev_risky})\n"
        f"- 현금파킹: **{ticker_cash}**"
    )

    if ticker_safe in ["SOXX"] or ticker_risky in ["SOXL"]:
        st.warning("⚠️ 반도체 ETF는 섹터 집중 리스크가 있으며 데이터가 2010년 이후부터만 존재합니다.")
    if ticker_risky in ["TQQQ", "SOXL", "UDOW"]:
        st.warning(f"⚠️ {ticker_risky}은 3× 레버리지로 MDD가 매우 클 수 있습니다.")

    # ── 2. 전략 기본 옵션 ─────────────────────────────────────────────────────
    st.header("2. 전략 기본 옵션")
    start_date      = st.date_input("시작일", pd.to_datetime("2020-01-01"))
    initial_capital = st.number_input("초기 자본", value=100_000_000, step=1_000_000)
    fee_rate        = st.number_input("매매 수수료 (%)", value=0.02, step=0.01) / 100.0
    apply_tax       = st.checkbox("양도소득세 22% 차감", value=False)

    # ── 3. 추세 이평선 ────────────────────────────────────────────────────────
    st.header("3. 추세 이평선")
    ma_window = st.number_input("Bull 진입 이평선 (일)", value=150, min_value=5)
    use_asymmetric_ma = st.checkbox("비대칭 MA 사용 (빠른 퇴출)", value=True)
    if use_asymmetric_ma:
        ma_exit_window = st.number_input("Bear 퇴출 이평선 (일)", value=112, min_value=5)
        st.caption(f"ℹ️ 진입 MA{int(ma_window)} / 퇴출 MA{int(ma_exit_window)}")
    else:
        ma_exit_window = ma_window

    # ── 4. 금리 리스크 필터 ───────────────────────────────────────────────────
    st.header("4. 금리 리스크 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    ticker_rate     = st.text_input("금리 지표", value="^TNX")
    rate_ma_window  = st.number_input("금리 이평선 (일)", value=120)

    # ── 5. Whipsaw 필터 ───────────────────────────────────────────────────────
    st.header("5. Whipsaw 필터")
    use_whipsaw  = st.checkbox("Whipsaw 필터 사용", value=False)
    confirm_days = st.number_input("신호 확정 기간 (일)", value=1, min_value=1, max_value=10)

    # ── 6. Bear 구간 배분 ─────────────────────────────────────────────────────
    st.header("6. Bear 구간 배분")
    bear_sgov_ratio = st.slider(f"Bear 시 {ticker_cash} 비중", 0.0, 1.0, 0.5, 0.1)
    bear_safe_ratio = 1.0 - bear_sgov_ratio
    st.caption(f"Bear 배분: {ticker_safe} {bear_safe_ratio*100:.0f}% / {ticker_cash} {bear_sgov_ratio*100:.0f}%")

    # ── 7. Bull Full 구간 배분 ────────────────────────────────────────────────
    st.header("7. Bull Full 구간 배분")
    bull_full_risky_ratio = st.slider(f"Bull Full 시 {ticker_risky} 비중", 0.0, 1.0, 1.0, 0.1)
    bull_full_safe_ratio = 1.0 - bull_full_risky_ratio
    st.caption(f"Bull Full: {ticker_risky} {bull_full_risky_ratio*100:.0f}% / {ticker_safe} {bull_full_safe_ratio*100:.0f}%")

    # ── 8. Bull Mix 구간 배분 ─────────────────────────────────────────────────
    st.header("8. Bull Mix 구간 배분")
    bull_mix_risky_ratio = st.slider(f"Bull Mix 시 {ticker_risky} 비중", 0.0, 1.0, 0.6, 0.1)
    bull_mix_safe_ratio = 1.0 - bull_mix_risky_ratio
    st.caption(f"Bull Mix: {ticker_risky} {bull_mix_risky_ratio*100:.0f}% / {ticker_safe} {bull_mix_safe_ratio*100:.0f}%")

    # ── 9. 보조 시장 필터 ─────────────────────────────────────────────────────
    st.header("9. 보조 시장 필터")
    use_aux_signal = st.checkbox("보조 시장 신호 사용", value=False)
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

# ── 유틸 ──────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
#  핵심 백테스트 함수 (모드를 인자로 받음)
# ─────────────────────────────────────────────────────────────────────────────
def run_backtest(df_raw, raw_state, returns_df, sim_start, mode_label, *,
                 ticker_safe, ticker_risky, ticker_cash,
                 bear_safe_ratio, bear_sgov_ratio,
                 bull_full_risky_ratio, bull_full_safe_ratio,
                 bull_mix_risky_ratio, bull_mix_safe_ratio,
                 cash_available, initial_capital, fee_rate,
                 shift_signal: bool):
    """
    shift_signal=True  → T+1 종가 매매 (기존 v6: trade_state = raw_state.shift(1))
    shift_signal=False → T 종가 매매   (코스피식 즉시매매: trade_state = raw_state)
    """
    if shift_signal:
        trade_state = raw_state.shift(1)
    else:
        trade_state = raw_state.copy()

    df_sim      = df_raw.loc[sim_start:].copy()
    trade_state = trade_state.loc[sim_start:].fillna("Bear")
    returns_sim = returns_df.loc[sim_start:]

    def state_to_weights(state):
        if state == "Bear":
            w = {ticker_safe: bear_safe_ratio}
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

    equity = float(initial_capital)
    peak   = equity
    history = []
    curr_w  = {k: v for k, v in state_to_weights(trade_state.iloc[0]).items() if v > 0}
    equity -= equity * fee_rate
    REBAL_THRESH = 0.05

    col_risky = f"{ticker_risky}_W(%)"
    col_safe  = f"{ticker_safe}_W(%)"
    col_cash  = f"{ticker_cash}_W(%)"

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

        history.append({
            "Date":            today,
            "State":           state,
            "Mode":            mode_label,
            col_risky:         round(curr_w.get(ticker_risky, 0) * 100, 1),
            col_safe:          round(curr_w.get(ticker_safe,  0) * 100, 1),
            col_cash:          round(curr_w.get(ticker_cash,  0) * 100, 1),
            "Action":          action,
            "Equity":          round(equity),
            "Daily_Return(%)": round(day_ret * 100, 4),
            "Drawdown(%)":     round(dd * 100, 4),
        })

    res_df = pd.DataFrame(history).set_index("Date")

    final_balance = float(res_df['Equity'].iloc[-1])
    days   = (res_df.index[-1] - res_df.index[0]).days
    cagr   = (final_balance / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
    mdd    = res_df['Drawdown(%)'].min() / 100.0
    daily_ret_series = res_df['Daily_Return(%)'] / 100.0
    sharpe = sharpe_ratio(daily_ret_series)
    calmar = calmar_ratio(cagr, mdd)
    wr, avg_hold = win_rate_and_avg_hold(res_df)
    n_trades = len(res_df[res_df['Action'] == 'SWITCH'])

    return {
        "label": mode_label,
        "res_df": res_df,
        "final_balance": final_balance,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "wr": wr,
        "avg_hold": avg_hold,
        "n_trades": n_trades,
    }


# ── 메인 실행 ─────────────────────────────────────────────────────────────────
if st.button("🚀 Run Backtest v6.1", type="primary", use_container_width=True):
    with st.spinner(f"v6.1 전략 분석 중... ({ticker_safe} / {ticker_risky} / {ticker_cash})"):

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

        conditions = [
            ~is_bull,
            is_bull & is_aux & (~is_hike | ~use_rate_filter),
            is_bull & (~is_aux | (is_aux & is_hike & use_rate_filter)),
        ]
        raw_state = pd.Series(
            np.select(conditions, ["Bear", "Bull_Full", "Bull_Mix"], default="Bear"),
            index=df_raw.index
        )

        sim_start = max(pd.to_datetime(start_date), df_raw.index[0])

        # ── 모드 분기 ─────────────────────────────────────────────────────────
        common_kw = dict(
            ticker_safe=ticker_safe, ticker_risky=ticker_risky, ticker_cash=ticker_cash,
            bear_safe_ratio=bear_safe_ratio, bear_sgov_ratio=bear_sgov_ratio,
            bull_full_risky_ratio=bull_full_risky_ratio, bull_full_safe_ratio=bull_full_safe_ratio,
            bull_mix_risky_ratio=bull_mix_risky_ratio, bull_mix_safe_ratio=bull_mix_safe_ratio,
            cash_available=cash_available,
            initial_capital=initial_capital, fee_rate=fee_rate,
        )

        results = []
        if exec_mode == "T+1 종가 (기존, 보수적)":
            results.append(run_backtest(df_raw, raw_state, returns_df, sim_start,
                                        "T+1 (기존)", shift_signal=True, **common_kw))
        elif exec_mode == "T 종가 (즉시, 코스피식)":
            results.append(run_backtest(df_raw, raw_state, returns_df, sim_start,
                                        "T (즉시)", shift_signal=False, **common_kw))
        else:  # 비교
            results.append(run_backtest(df_raw, raw_state, returns_df, sim_start,
                                        "T+1 (기존)", shift_signal=True, **common_kw))
            results.append(run_backtest(df_raw, raw_state, returns_df, sim_start,
                                        "T (즉시)",   shift_signal=False, **common_kw))

        bm_ret = returns_df.loc[sim_start:][ticker_safe].fillna(0)
        benchmark_eq = (1 + bm_ret).cumprod() * initial_capital
        bm_cagr = (float(benchmark_eq.iloc[-1]) / initial_capital) ** (365.0 / (benchmark_eq.index[-1] - benchmark_eq.index[0]).days) - 1
        bm_dd = (benchmark_eq / benchmark_eq.cummax() - 1).min()

        # ── 결과 출력 ─────────────────────────────────────────────────────────
        st.divider()

        if len(results) == 1:
            r = results[0]
            st.markdown(f"### 📊 성과 요약 — {r['label']}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Final Balance", f"{r['final_balance']:,.0f}")
            c2.metric("CAGR", f"{r['cagr']*100:.2f}%", delta=f"{(r['cagr']-bm_cagr)*100:.2f}%p vs BM")
            c3.metric("MDD",  f"{r['mdd']*100:.2f}%")
            c4.metric(f"BM CAGR ({ticker_safe})", f"{bm_cagr*100:.2f}%")

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Sharpe", f"{r['sharpe']:.2f}")
            c6.metric("Calmar", f"{r['calmar']:.2f}")
            c7.metric("승률",   f"{r['wr']:.1f}%")
            c8.metric("매매/평균보유", f"{r['n_trades']}회 / {r['avg_hold']}일")
        else:
            # ── 비교 테이블 ───────────────────────────────────────────────────
            st.markdown("### 📊 매매시점 비교 — T+1 vs T")

            r1, r2 = results[0], results[1]
            comp_df = pd.DataFrame({
                "지표":           ["Final Balance", "CAGR", "MDD", "Sharpe", "Calmar", "승률", "매매횟수", "평균보유일"],
                "T+1 (기존)":     [f"{r1['final_balance']:,.0f}", f"{r1['cagr']*100:.2f}%",
                                  f"{r1['mdd']*100:.2f}%", f"{r1['sharpe']:.2f}",
                                  f"{r1['calmar']:.2f}", f"{r1['wr']:.1f}%",
                                  f"{r1['n_trades']}회", f"{r1['avg_hold']}일"],
                "T (즉시)":       [f"{r2['final_balance']:,.0f}", f"{r2['cagr']*100:.2f}%",
                                  f"{r2['mdd']*100:.2f}%", f"{r2['sharpe']:.2f}",
                                  f"{r2['calmar']:.2f}", f"{r2['wr']:.1f}%",
                                  f"{r2['n_trades']}회", f"{r2['avg_hold']}일"],
                "차이 (T - T+1)": [
                    f"{r2['final_balance']-r1['final_balance']:+,.0f}",
                    f"{(r2['cagr']-r1['cagr'])*100:+.2f}%p",
                    f"{(r2['mdd']-r1['mdd'])*100:+.2f}%p",
                    f"{r2['sharpe']-r1['sharpe']:+.2f}",
                    f"{r2['calmar']-r1['calmar']:+.2f}",
                    f"{r2['wr']-r1['wr']:+.1f}%p",
                    f"{r2['n_trades']-r1['n_trades']:+d}회",
                    f"{r2['avg_hold']-r1['avg_hold']:+d}일",
                ],
            })
            st.dataframe(comp_df, use_container_width=True, hide_index=True)

            # ── 해석 가이드 ───────────────────────────────────────────────────
            cagr_diff = (r2['cagr']-r1['cagr'])*100
            mdd_diff  = (r2['mdd']-r1['mdd'])*100
            sharpe_diff = r2['sharpe']-r1['sharpe']

            interp = []
            if cagr_diff > 0.5:
                interp.append(f"✅ T 즉시매매가 CAGR을 {cagr_diff:+.2f}%p 개선 (하루 일찍 추세 진입 효과)")
            elif cagr_diff < -0.5:
                interp.append(f"⚠️ T 즉시매매가 CAGR을 {cagr_diff:+.2f}%p 악화 (Whipsaw 비용이 추세 효과 초과)")
            else:
                interp.append(f"➖ CAGR 차이 미미 ({cagr_diff:+.2f}%p)")

            if mdd_diff > 1.0:
                interp.append(f"⚠️ T 즉시매매 MDD가 {mdd_diff:+.2f}%p 악화 (일찍 진입한 포지션이 단기 되돌림에 노출)")
            elif mdd_diff < -1.0:
                interp.append(f"✅ T 즉시매매 MDD가 {mdd_diff:+.2f}%p 개선 (Bear 진입을 하루 빨리 해서 갭다운 회피)")
            else:
                interp.append(f"➖ MDD 차이 미미 ({mdd_diff:+.2f}%p)")

            if sharpe_diff > 0.05:
                interp.append(f"✅ Sharpe 개선 ({sharpe_diff:+.2f}) — 위험조정수익률 우위")
            elif sharpe_diff < -0.05:
                interp.append(f"⚠️ Sharpe 악화 ({sharpe_diff:+.2f}) — 변동성 증가가 수익보다 큼")
            else:
                interp.append(f"➖ Sharpe 차이 미미 ({sharpe_diff:+.2f})")

            for line in interp:
                st.markdown(f"- {line}")

            # ── 비교 차트 ─────────────────────────────────────────────────────
            tab1, tab2, tab3 = st.tabs(["📈 Equity Curve 비교", "📉 Drawdown 비교", "📊 연도별 수익률"])

            with tab1:
                fig, ax = plt.subplots(figsize=(14, 6))
                ax.plot(r1['res_df'].index, r1['res_df']['Equity'], lw=1.5, label=f"T+1 (기존) — Final {r1['final_balance']:,.0f}", color='steelblue')
                ax.plot(r2['res_df'].index, r2['res_df']['Equity'], lw=1.5, label=f"T (즉시) — Final {r2['final_balance']:,.0f}", color='firebrick')
                ax.plot(benchmark_eq.index, benchmark_eq.values, lw=1.0, ls='--', alpha=0.6,
                        label=f"B&H {ticker_safe}", color='gray')
                ax.set_yscale('log')
                ax.set_title(f"Equity Curve — {ticker_safe}/{ticker_risky}/{ticker_cash}")
                ax.legend()
                ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))
                st.pyplot(fig)

            with tab2:
                fig, ax = plt.subplots(figsize=(14, 5))
                ax.fill_between(r1['res_df'].index, r1['res_df']['Drawdown(%)'], 0, alpha=0.2, color='steelblue')
                ax.plot(r1['res_df'].index, r1['res_df']['Drawdown(%)'], lw=1.0, color='steelblue',
                        label=f"T+1 (MDD {r1['mdd']*100:.1f}%)")
                ax.fill_between(r2['res_df'].index, r2['res_df']['Drawdown(%)'], 0, alpha=0.2, color='firebrick')
                ax.plot(r2['res_df'].index, r2['res_df']['Drawdown(%)'], lw=1.0, color='firebrick',
                        label=f"T (MDD {r2['mdd']*100:.1f}%)")
                ax.axhline(0, color='black', lw=0.5)
                ax.set_title("Drawdown 비교 (%)")
                ax.legend(loc='lower left')
                st.pyplot(fig)

            with tab3:
                # 연도별 수익률 비교
                def yearly_returns(res_df, init_cap):
                    eq = res_df['Equity']
                    yearly = {}
                    for yr in eq.index.year.unique():
                        yr_data = eq[eq.index.year == yr]
                        before  = eq[eq.index.year < yr]
                        start_v = float(before.iloc[-1]) if len(before) > 0 else float(init_cap)
                        yearly[yr] = float(yr_data.iloc[-1]) / start_v - 1.0
                    return pd.Series(yearly)

                y1 = yearly_returns(r1['res_df'], initial_capital)
                y2 = yearly_returns(r2['res_df'], initial_capital)

                yearly_comp = pd.DataFrame({
                    "T+1 (기존)": (y1 * 100).round(2).astype(str) + "%",
                    "T (즉시)":   (y2 * 100).round(2).astype(str) + "%",
                    "차이(%p)":   ((y2 - y1) * 100).round(2),
                })
                st.dataframe(yearly_comp, use_container_width=True)

            # ── 일별 로그 (옵션) ─────────────────────────────────────────────
            with st.expander("📝 일별 로그 보기"):
                tabA, tabB = st.tabs(["T+1 모드", "T 모드"])
                with tabA:
                    st.dataframe(r1['res_df'].sort_index(ascending=False), use_container_width=True)
                with tabB:
                    st.dataframe(r2['res_df'].sort_index(ascending=False), use_container_width=True)

            # ── 엑셀 다운로드 ─────────────────────────────────────────────────
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                comp_df.to_excel(writer, sheet_name='Comparison', index=False)
                r1['res_df'].to_excel(writer, sheet_name='T+1_Daily')
                r2['res_df'].to_excel(writer, sheet_name='T_Daily')

            st.download_button(
                "📥 비교 결과 엑셀 다운로드",
                data=output.getvalue(),
                file_name=f"Mix_Strategy_v6_1_Compare_{ticker_safe}_{ticker_risky}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
