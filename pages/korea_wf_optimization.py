"""
한국 Bull/Bear 전략 - Walk-Forward 파라미터 최적화 v2
────────────────────────────────────────────────────
매매 로직: 전일 종가 신호 → 당일 시가 매매
최적화 대상: MA, TNX MA, Bull Full/Mix 레버리지, Bear 채권 비중
속도 최적화: numpy 벡터 연산 (신호 사전 계산)
"""

import os
import warnings
from datetime import datetime, timedelta
from itertools import product

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="한국 Bull/Bear 최적화 v2",
    page_icon="🔬",
    layout="wide",
)

st.title("🔬 한국 Bull/Bear 전략 — Walk-Forward 최적화 v2")
st.caption("전일 신호 → 당일 시가 매매 · MA + 비중 동시 최적화")


# ── KRX 로그인 ───────────────────────────────────────────────
def try_krx_login(krx_id, krx_pw):
    os.environ["KRX_ID"] = krx_id
    os.environ["KRX_PW"] = krx_pw
    try:
        from pykrx.website.comm.auth import build_krx_session, set_auth_session
        import pykrx.website.comm.webio as webio
        session = build_krx_session(krx_id, krx_pw)
        if session and session.is_authenticated:
            set_auth_session(session)
            webio._session = session
            return True, "✅ 로그인 성공"
        return False, "❌ 로그인 실패"
    except Exception as e:
        return False, f"❌ 오류: {e}"

def auto_login_from_secrets():
    if st.session_state.get("krx_ok"):
        return
    try:
        sid = st.secrets.get("KRX_ID", "")
        spw = st.secrets.get("KRX_PW", "")
        if sid and spw:
            ok, msg = try_krx_login(sid, spw)
            st.session_state.update(krx_ok=ok, krx_msg=msg, krx_from_secrets=True)
    except Exception:
        pass

auto_login_from_secrets()
TODAY = datetime.today().date()

# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")

    if st.session_state.get("krx_from_secrets"):
        with st.expander("🔐 KRX 로그인", expanded=False):
            if st.session_state.get("krx_ok"):
                st.success("🟢 자동 로그인됨 (Secrets)")
            else:
                st.error(st.session_state.get("krx_msg", "실패"))
            if st.button("🔄 캐시 초기화"):
                st.cache_data.clear()
                st.toast("완료!")
    else:
        with st.expander("🔐 KRX 로그인", expanded=True):
            krx_id = st.text_input("KRX 아이디")
            krx_pw = st.text_input("KRX 비밀번호", type="password")
            if st.button("🔓 로그인"):
                ok, msg = try_krx_login(krx_id, krx_pw)
                st.session_state.update(krx_ok=ok, krx_msg=msg)
            if st.session_state.get("krx_ok"):
                st.success("🟢 로그인됨")
            elif st.session_state.get("krx_msg"):
                st.error(st.session_state["krx_msg"])

    st.divider()
    st.subheader("📅 전체 기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2016, 1, 4))
    with c2:
        end_date = st.date_input("종료", TODAY)

    st.divider()
    st.subheader("🔄 Walk-Forward 설정")
    train_years = st.slider("Train 기간 (년)", 2, 5, 3)
    test_years  = st.slider("Test 기간 (년)", 1, 2, 1)

    st.divider()
    st.subheader("🔍 MA 파라미터 범위")
    ma_min  = st.slider("MA 최솟값", 20, 100, 40, 10)
    ma_max  = st.slider("MA 최댓값", 60, 250, 200, 10)
    ma_step = st.slider("MA 간격", 10, 40, 20, 10)

    tnx_min  = st.slider("TNX MA 최솟값", 40, 100, 60, 10)
    tnx_max  = st.slider("TNX MA 최댓값", 100, 200, 180, 10)
    tnx_step = st.slider("TNX MA 간격", 10, 30, 20, 10)

    st.divider()
    st.subheader("📦 비중 파라미터 범위")

    st.markdown("**🐂 Bull Full 레버리지 (%)**")
    bf_min  = st.slider("최솟값", 0, 50, 0, 10, key="bf_min")
    bf_max  = st.slider("최댓값", 10, 100, 50, 10, key="bf_max")
    bf_step = st.slider("간격", 10, 30, 10, 10, key="bf_step")

    st.markdown("**⚠️ Bull Mix 레버리지 (%)**")
    bm_min  = st.slider("최솟값", 10, 50, 20, 10, key="bm_min")
    bm_max  = st.slider("최댓값", 50, 100, 80, 10, key="bm_max")
    bm_step = st.slider("간격", 10, 30, 10, 10, key="bm_step")

    st.markdown("**🐻 Bear 단기채권 (%)**")
    bb_min  = st.slider("최솟값", 50, 100, 50, 10, key="bb_min")
    bb_max  = st.slider("최댓값", 50, 100, 100, 10, key="bb_max")
    bb_step = st.slider("간격", 10, 30, 10, 10, key="bb_step")

    st.divider()
    fee_rate = st.number_input("편도 수수료 (%)", value=0.015, step=0.005) / 100

    st.subheader("🎯 최적화 기준")
    opt_metric = st.selectbox("기준 지표", ["Sharpe", "CAGR", "Calmar"])

    run_btn = st.button("▶ 최적화 실행", type="primary", use_container_width=True)

if not st.session_state.get("krx_ok"):
    st.info("👈 KRX 로그인 후 실행하세요.")
    with st.expander("📋 최적화 개요", expanded=True):
        st.markdown("""
| 파라미터 | 설명 |
|----------|------|
| KOSPI200 MA | 추세 필터 이평선 |
| TNX MA | 금리 필터 이평선 |
| Bull Full 레버리지 | 최적 상승장 레버리지 비중 |
| Bull Mix 레버리지 | 금리 주의 구간 레버리지 비중 |
| Bear 단기채권 | 하락장 채권 비중 |

**Walk-Forward 방식:**
Train 구간 최적화 → Test 구간 검증 → 반복
        """)
    st.stop()

from pykrx import stock


# ── 데이터 로더 ──────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=3600)
def load_all_data(start_str, end_str):
    try:
        k200 = stock.get_index_ohlcv_by_date(start_str, end_str, "1028")["종가"]
    except:
        k200 = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")["종가"]

    e200  = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")
    elev  = stock.get_market_ohlcv_by_date(start_str, end_str, "122630")
    ebond = stock.get_market_ohlcv_by_date(start_str, end_str, "153130")

    s = (datetime.strptime(start_str, "%Y%m%d") - timedelta(days=220)).strftime("%Y-%m-%d")
    e = (datetime.strptime(end_str, "%Y%m%d") + timedelta(days=2)).strftime("%Y-%m-%d")
    tnx = yf.download("^TNX", start=s, end=e, progress=False, auto_adjust=True)["Close"].squeeze()

    return dict(
        k200=k200,
        c200=e200["종가"], o200=e200["시가"],
        clev=elev["종가"], olev=elev["시가"],
        cbond=ebond["종가"], obond=ebond["시가"],
        tnx=tnx,
    )


# ── 신호 사전 계산 (속도 최적화) ──────────────────────────────
def precompute_signals(data, common_idx, ma_list, tnx_ma_list):
    """모든 MA 조합의 신호를 미리 계산 → 재사용"""
    k200 = data["k200"].reindex(common_idx).ffill()
    tnx  = data["tnx"]
    full_idx = pd.date_range(tnx.index[0], common_idx[-1], freq="D")
    tnx_al   = tnx.reindex(full_idx).ffill().reindex(common_idx).ffill()

    signals_cache = {}

    for ma in ma_list:
        k200_ma = data["k200"].rolling(ma).mean().reindex(common_idx).ffill()
        for tnx_ma in tnx_ma_list:
            tnx_ma_s = tnx.rolling(tnx_ma).mean().reindex(full_idx).ffill().reindex(common_idx).ffill()

            # 벡터 방식 신호 계산
            sig = pd.Series("Bull_Full", index=common_idx)
            sig[k200 < k200_ma] = "Bear"
            bull_mask = k200 >= k200_ma
            mix_mask  = bull_mask & (tnx_al > tnx_ma_s)
            sig[mix_mask] = "Bull_Mix"

            # shift(1)
            sig_shifted = sig.shift(1).fillna("Bear")
            signals_cache[(ma, tnx_ma)] = sig_shifted.values

    return signals_cache


# ── 벡터 백테스트 함수 ────────────────────────────────────────
def backtest_vec(ret_cc, ret_oc, ret_co, signals,
                 w_bf_lev, w_bf_k200,
                 w_bm_lev, w_bm_k200,
                 w_bear_bond, w_bear_k200,
                 fee):
    """
    numpy 벡터 연산으로 고속 백테스트
    ret_cc: 종가→종가 수익률 배열 (3xN: 200, lev, bond)
    ret_oc: 시가→종가 수익률 배열 (3xN)
    ret_co: 종가→시가 수익률 배열 (3xN)
    signals: 신호 배열 (N,)
    """
    n = len(signals)
    nav = 1.0
    nav_arr = np.zeros(n)

    prev = signals[0]

    for i in range(n):
        st_now = signals[i]

        if i > 0 and st_now != prev:
            # 전환일: 매도(종가→시가) + 거래비용 + 매수(시가→종가)
            if prev == "Bull_Full":
                sell_r = w_bf_lev * ret_co[1,i] + w_bf_k200 * ret_co[0,i]
            elif prev == "Bull_Mix":
                sell_r = w_bm_lev * ret_co[1,i] + w_bm_k200 * ret_co[0,i]
            else:
                sell_r = w_bear_bond * ret_co[2,i] + w_bear_k200 * ret_co[0,i]

            nav *= (1 + sell_r) * (1 - fee * 2)

            if st_now == "Bull_Full":
                buy_r = w_bf_lev * ret_oc[1,i] + w_bf_k200 * ret_oc[0,i]
            elif st_now == "Bull_Mix":
                buy_r = w_bm_lev * ret_oc[1,i] + w_bm_k200 * ret_oc[0,i]
            else:
                buy_r = w_bear_bond * ret_oc[2,i] + w_bear_k200 * ret_oc[0,i]

            nav *= (1 + buy_r)
        else:
            # 보유일: 종가→종가
            if st_now == "Bull_Full":
                r = w_bf_lev * ret_cc[1,i] + w_bf_k200 * ret_cc[0,i]
            elif st_now == "Bull_Mix":
                r = w_bm_lev * ret_cc[1,i] + w_bm_k200 * ret_cc[0,i]
            else:
                r = w_bear_bond * ret_cc[2,i] + w_bear_k200 * ret_cc[0,i]

            nav *= (1 + r)

        nav_arr[i] = nav
        prev = st_now

    # 성과 계산
    n_yr   = n / 252
    cagr   = nav_arr[-1] ** (1/n_yr) - 1 if n_yr > 0.1 else 0
    peak   = np.maximum.accumulate(nav_arr)
    dd     = (nav_arr - peak) / peak
    mdd    = dd.min()
    rets   = np.diff(nav_arr) / nav_arr[:-1]
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    calmar = abs(cagr / mdd) if mdd != 0 else 0

    return cagr, mdd, sharpe, calmar


# ── 메인 실행 ─────────────────────────────────────────────────
if run_btn:
    START_STR = (start_date - timedelta(days=300)).strftime("%Y%m%d")
    END_STR   = end_date.strftime("%Y%m%d")

    prog = st.progress(0, text="데이터 로딩 중...")
    data = load_all_data(START_STR, END_STR)

    common_idx = data["c200"].loc[start_date.strftime("%Y%m%d"):END_STR].dropna().index

    # 파라미터 그리드
    ma_list   = list(range(ma_min,  ma_max  + 1, ma_step))
    tnx_list  = list(range(tnx_min, tnx_max + 1, tnx_step))
    bf_list   = list(range(bf_min,  bf_max  + 1, bf_step))
    bm_list   = list(range(bm_min,  bm_max  + 1, bm_step))
    bb_list   = list(range(bb_min,  bb_max  + 1, bb_step))

    n_ma   = len(ma_list) * len(tnx_list)
    n_wt   = len(bf_list) * len(bm_list) * len(bb_list)
    n_grid = n_ma * n_wt

    # Walk-Forward 구간
    windows = []
    s = common_idx[0]
    while True:
        train_end = s + pd.DateOffset(years=train_years)
        test_end  = train_end + pd.DateOffset(years=test_years)
        if test_end > common_idx[-1]:
            break
        t_idx = common_idx[(common_idx >= s) & (common_idx < train_end)]
        v_idx = common_idx[(common_idx >= train_end) & (common_idx < test_end)]
        if len(t_idx) > 60 and len(v_idx) > 20:
            windows.append((t_idx, v_idx))
        s = s + pd.DateOffset(years=test_years)

    if not windows:
        st.error("기간이 너무 짧습니다.")
        st.stop()

    n_windows = len(windows)
    st.info(f"📊 MA 조합: {n_ma}개 | 비중 조합: {n_wt}개 | 총: {n_grid}개 | 구간: {n_windows}개 | 총 백테스트: {n_grid*n_windows:,}회")

    # 신호 사전 계산
    prog.progress(5, text="신호 사전 계산 중...")
    signals_cache = precompute_signals(data, common_idx, ma_list, tnx_list)

    # 수익률 배열 사전 계산
    def safe_r(s, idx): return s.reindex(idx).ffill()

    c200  = safe_r(data["c200"], common_idx)
    o200  = safe_r(data["o200"], common_idx)
    clev  = safe_r(data["clev"], common_idx)
    olev  = safe_r(data["olev"], common_idx)
    cbond = safe_r(data["cbond"], common_idx)
    obond = safe_r(data["obond"], common_idx)

    # 종가→종가
    cc = np.array([
        c200.pct_change().fillna(0).values,
        clev.pct_change().fillna(0).values,
        cbond.pct_change().fillna(0).values,
    ])
    # 시가→종가
    oc = np.array([
        ((c200 - o200) / o200).fillna(0).values,
        ((clev - olev) / olev).fillna(0).values,
        ((cbond - obond) / obond).fillna(0).values,
    ])
    # 종가→시가
    co = np.array([
        ((o200 - c200.shift(1)) / c200.shift(1)).fillna(0).values,
        ((olev - clev.shift(1)) / clev.shift(1)).fillna(0).values,
        ((obond - cbond.shift(1)) / cbond.shift(1)).fillna(0).values,
    ])

    # 날짜 → 인덱스 매핑
    idx_map = {d: i for i, d in enumerate(common_idx)}

    opt_map = {"Sharpe": 2, "CAGR": 0, "Calmar": 3}
    opt_pos = opt_map[opt_metric]

    wf_results = []
    done = 0
    total = n_grid * n_windows

    for wi, (train_idx, test_idx) in enumerate(windows):
        t_pos = np.array([idx_map[d] for d in train_idx])
        v_pos = np.array([idx_map[d] for d in test_idx])

        best_score = -np.inf
        best_params = None

        for ma, tnx_ma in product(ma_list, tnx_list):
            sigs_full = signals_cache[(ma, tnx_ma)]
            sigs_t    = sigs_full[t_pos]

            cc_t = cc[:, t_pos]
            oc_t = oc[:, t_pos]
            co_t = co[:, t_pos]

            for bf, bm, bb in product(bf_list, bm_list, bb_list):
                w_bf_lev   = bf / 100
                w_bf_k200  = 1 - w_bf_lev
                w_bm_lev   = bm / 100
                w_bm_k200  = 1 - w_bm_lev
                w_bear_bond = bb / 100
                w_bear_k200 = 1 - w_bear_bond

                res = backtest_vec(cc_t, oc_t, co_t, sigs_t,
                                   w_bf_lev, w_bf_k200,
                                   w_bm_lev, w_bm_k200,
                                   w_bear_bond, w_bear_k200,
                                   fee_rate)
                score = res[opt_pos]
                if score > best_score:
                    best_score = score
                    best_params = (ma, tnx_ma, bf, bm, bb)

                done += 1

            if done % 500 == 0:
                prog.progress(
                    5 + int(done / total * 88),
                    text=f"구간 {wi+1}/{n_windows} | {done:,}/{total:,} ({done/total*100:.1f}%)"
                )

        # Test 검증
        ma, tnx_ma, bf, bm, bb = best_params
        sigs_v = signals_cache[(ma, tnx_ma)][v_pos]
        cc_v = cc[:, v_pos]
        oc_v = oc[:, v_pos]
        co_v = co[:, v_pos]

        w_bf_lev   = bf / 100
        w_bf_k200  = 1 - w_bf_lev
        w_bm_lev   = bm / 100
        w_bm_k200  = 1 - w_bm_lev
        w_bear_bond = bb / 100
        w_bear_k200 = 1 - w_bear_bond

        test_res = backtest_vec(cc_v, oc_v, co_v, sigs_v,
                                w_bf_lev, w_bf_k200,
                                w_bm_lev, w_bm_k200,
                                w_bear_bond, w_bear_k200,
                                fee_rate)

        wf_results.append({
            "Train 기간":       f"{train_idx[0].date()}~{train_idx[-1].date()}",
            "Test 기간":        f"{test_idx[0].date()}~{test_idx[-1].date()}",
            "최적 MA":          ma,
            "최적 TNX MA":      tnx_ma,
            "Bull Full 레버(%)": bf,
            "Bull Mix 레버(%)":  bm,
            "Bear 채권(%)":      bb,
            f"Train {opt_metric}": round(best_score, 3),
            "Test CAGR":        f"{test_res[0]:+.1%}",
            "Test MDD":         f"{test_res[1]:.1%}",
            "Test Sharpe":      f"{test_res[2]:.2f}",
            "Test Calmar":      f"{test_res[3]:.2f}",
        })

    prog.progress(100, text="완료!")
    import time; time.sleep(0.3)
    prog.empty()

    # ── 결과 표시 ─────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Walk-Forward 결과")
    wf_df = pd.DataFrame(wf_results)
    st.dataframe(wf_df, use_container_width=True, hide_index=True)

    # 파라미터 빈도
    st.subheader("🏆 최적 파라미터 빈도")
    param_cols = ["최적 MA", "최적 TNX MA", "Bull Full 레버(%)", "Bull Mix 레버(%)", "Bear 채권(%)"]
    cols = st.columns(len(param_cols))
    best_final = {}
    for col, p in zip(cols, param_cols):
        freq = pd.Series([r[p] for r in wf_results]).value_counts()
        best_val = int(freq.idxmax())
        best_final[p] = best_val
        col.write(f"**{p}**")
        col.dataframe(freq.reset_index().rename(columns={"index": p, "count": "빈도"}),
                      hide_index=True, height=160)

    # 추천 파라미터
    st.success(
        f"✅ **추천 파라미터** — "
        f"MA: {best_final['최적 MA']}일 | "
        f"TNX MA: {best_final['최적 TNX MA']}일 | "
        f"Bull Full 레버: {best_final['Bull Full 레버(%)']}% | "
        f"Bull Mix 레버: {best_final['Bull Mix 레버(%)']}% | "
        f"Bear 채권: {best_final['Bear 채권(%)']}%"
    )

    # 추천 파라미터 전체 기간 성과
    st.subheader("📈 추천 파라미터 전체 기간 성과")
    ma_f   = best_final["최적 MA"]
    tnx_f  = best_final["최적 TNX MA"]
    bf_f   = best_final["Bull Full 레버(%)"]
    bm_f   = best_final["Bull Mix 레버(%)"]
    bb_f   = best_final["Bear 채권(%)"]

    all_pos   = np.arange(len(common_idx))
    sigs_all  = signals_cache[(ma_f, tnx_f)]
    final_res = backtest_vec(cc, oc, co, sigs_all,
                             bf_f/100, 1-bf_f/100,
                             bm_f/100, 1-bm_f/100,
                             bb_f/100, 1-bb_f/100,
                             fee_rate)

    fc1, fc2, fc3, fc4 = st.columns(4)
    fc1.metric("연 수익률", f"{final_res[0]:+.1%}")
    fc2.metric("MDD",       f"{final_res[1]:.1%}")
    fc3.metric("Sharpe",    f"{final_res[2]:.2f}")
    fc4.metric("Calmar",    f"{final_res[3]:.2f}")

    st.download_button(
        "📥 결과 CSV 다운로드",
        wf_df.to_csv(index=False).encode("utf-8-sig"),
        "wf_opt_v2_result.csv", "text/csv"
    )

else:
    st.info("👈 사이드바에서 설정 후 **▶ 최적화 실행** 을 누르세요.")
    st.markdown("""
**최적화 파라미터 5가지:**

| 파라미터 | 범위 (기본) |
|----------|------------|
| KOSPI200 MA | 40~200일 (간격 20) |
| TNX MA | 60~180일 (간격 20) |
| Bull Full 레버리지 | 0~50% (간격 10) |
| Bull Mix 레버리지 | 20~80% (간격 10) |
| Bear 단기채권 | 50~100% (간격 10) |

**속도 최적화:**
- 신호를 사전 계산하여 재사용
- numpy 벡터 연산으로 고속 처리
    """)
