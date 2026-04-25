"""
한국 Bull/Bear 전략 - Walk-Forward 파라미터 최적화
────────────────────────────────────────────────
매매 로직: 전일 종가 신호 → 당일 시가 매매
- 전환일: 전일보유 ETF → 당일 시가 매도 + 새 ETF → 당일 시가 매수 → 종가 평가
- 보유일: 전일 종가 → 당일 종가 수익률

최적화 파라미터:
- KOSPI200 이평선 (MA): 40~200일
- TNX 이평선 (MA_TNX): 60~180일
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
    page_title="한국 Bull/Bear 최적화",
    page_icon="🔬",
    layout="wide",
)

st.title("🔬 한국 Bull/Bear 전략 — Walk-Forward 최적화")
st.caption("전일 신호 → 당일 시가 매매 · 보유일 종가→종가 수익률")


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
    st.caption(f"예: Train {train_years}년 → Test {test_years}년 슬라이딩")

    st.divider()
    st.subheader("🔍 최적화 파라미터 범위")
    ma_min  = st.slider("MA 최솟값", 20, 100, 40, 10)
    ma_max  = st.slider("MA 최댓값", 60, 250, 200, 10)
    ma_step = st.slider("MA 간격", 5, 30, 20, 5)

    tnx_min  = st.slider("TNX MA 최솟값", 40, 100, 60, 10)
    tnx_max  = st.slider("TNX MA 최댓값", 100, 200, 180, 10)
    tnx_step = st.slider("TNX MA 간격", 10, 30, 20, 10)

    st.divider()
    st.subheader("📦 포트폴리오 비중 (고정)")
    bf_lev   = st.slider("Bull Full 레버리지 (%)", 0, 100, 20, 5)
    bm_lev   = st.slider("Bull Mix 레버리지 (%)", 0, 100, 50, 5)
    bear_bond = st.slider("Bear 단기채권 (%)", 0, 100, 100, 5)
    fee_rate = st.number_input("편도 수수료 (%)", value=0.015, step=0.005) / 100

    st.subheader("🎯 최적화 기준")
    opt_metric = st.selectbox("기준 지표", ["Sharpe", "CAGR", "Calmar"])

    run_btn = st.button("▶ 최적화 실행", type="primary", use_container_width=True)

if not st.session_state.get("krx_ok"):
    st.info("👈 KRX 로그인 후 실행하세요.")
    st.stop()

from pykrx import stock


# ── 데이터 로더 ──────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=3600)
def load_all_data(start_str, end_str):
    """전체 데이터를 한 번에 로드"""
    # KOSPI200 지수
    try:
        k200 = stock.get_index_ohlcv_by_date(start_str, end_str, "1028")["종가"]
    except:
        k200 = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")["종가"]

    # ETF 종가 & 시가
    e200  = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")
    elev  = stock.get_market_ohlcv_by_date(start_str, end_str, "122630")
    ebond = stock.get_market_ohlcv_by_date(start_str, end_str, "153130")

    # TNX
    s = (datetime.strptime(start_str, "%Y%m%d") - timedelta(days=220)).strftime("%Y-%m-%d")
    e = (datetime.strptime(end_str,   "%Y%m%d") + timedelta(days=2)).strftime("%Y-%m-%d")
    tnx = yf.download("^TNX", start=s, end=e, progress=False, auto_adjust=True)["Close"].squeeze()

    return dict(
        k200=k200,
        c200=e200["종가"], o200=e200["시가"],
        clev=elev["종가"], olev=elev["시가"],
        cbond=ebond["종가"], obond=ebond["시가"],
        tnx=tnx,
    )


# ── 백테스트 함수 ─────────────────────────────────────────────
def backtest(data, idx, ma, tnx_ma,
             w_bf_lev, w_bf_k200,
             w_bm_lev, w_bm_k200,
             w_bear_bond, w_bear_k200,
             fee):
    """
    idx: 백테스트할 날짜 인덱스
    반환: (cagr, mdd, sharpe, calmar)
    """
    k200  = data["k200"]
    c200  = data["c200"]
    o200  = data["o200"]
    clev  = data["clev"]
    olev  = data["olev"]
    cbond = data["cbond"]
    obond = data["obond"]
    tnx   = data["tnx"]

    # MA 계산
    k200_ma = k200.rolling(ma).mean()

    # TNX aligned
    full_idx = pd.date_range(tnx.index[0], idx[-1], freq="D")
    tnx_al   = tnx.reindex(full_idx).ffill().reindex(idx).ffill()
    tnx_ma_s = tnx.rolling(tnx_ma).mean().reindex(full_idx).ffill().reindex(idx).ffill()

    # 수익률
    def safe(s, i): return s.reindex(i).ffill()

    cc200  = safe(c200, idx).pct_change().fillna(0)
    cclev  = safe(clev, idx).pct_change().fillna(0)
    ccbond = safe(cbond, idx).pct_change().fillna(0)

    oc200  = ((safe(c200,idx) - safe(o200,idx)) / safe(o200,idx)).fillna(0)
    oclev  = ((safe(clev,idx) - safe(olev,idx)) / safe(olev,idx)).fillna(0)
    ocbond = ((safe(cbond,idx) - safe(obond,idx)) / safe(obond,idx)).fillna(0)

    co200  = ((safe(o200,idx) - safe(c200,idx).shift(1)) / safe(c200,idx).shift(1)).fillna(0)
    colev  = ((safe(olev,idx) - safe(clev,idx).shift(1)) / safe(clev,idx).shift(1)).fillna(0)
    cobond = ((safe(obond,idx) - safe(cbond,idx).shift(1)) / safe(cbond,idx).shift(1)).fillna(0)

    k200_r  = safe(k200, idx).ffill()
    k200_mr = safe(k200_ma, idx).ffill()

    # 신호 생성 + shift
    sigs = []
    for d in idx:
        k  = k200_r[d]
        km = k200_mr[d]
        t  = tnx_al[d]
        tm = tnx_ma_s[d]
        if np.isnan(k) or np.isnan(km):
            sigs.append(sigs[-1] if sigs else "Bear")
        elif k < km:
            sigs.append("Bear")
        elif not np.isnan(t) and not np.isnan(tm) and t > tm:
            sigs.append("Bull_Mix")
        else:
            sigs.append("Bull_Full")

    sig_s = pd.Series(sigs, index=idx).shift(1).fillna("Bear")

    def port_ret(r200, rlev, rbond, st):
        if st == "Bull_Full":
            return w_bf_lev * rlev + w_bf_k200 * r200
        elif st == "Bull_Mix":
            return w_bm_lev * rlev + w_bm_k200 * r200
        return w_bear_bond * rbond + w_bear_k200 * r200

    nav = 1.0
    nav_list = []
    prev = None

    for d in idx:
        st = sig_s[d]
        if prev is not None and st != prev:
            nav *= (1 + port_ret(co200[d], colev[d], cobond[d], prev))
            nav *= (1 - fee * 2)
            nav *= (1 + port_ret(oc200[d], oclev[d], ocbond[d], st))
        else:
            nav *= (1 + port_ret(cc200[d], cclev[d], ccbond[d], st))
        nav_list.append(nav)
        prev = st

    nav_s = pd.Series(nav_list, index=idx)
    ret_s = nav_s.pct_change().dropna()
    n_yr  = len(nav_s) / 252
    if n_yr < 0.1:
        return 0, 0, 0, 0
    cagr   = nav_s.iloc[-1] ** (1/n_yr) - 1
    roll   = nav_s.cummax()
    mdd    = ((nav_s - roll) / roll).min()
    sharpe = (ret_s.mean() / ret_s.std() * np.sqrt(252)) if ret_s.std() > 0 else 0
    calmar = abs(cagr / mdd) if mdd != 0 else 0
    return cagr, mdd, sharpe, calmar


# ── 메인 실행 ─────────────────────────────────────────────────
if run_btn:
    START_STR = (start_date - timedelta(days=300)).strftime("%Y%m%d")
    END_STR   = end_date.strftime("%Y%m%d")

    prog = st.progress(0, text="데이터 로딩 중...")
    data = load_all_data(START_STR, END_STR)

    # 공통 인덱스
    common_idx = data["c200"].loc[start_date.strftime("%Y%m%d"):END_STR].dropna().index

    # 비중
    w_bf_lev   = bf_lev   / 100
    w_bf_k200  = (100 - bf_lev)   / 100
    w_bm_lev   = bm_lev   / 100
    w_bm_k200  = (100 - bm_lev)   / 100
    w_bear_bond = bear_bond / 100
    w_bear_k200 = (100 - bear_bond) / 100

    # Walk-Forward 구간 생성
    windows = []
    s = common_idx[0]
    while True:
        train_end = s + pd.DateOffset(years=train_years)
        test_end  = train_end + pd.DateOffset(years=test_years)
        if test_end > common_idx[-1]:
            break
        train_idx = common_idx[(common_idx >= s) & (common_idx < train_end)]
        test_idx  = common_idx[(common_idx >= train_end) & (common_idx < test_end)]
        if len(train_idx) > 60 and len(test_idx) > 20:
            windows.append((train_idx, test_idx))
        s = s + pd.DateOffset(years=test_years)

    if not windows:
        st.error("기간이 너무 짧습니다. 시작일을 앞당기거나 Train/Test 기간을 줄여주세요.")
        st.stop()

    # 파라미터 그리드
    ma_range  = range(ma_min, ma_max + 1, ma_step)
    tnx_range = range(tnx_min, tnx_max + 1, tnx_step)
    grid      = list(product(ma_range, tnx_range))
    n_grid    = len(grid)
    n_windows = len(windows)

    st.info(f"📊 파라미터 조합: {n_grid}개 | Walk-Forward 구간: {n_windows}개 | 총 백테스트: {n_grid * n_windows}회")

    # Walk-Forward 최적화
    wf_results = []
    opt_map = {"Sharpe": 2, "CAGR": 0, "Calmar": 3}
    opt_idx = opt_map[opt_metric]

    total_runs = n_grid * n_windows
    done = 0

    for wi, (train_idx, test_idx) in enumerate(windows):
        # Train: 최적 파라미터 탐색
        best_score = -np.inf
        best_ma, best_tnx = ma_range[0], tnx_range[0]

        for ma, tnx_ma in grid:
            res = backtest(data, train_idx, ma, tnx_ma,
                           w_bf_lev, w_bf_k200,
                           w_bm_lev, w_bm_k200,
                           w_bear_bond, w_bear_k200, fee_rate)
            score = res[opt_idx]
            if score > best_score:
                best_score = score
                best_ma, best_tnx = ma, tnx_ma
            done += 1
            if done % 20 == 0:
                prog.progress(int(done / total_runs * 90),
                              text=f"구간 {wi+1}/{n_windows} 최적화 중... ({done}/{total_runs})")

        # Test: 최적 파라미터로 검증
        test_res = backtest(data, test_idx, best_ma, best_tnx,
                            w_bf_lev, w_bf_k200,
                            w_bm_lev, w_bm_k200,
                            w_bear_bond, w_bear_k200, fee_rate)

        wf_results.append({
            "구간":          f"{train_idx[0].date()}~{train_idx[-1].date()}",
            "Test기간":      f"{test_idx[0].date()}~{test_idx[-1].date()}",
            "최적 MA":       best_ma,
            "최적 TNX MA":   best_tnx,
            f"Train {opt_metric}": round(best_score, 3),
            "Test CAGR":    f"{test_res[0]:+.1%}",
            "Test MDD":     f"{test_res[2]:.1%}",  # sharpe
            "Test Sharpe":  f"{test_res[2]:.2f}",
        })

    prog.progress(100, text="완료!")
    import time; time.sleep(0.3)
    prog.empty()

    # ── 결과 표시 ─────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Walk-Forward 결과")
    wf_df = pd.DataFrame(wf_results)
    st.dataframe(wf_df, use_container_width=True, hide_index=True)

    # 최적 파라미터 빈도
    st.subheader("🏆 최적 파라미터 빈도")
    c1, c2 = st.columns(2)
    with c1:
        ma_freq = pd.Series([r["최적 MA"] for r in wf_results]).value_counts()
        st.write("**KOSPI200 MA 빈도:**")
        st.dataframe(ma_freq.reset_index().rename(columns={"index":"MA","count":"빈도"}),
                     hide_index=True)
    with c2:
        tnx_freq = pd.Series([r["최적 TNX MA"] for r in wf_results]).value_counts()
        st.write("**TNX MA 빈도:**")
        st.dataframe(tnx_freq.reset_index().rename(columns={"index":"TNX MA","count":"빈도"}),
                     hide_index=True)

    # 추천 파라미터
    best_ma_final  = int(ma_freq.idxmax())
    best_tnx_final = int(tnx_freq.idxmax())

    st.success(f"✅ **추천 파라미터: KOSPI200 MA = {best_ma_final}일 | TNX MA = {best_tnx_final}일**")

    # 추천 파라미터로 전체 기간 백테스트
    st.subheader(f"📈 추천 파라미터 전체 기간 성과 (MA{best_ma_final}, TNX MA{best_tnx_final})")
    final_res = backtest(data, common_idx, best_ma_final, best_tnx_final,
                         w_bf_lev, w_bf_k200,
                         w_bm_lev, w_bm_k200,
                         w_bear_bond, w_bear_k200, fee_rate)

    fc1, fc2, fc3, fc4 = st.columns(4)
    fc1.metric("연 수익률", f"{final_res[0]:+.1%}")
    fc2.metric("MDD",       f"{final_res[1]:.1%}")
    fc3.metric("Sharpe",    f"{final_res[2]:.2f}")
    fc4.metric("Calmar",    f"{final_res[3]:.2f}")

    st.download_button(
        "📥 결과 CSV 다운로드",
        wf_df.to_csv(index=False).encode("utf-8-sig"),
        "wf_optimization_result.csv", "text/csv"
    )

else:
    st.info("👈 사이드바에서 설정 후 **▶ 최적화 실행** 을 누르세요.")
    st.markdown("""
**Walk-Forward 최적화 방식:**

| 단계 | 내용 |
|------|------|
| 1 | 전체 기간을 Train/Test 구간으로 분할 |
| 2 | Train 구간에서 최적 MA 파라미터 탐색 |
| 3 | Test 구간에서 그 파라미터로 실제 성과 검증 |
| 4 | 여러 구간에서 일관되게 좋은 파라미터 선택 |

**단순 Grid Search와 차이:**
- Grid Search: 과거 전체에 최적화 → 과적합 위험
- Walk-Forward: 미래 구간으로 검증 → 실전에 더 신뢰할 수 있음
    """)
