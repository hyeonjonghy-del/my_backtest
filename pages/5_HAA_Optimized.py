import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import io
import warnings
import itertools
import xlsxwriter

# -----------------------------------------------------------------------------
# 1. Configuration & Data Loading
# -----------------------------------------------------------------------------
warnings.filterwarnings('ignore')
plt.style.use('ggplot')
st.set_page_config(page_title="HAA Enhanced Strategy", page_icon="📈", layout="wide")

CANARY_TICKERS       = ["TIP", "DBC", "VWO"]
RISKY_BASE_CANDIDATES = ["SPY", "QQQ", "IWM"]

ALL_TICKERS = list(set(
    CANARY_TICKERS + RISKY_BASE_CANDIDATES +
    ["SPY", "QQQ", "IWM", "SSO", "UPRO", "QLD", "TQQQ", "UWM",
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
# 2. 핵심 백테스트 함수 (최적화에서 반복 호출)
# -----------------------------------------------------------------------------
def run_backtest(full_df, sim_start, initial_capital,
                 canary_threshold, w_base, ticker_risky_lev,
                 ticker_safe_cash, ticker_safe_bond,
                 use_dynamic_lev, vol_window, apply_tax,
                 w_def_atk=0.0):

    needed = list(set(
        CANARY_TICKERS + RISKY_BASE_CANDIDATES +
        [ticker_risky_lev, ticker_safe_cash, ticker_safe_bond]
    ))
    available = [t for t in needed if t in full_df.columns]
    df_sel    = full_df[available].copy()

    first_valid = df_sel.apply(lambda col: col.first_valid_index())
    data_start  = max(first_valid)
    df_clean    = df_sel.loc[data_start:].ffill()

    if sim_start < data_start:
        sim_start = data_start

    df_price = df_clean.loc[sim_start:]
    if df_price.empty or len(df_price) < 60:
        return None

    def get_score(series):
        return ((series.pct_change(21) * 12) +
                (series.pct_change(63) * 4)  +
                (series.pct_change(126) * 2) +
                (series.pct_change(252) * 1))

    scores = pd.DataFrame({t: get_score(df_clean[t]) for t in available}, index=df_clean.index)
    scores = scores.loc[sim_start:]
    df_ret = df_price.pct_change().fillna(0)

    cap            = float(initial_capital)
    b_cap          = float(initial_capital)
    equity, b_eq   = [], []
    curr_w         = {ticker_safe_cash: 1.0}
    year_start_cap = cap
    trade_logs     = []

    for i in range(len(df_price)):
        date = df_price.index[i]

        if i > 0 and date.year != df_price.index[i - 1].year:
            if apply_tax:
                yr_profit = cap - year_start_cap
                if yr_profit > 2_500_000:
                    cap -= (yr_profit - 2_500_000) * 0.22
            year_start_cap = cap

        if i == 0:
            equity.append(cap); b_eq.append(b_cap); continue

        if date.month != df_price.index[i - 1].month:
            try:
                c_scores = {t: scores[t].iloc[i-1] for t in CANARY_TICKERS if t in scores.columns}
                c_pos    = sum(1 for v in c_scores.values() if v > 0)
                is_bull  = (c_pos >= canary_threshold)

                b_scores   = {t: scores[t].iloc[i-1] for t in RISKY_BASE_CANDIDATES if t in scores.columns}
                best_base  = max(b_scores, key=b_scores.get)
                best_score = b_scores[best_base]

                cash_sc = scores[ticker_safe_cash].iloc[i-1] if ticker_safe_cash in scores.columns else 0
                bond_sc = scores[ticker_safe_bond].iloc[i-1] if ticker_safe_bond in scores.columns else 0
            except Exception:
                equity.append(cap); b_eq.append(b_cap); continue

            target = {}
            if is_bull and best_score > 0:
                if use_dynamic_lev and i >= vol_window and best_base in df_ret.columns:
                    vol_a = df_ret[best_base].iloc[i-vol_window:i].std() * (252**0.5)
                    if vol_a < 0.12:   lev_w = 0.80
                    elif vol_a < 0.18: lev_w = 0.60
                    elif vol_a < 0.25: lev_w = 0.40
                    else:              lev_w = 0.20
                    base_w = 1.0 - lev_w
                else:
                    base_w = w_base
                    lev_w  = 1.0 - w_base
                target = {best_base: base_w, ticker_risky_lev: lev_w}
            else:
                if c_pos == 0:
                    s_alloc = {ticker_safe_cash: 1.0}
                elif c_pos == 1:
                    s_alloc = ({ticker_safe_bond: 0.5, ticker_safe_cash: 0.5}
                               if bond_sc > 0 else {ticker_safe_cash: 1.0})
                else:
                    s_alloc = {ticker_safe_bond: 0.7, ticker_safe_cash: 0.3}

                if w_def_atk > 0:
                    target[best_base] = w_def_atk
                    for t, w in s_alloc.items():
                        target[t] = w * (1.0 - w_def_atk)
                else:
                    target = s_alloc

            curr_w = target

        day_ret = sum(df_ret[t].iloc[i] * w for t, w in curr_w.items() if t in df_ret.columns)
        cap   *= (1 + day_ret)
        b_cap *= (1 + df_ret['SPY'].iloc[i] if 'SPY' in df_ret.columns else 0)
        equity.append(cap); b_eq.append(b_cap)

    if len(equity) < 2:
        return None

    res = pd.DataFrame({'Strategy': equity, 'Benchmark': b_eq},
                       index=df_price.index[:len(equity)])

    final_bal = res['Strategy'].iloc[-1]
    days      = (res.index[-1] - res.index[0]).days
    if days < 1: return None

    cagr = (final_bal / initial_capital) ** (365 / days) - 1
    peak = res['Strategy'].cummax()
    mdd  = ((res['Strategy'] - peak) / peak).min()
    dret = res['Strategy'].pct_change().dropna()
    sharpe = (dret.mean() / dret.std() * (252**0.5)) if dret.std() > 0 else 0
    calmar = cagr / abs(mdd) if mdd != 0 else 0

    return {
        'cagr': cagr, 'mdd': mdd, 'sharpe': sharpe,
        'calmar': calmar, 'final': final_bal, 'res': res
    }

# -----------------------------------------------------------------------------
# 3. Sidebar
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Strategy Parameters")

    with st.expander("🐦 Canary Signal (방향 1)", expanded=True):
        canary_threshold = st.slider("Bull 진입 캐너리 양수 개수", 1, 3, 2)

    with st.expander("🚀 Bull Market Assets (방향 2)", expanded=True):
        ticker_risky_lev = st.selectbox("Leverage ETF", ["UPRO", "TQQQ", "QLD", "SSO"], index=0)
        w_base = st.slider("Base Weight (%)", 0, 100, 30, step=5) / 100.0

    with st.expander("🛡️ Defense Assets (방향 3)", expanded=True):
        ticker_safe_cash = st.selectbox("Safe 1 (Cash)", ["BIL", "SGOV", "SHV"], index=0)
        ticker_safe_bond = st.selectbox("Safe 2 (Bond)", ["IEF", "TLT", "BND"], index=0)
        w_def_atk = st.slider("Bear: 공격자산 잔존 (%)", 0, 100, 0, step=5) / 100.0

    with st.expander("⚡ Dynamic Leverage (방향 4)", expanded=True):
        use_dynamic_lev = st.checkbox("변동성 기반 레버리지 자동 조절", value=True)
        vol_window = st.slider("변동성 측정 기간 (일)", 10, 60, 20, step=5)

    with st.expander("💰 Capital & Period", expanded=True):
        initial_capital = st.number_input("초기 자본 (KRW)", value=100_000_000, step=1_000_000)
        start_date      = st.date_input("시작일", pd.to_datetime("2016-01-01"))
        apply_tax       = st.checkbox("연 22% 세금 적용", value=True)

# -----------------------------------------------------------------------------
# 4. Main
# -----------------------------------------------------------------------------
full_df   = load_all_data_cached()
sim_start = pd.to_datetime(start_date)

st.title("🛡️ HAA Enhanced Strategy Report")

# ─── 탭 구성 ──────────────────────────────────────────────────────────────────
main_tab1, main_tab2 = st.tabs(["📊 단일 시뮬레이션", "🔍 파라미터 최적화"])

# =============================================================================
# TAB 1: 단일 시뮬레이션 (기존 기능 유지)
# =============================================================================
with main_tab1:
    if st.button("🚀 Run Simulation", type="primary", use_container_width=True):
        with st.spinner("시뮬레이션 실행 중..."):
            result = run_backtest(
                full_df, sim_start, initial_capital,
                canary_threshold, w_base, ticker_risky_lev,
                ticker_safe_cash, ticker_safe_bond,
                use_dynamic_lev, vol_window, apply_tax, w_def_atk
            )

        if result is None:
            st.error("데이터 부족 또는 오류가 발생했습니다.")
            st.stop()

        res    = result['res']
        cagr   = result['cagr']
        mdd    = result['mdd']
        sharpe = result['sharpe']
        calmar = result['calmar']

        # Action Plan
        needed_ap = list(set(CANARY_TICKERS + RISKY_BASE_CANDIDATES +
                             [ticker_risky_lev, ticker_safe_cash, ticker_safe_bond]))
        av_ap     = [t for t in needed_ap if t in full_df.columns]
        df_ap     = full_df[av_ap].copy()
        fv        = df_ap.apply(lambda c: c.first_valid_index())
        ds        = max(fv)
        dc        = df_ap.loc[ds:].ffill()

        def get_score(series):
            return ((series.pct_change(21)*12)+(series.pct_change(63)*4)+
                    (series.pct_change(126)*2)+(series.pct_change(252)*1))

        sc_ap = pd.DataFrame({t: get_score(dc[t]) for t in av_ap}, index=dc.index)
        sc_ap = sc_ap.loc[sim_start:] if sim_start >= ds else sc_ap

        st.divider()
        st.markdown("### 🔔 Action Plan (Today)")

        lc = {t: sc_ap[t].iloc[-2] for t in CANARY_TICKERS if t in sc_ap.columns}
        lp = sum(1 for v in lc.values() if v > 0)
        lb = {t: sc_ap[t].iloc[-2] for t in RISKY_BASE_CANDIDATES if t in sc_ap.columns}
        lbb = max(lb, key=lb.get)

        cols_c = st.columns(3)
        for idx, t in enumerate(CANARY_TICKERS):
            sc_v = lc.get(t, 0)
            with cols_c[idx]:
                st.metric(f"{'🟢' if sc_v > 0 else '🔴'} {t}", f"{sc_v:.3f}",
                          delta="양수" if sc_v > 0 else "음수",
                          delta_color="normal" if sc_v > 0 else "inverse")
        st.caption(f"캐너리 양수: {lp}/3 (기준: {canary_threshold}개 이상)")

        is_bull_now = lp >= canary_threshold
        lb_score    = lb[lbb]

        if is_bull_now and lb_score > 0:
            if use_dynamic_lev:
                rv = full_df['SPY'].pct_change().dropna().iloc[-vol_window:].std() * (252**0.5)
                if rv < 0.12:   lw = 0.80
                elif rv < 0.18: lw = 0.60
                elif rv < 0.25: lw = 0.40
                else:           lw = 0.20
                bw = 1.0 - lw
            else:
                bw, lw = w_base, 1.0 - w_base
            ft = {lbb: bw, ticker_risky_lev: lw}
            st.success(f"🚀 **Bull Market**: {lbb} (모멘텀 1위) + {ticker_risky_lev}")
        else:
            lcsh = sc_ap[ticker_safe_cash].iloc[-2] if ticker_safe_cash in sc_ap.columns else 0
            lbnd = sc_ap[ticker_safe_bond].iloc[-2] if ticker_safe_bond in sc_ap.columns else 0
            if lp == 0:
                ft = {ticker_safe_cash: 1.0}
                st.warning("🛡️ **Strong Defense**: 현금 100%")
            elif lp == 1:
                ft = ({ticker_safe_bond: 0.5, ticker_safe_cash: 0.5}
                      if lbnd > 0 else {ticker_safe_cash: 1.0})
                st.warning("🛡️ **Weak Defense**: 채권/현금 혼합")
            else:
                ft = {ticker_safe_bond: 0.7, ticker_safe_cash: 0.3}
                st.warning("⚠️ **Uncertain**: 채권 위주")

        st.markdown("**👇 Target Weights**")
        for t, w in ft.items():
            if w > 0: st.markdown(f"- **{t}**: `{w*100:.1f}%`")

        # Metrics
        st.divider()
        final_bal = res['Strategy'].iloc[-1]
        profit    = final_bal - initial_capital
        m1,m2,m3,m4,m5 = st.columns(5)
        m1.metric("Final Balance", f"{final_bal:,.0f} KRW", f"+{profit:,.0f}")
        m2.metric("CAGR",          f"{cagr*100:.2f}%")
        m3.metric("MDD",           f"{mdd*100:.2f}%")
        m4.metric("Sharpe",        f"{sharpe:.2f}")
        m5.metric("Calmar",        f"{calmar:.2f}")

        # Charts
        st.markdown("---")
        sub1, sub2, sub3 = st.tabs(["📈 Charts", "📝 Trade Logs", "📅 Monthly Returns"])

        with sub1:
            fig, ax = plt.subplots(3, 1, figsize=(12, 14),
                                   gridspec_kw={'height_ratios': [2,1,1]})
            ax[0].plot(res.index, res['Strategy'],  label='Strategy', color='#d62728', lw=2)
            ax[0].plot(res.index, res['Benchmark'], label='Benchmark (SPY)', color='gray', linestyle='--')
            ax[0].set_title("Cumulative Equity Curve (After Tax)")
            ax[0].legend()
            ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x,p: format(int(x),',')))
            ax[0].grid(True, alpha=0.3)

            res['peak'] = res['Strategy'].cummax()
            res['dd']   = (res['Strategy'] - res['peak']) / res['peak']
            ax[1].fill_between(res.index, res['dd']*100, 0, color='blue', alpha=0.3, label='Strategy DD')
            bp = res['Benchmark'].cummax()
            bd = (res['Benchmark']-bp)/bp
            ax[1].plot(res.index, bd*100, color='black', alpha=0.5, linestyle=':', label='Benchmark DD')
            ax[1].set_title("Drawdown (%)")
            ax[1].legend(); ax[1].grid(True, alpha=0.3)

            if 'SPY' in full_df.columns:
                vs = full_df['SPY'].pct_change().rolling(vol_window).std() * (252**0.5)
                vp = vs.reindex(res.index)
                ax[2].plot(vp.index, vp*100, color='orange', lw=1.5, label=f'SPY {vol_window}d Vol')
                for lvl, clr in [(12,'green'),(18,'gold'),(25,'red')]:
                    ax[2].axhline(lvl, color=clr, linestyle=':', lw=1, alpha=0.7, label=f'{lvl}%')
                ax[2].set_title("Realized Volatility")
                ax[2].legend(fontsize=8); ax[2].grid(True, alpha=0.3)

            plt.tight_layout()
            st.pyplot(fig)

        with sub2:
            st.info("Trade Log는 최적화 탭에서는 생략됩니다. 단일 시뮬레이션 결과입니다.")

        with sub3:
            mr   = res['Strategy'].resample('ME').last().pct_change().fillna(0)
            mdf  = pd.DataFrame({'Return': mr})
            mdf['Year'] = mdf.index.year; mdf['Month'] = mdf.index.month
            mp   = mdf.pivot(index='Year', columns='Month', values='Return')
            yr_s = res['Strategy'].resample('YE').last().pct_change()
            fy   = res.index[0].year
            yr_s.iloc[0] = (res['Strategy'][res.index.year==fy].iloc[-1] / res['Strategy'].iloc[0]) - 1
            mp['Total'] = mp.index.map(
                lambda y: yr_s[yr_s.index.year==y].iloc[0] if any(yr_s.index.year==y) else None)
            mp.rename(columns={i: pd.to_datetime(f"2000-{i}-01").strftime('%b')
                                for i in range(1,13)}, inplace=True)
            st.dataframe(mp.style.background_gradient(cmap='RdYlGn',axis=None,vmin=-0.1,vmax=0.1)
                         .format("{:.2%}"), use_container_width=True)

        # Excel
        st.markdown("---")
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w:
            res.to_excel(w, sheet_name='Daily Data')
            mp.to_excel(w, sheet_name='Monthly Returns')
        st.download_button("📥 Download Excel", out.getvalue(),
                           "HAA_Enhanced.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)

# =============================================================================
# TAB 2: 파라미터 최적화 (그리드 서치)
# =============================================================================
with main_tab2:
    st.markdown("### 🔍 파라미터 최적화 (Grid Search)")
    st.info("""
    아래 범위에서 모든 파라미터 조합을 백테스트하여 **최적 조합**을 찾습니다.
    - 정렬 기준: **Calmar Ratio** (CAGR ÷ |MDD|) — 수익 대비 위험 최소화
    - 조합 수가 많을수록 시간이 걸립니다 (보통 1~3분)
    """)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**🐦 캐너리 임계값**")
        opt_canary = st.multiselect("캐너리 양수 개수 후보",
                                    [1, 2, 3], default=[1, 2, 3])

        st.markdown("**🚀 Base Weight (비레버리지 비중)**")
        opt_w_base = st.multiselect("w_base 후보 (%)",
                                    [10,20,30,40,50], default=[20,30,40])

        st.markdown("**⚡ 변동성 측정 기간**")
        opt_vol_win = st.multiselect("vol_window 후보 (일)",
                                     [10,15,20,30,40,60], default=[10,20,30])

    with col2:
        st.markdown("**🔧 레버리지 ETF**")
        opt_lev = st.multiselect("Leverage ETF 후보",
                                 ["UPRO","TQQQ","QLD","SSO"], default=["UPRO","TQQQ"])

        st.markdown("**🛡️ 안전자산 (Cash)**")
        opt_cash = st.multiselect("Cash ETF 후보",
                                  ["BIL","SGOV","SHV"], default=["BIL"])

        st.markdown("**🛡️ 안전자산 (Bond)**")
        opt_bond = st.multiselect("Bond ETF 후보",
                                  ["IEF","TLT","BND"], default=["IEF","TLT"])

        opt_dyn_lev = st.checkbox("동적 레버리지 항상 ON", value=True)

    st.markdown("**📊 결과 정렬 기준**")
    sort_by = st.selectbox("최적화 목표",
                           ["Calmar (CAGR÷MDD)", "Sharpe Ratio", "CAGR", "MDD (최소)"],
                           index=0)

    total_combos = (len(opt_canary) * len(opt_w_base) * len(opt_vol_win) *
                    len(opt_lev) * len(opt_cash) * len(opt_bond))
    st.caption(f"총 조합 수: **{total_combos}개**")

    if total_combos > 500:
        st.warning("⚠️ 조합이 500개를 초과합니다. 후보를 줄이는 것을 권장합니다.")

    if st.button("🔍 최적화 실행", type="primary", use_container_width=True,
                 disabled=(total_combos == 0)):

        combos = list(itertools.product(
            opt_canary, opt_w_base, opt_vol_win,
            opt_lev, opt_cash, opt_bond
        ))

        progress_bar = st.progress(0)
        status_text  = st.empty()
        results_list = []

        for idx, (c_thr, wb, vw, lev, cash, bond) in enumerate(combos):
            status_text.text(f"진행 중: {idx+1}/{len(combos)} — "
                             f"canary={c_thr}, w_base={wb}%, vol={vw}d, "
                             f"lev={lev}, cash={cash}, bond={bond}")
            progress_bar.progress((idx + 1) / len(combos))

            r = run_backtest(
                full_df, sim_start, initial_capital,
                canary_threshold=c_thr,
                w_base=wb / 100.0,
                ticker_risky_lev=lev,
                ticker_safe_cash=cash,
                ticker_safe_bond=bond,
                use_dynamic_lev=opt_dyn_lev,
                vol_window=vw,
                apply_tax=apply_tax,
                w_def_atk=0.0
            )

            if r is not None:
                results_list.append({
                    'canary_thr': c_thr,
                    'w_base(%)':  wb,
                    'vol_window': vw,
                    'lev_etf':    lev,
                    'cash_etf':   cash,
                    'bond_etf':   bond,
                    'CAGR(%)':    round(r['cagr'] * 100, 2),
                    'MDD(%)':     round(r['mdd'] * 100, 2),
                    'Sharpe':     round(r['sharpe'], 3),
                    'Calmar':     round(r['calmar'], 3),
                    'Final(만원)': round(r['final'] / 10000),
                    '_res':       r['res']
                })

        status_text.text("✅ 최적화 완료!")
        progress_bar.progress(1.0)

        if not results_list:
            st.error("유효한 결과가 없습니다.")
            st.stop()

        df_res = pd.DataFrame(results_list)

        # 정렬
        sort_col_map = {
            "Calmar (CAGR÷MDD)": ("Calmar",   False),
            "Sharpe Ratio":       ("Sharpe",   False),
            "CAGR":               ("CAGR(%)",  False),
            "MDD (최소)":         ("MDD(%)",   True),
        }
        sc, asc = sort_col_map[sort_by]
        df_sorted = df_res.sort_values(sc, ascending=asc).reset_index(drop=True)

        st.markdown("---")
        st.markdown("### 🏆 최적화 결과 Top 20")

        # 상위 20개 표시 (res 컬럼 제거)
        display_cols = ['canary_thr','w_base(%)','vol_window','lev_etf',
                        'cash_etf','bond_etf','CAGR(%)','MDD(%)','Sharpe','Calmar','Final(만원)']
        top20 = df_sorted.head(20)[display_cols].copy()

        st.dataframe(
            top20.style
                 .background_gradient(subset=['CAGR(%)'], cmap='Greens')
                 .background_gradient(subset=['MDD(%)'],  cmap='Reds_r')
                 .background_gradient(subset=['Calmar'],  cmap='Blues')
                 .format({'CAGR(%)': '{:.2f}', 'MDD(%)': '{:.2f}',
                          'Sharpe': '{:.3f}', 'Calmar': '{:.3f}',
                          'Final(만원)': '{:,.0f}'}),
            use_container_width=True, height=600
        )

        # 1위 파라미터 강조
        best = df_sorted.iloc[0]
        st.success(f"""
        🥇 **최적 파라미터 ({sort_by} 기준)**
        - 캐너리 임계값: **{int(best['canary_thr'])}개** | Base Weight: **{int(best['w_base(%)'])}%**
        - 변동성 기간: **{int(best['vol_window'])}일** | 레버리지: **{best['lev_etf']}**
        - Cash: **{best['cash_etf']}** | Bond: **{best['bond_etf']}**
        - → CAGR: **{best['CAGR(%)']:.2f}%** | MDD: **{best['MDD(%)']:.2f}%**
        | Sharpe: **{best['Sharpe']:.3f}** | Calmar: **{best['Calmar']:.3f}**
        """)

        # 1위 에쿼티 커브 시각화
        st.markdown("### 📈 최적 파라미터 에쿼티 커브")
        best_res = df_sorted.iloc[0]['_res']
        fig2, ax2 = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios':[2,1]})

        ax2[0].plot(best_res.index, best_res['Strategy'],
                    label='Best Strategy', color='#d62728', lw=2)
        ax2[0].plot(best_res.index, best_res['Benchmark'],
                    label='Benchmark (SPY)', color='gray', linestyle='--')
        ax2[0].set_title(f"최적 전략 에쿼티 커브 — {sort_by} 1위")
        ax2[0].legend()
        ax2[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x,p: format(int(x),',')))
        ax2[0].grid(True, alpha=0.3)

        pk = best_res['Strategy'].cummax()
        dd = (best_res['Strategy'] - pk) / pk
        ax2[1].fill_between(best_res.index, dd*100, 0, color='blue', alpha=0.3, label='DD')
        ax2[1].set_title("Drawdown (%)")
        ax2[1].legend(); ax2[1].grid(True, alpha=0.3)

        plt.tight_layout()
        st.pyplot(fig2)

        # 전체 결과 Excel 다운로드
        st.markdown("---")
        out2 = io.BytesIO()
        with pd.ExcelWriter(out2, engine='xlsxwriter') as w:
            df_sorted[display_cols].to_excel(w, sheet_name='All Results', index=False)
            top20.to_excel(w, sheet_name='Top 20', index=False)

        st.download_button(
            "📥 전체 최적화 결과 Excel 다운로드",
            out2.getvalue(),
            "HAA_Optimization_Results.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
