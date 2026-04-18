"""
한국 공격적 모멘텀 전략 백테스터 v3
────────────────────────────────────
수정사항: pykrx import를 로그인 완료 후로 이동 (작동하는 app.py 방식 적용)
"""

import os
import warnings
import time
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="한국 모멘텀 전략",
    page_icon="📈",
    layout="wide",
)

st.title("📈 한국 공격적 모멘텀 전략 v3")
st.caption("KOSPI/KOSDAQ 유니버스 · 6M+3M 모멘텀 · 200일선 현금 방어")


# ── KRX 로그인 함수 (app.py 방식 그대로) ────────────────────
def try_krx_login(krx_id: str, krx_pw: str) -> tuple:
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
        else:
            return False, "❌ 로그인 실패 — ID/PW를 확인하세요"
    except ImportError:
        return False, "❌ pykrx 버전이 낮습니다. pip install --upgrade pykrx 를 실행하세요."
    except Exception as e:
        err = str(e)
        if "Expecting value" in err or "JSONDecodeError" in err:
            return False, "❌ KRX 서버 응답 오류. 잠시 후 다시 시도하세요."
        return False, f"❌ 로그인 오류: {e}"


# ── Secrets 자동 로그인 (app.py 방식 그대로) ─────────────────
def auto_login_from_secrets():
    if st.session_state.get("krx_ok"):
        return
    try:
        secret_id = st.secrets.get("KRX_ID", "")
        secret_pw = st.secrets.get("KRX_PW", "")
        if secret_id and secret_pw:
            ok, msg = try_krx_login(secret_id, secret_pw)
            st.session_state["krx_ok"] = ok
            st.session_state["krx_msg"] = msg
            st.session_state["krx_from_secrets"] = True
    except Exception:
        pass

auto_login_from_secrets()


# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")

    # 로그인 UI
    if st.session_state.get("krx_from_secrets"):
        with st.expander("🔐 KRX 로그인", expanded=False):
            if st.session_state.get("krx_ok"):
                st.success("🟢 자동 로그인됨 (Secrets)")
            else:
                st.error(st.session_state.get("krx_msg", "자동 로그인 실패"))
    else:
        with st.expander("🔐 KRX 로그인 (필수)", expanded=True):
            st.markdown("[data.krx.co.kr](https://data.krx.co.kr) 무료 회원가입 후 입력")
            krx_id = st.text_input("KRX 아이디", placeholder="아이디")
            krx_pw = st.text_input("KRX 비밀번호", type="password", placeholder="비밀번호")
            if st.button("🔓 로그인", key="btn_login"):
                if not krx_id or not krx_pw:
                    st.warning("아이디와 비밀번호를 입력하세요.")
                else:
                    with st.spinner("로그인 중..."):
                        ok, msg = try_krx_login(krx_id, krx_pw)
                        st.session_state["krx_ok"] = ok
                        st.session_state["krx_msg"] = msg
            if st.session_state.get("krx_ok"):
                st.success("🟢 로그인됨")
            else:
                msg = st.session_state.get("krx_msg", "")
                if msg:
                    st.error(msg)
                else:
                    st.warning("🔴 미로그인 상태")

    st.divider()

    st.subheader("📅 기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2019, 1, 2))
    with c2:
        end_date = st.date_input("종료", datetime(2024, 12, 31))

    st.subheader("🌐 유니버스")
    market_sel = st.selectbox(
        "시장",
        ["KOSPI 전체", "KOSDAQ 전체", "KOSPI+KOSDAQ", "KOSPI 소형주", "KOSDAQ 소형주"],
        index=1,
    )

    st.subheader("📈 모멘텀")
    top_n     = st.slider("보유 종목 수", 5, 30, 20)
    mom_long  = st.slider("장기 모멘텀 (거래일)", 60, 200, 120, 5)
    mom_short = st.slider("단기 모멘텀 (거래일)", 20, 100, 60, 5)
    w_long    = st.slider("장기 가중치", 0.0, 1.0, 0.7, 0.1)
    w_short   = round(1.0 - w_long, 1)
    st.caption(f"단기 가중치: {w_short} (자동)")
    skip_1m   = st.checkbox("1M 역전 종목 제외", value=True,
                            help="최근 1개월 수익률이 음수인 종목 제외")

    st.subheader("🛡 리스크 관리")
    use_ma    = st.checkbox("KOSPI 이평선 현금 전환", value=True)
    ma_days   = st.slider("이평선 일수", 60, 250, 200, 10, disabled=not use_ma)
    use_stop  = st.checkbox("종목별 손절선", value=False)
    stop_pct  = st.slider("손절 기준 (%)", -40, -5, -20, 1, disabled=not use_stop) / 100

    st.subheader("💸 거래비용")
    fee_rate  = st.number_input("편도 수수료+슬리피지 (%)", value=0.5, step=0.1) / 100

    run_btn   = st.button("▶ 백테스트 실행", type="primary", use_container_width=True)


# ── 로그인 전 안내 ────────────────────────────────────────────
if not st.session_state.get("krx_ok"):
    st.info("""
    ### 🔐 KRX 로그인 후 사용 가능합니다

    **Streamlit Cloud Secrets 방법:**
    앱 우하단 ⚙️ Manage app → Secrets → 아래 내용 입력:
    ```toml
    KRX_ID = "본인 KRX ID"
    KRX_PW = "본인 KRX PW"
    ```

    **또는 사이드바에서 직접 로그인하세요.**
    KRX 계정 없으면 [data.krx.co.kr](https://data.krx.co.kr) 에서 무료 가입.
    """)
    st.stop()


# ── 로그인 완료 후 pykrx import ──────────────────────────────
# ★ 핵심: 로그인 후에 import 해야 인증이 유지됨
from pykrx import stock


# ── 유틸 함수들 ──────────────────────────────────────────────
def parse_markets(sel: str) -> list:
    if "KOSPI+KOSDAQ" in sel:
        return ["KOSPI", "KOSDAQ"]
    elif "KOSPI" in sel:
        return ["KOSPI"]
    else:
        return ["KOSDAQ"]


@st.cache_data(show_spinner=False, ttl=3600)
def load_kospi_index(start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_index_ohlcv_by_date(start_str, end_str, "1001")
        return df["종가"].rename("KOSPI")
    except Exception:
        # KODEX 200 대체
        df = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")
        return df["종가"].rename("KOSPI")


@st.cache_data(show_spinner=False, ttl=3600)
def load_price(ticker: str, start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
        return df["종가"]
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(show_spinner=False, ttl=86400)
def load_universe(date_str: str, markets: tuple) -> list:
    tickers = []
    for m in markets:
        try:
            tickers.extend(stock.get_market_ticker_list(date_str, market=m))
        except Exception:
            pass
    return list(set(tickers))


@st.cache_data(show_spinner=False, ttl=86400)
def load_market_cap(date_str: str, markets: tuple) -> pd.Series:
    caps = {}
    for m in markets:
        try:
            df = stock.get_market_cap_by_ticker(date_str, market=m)
            if "시가총액" in df.columns:
                caps.update(df["시가총액"].to_dict())
        except Exception:
            pass
    return pd.Series(caps)


def calc_metrics(nav: pd.Series) -> dict:
    ret    = nav.pct_change().dropna()
    n_yr   = len(nav) / 252
    cagr   = (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_yr) - 1 if n_yr > 0 else 0
    total  = nav.iloc[-1] / nav.iloc[0] - 1
    roll   = nav.cummax()
    dd     = (nav - roll) / roll
    mdd    = dd.min()
    sharpe = (ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0
    sd     = ret[ret < 0].std()
    sortino = (ret.mean() / sd * np.sqrt(252)) if sd > 0 else 0
    win_m  = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return dict(total=total, cagr=cagr, mdd=mdd,
                sharpe=sharpe, sortino=sortino, win_m=win_m, dd=dd)


# ── 백테스트 ─────────────────────────────────────────────────
if run_btn:
    START_STR = start_date.strftime("%Y%m%d")
    END_STR   = end_date.strftime("%Y%m%d")
    BM_START  = (start_date - timedelta(days=310)).strftime("%Y%m%d")
    markets   = tuple(parse_markets(market_sel))
    use_cap   = "소형주" in market_sel

    prog = st.progress(0, text="KOSPI 지수 로딩 중...")

    try:
        kospi = load_kospi_index(BM_START, END_STR)
    except Exception as e:
        st.error(f"KOSPI 데이터 로딩 실패: {e}")
        st.stop()

    kospi_ma  = kospi.rolling(ma_days).mean()
    all_bdays = kospi.loc[START_STR:END_STR].index

    rebal_dates, prev_m = [], None
    for d in all_bdays:
        if d.month != prev_m:
            rebal_dates.append(d)
            prev_m = d.month

    n_rebal   = len(rebal_dates)
    nav_s     = pd.Series(index=all_bdays, dtype=float)
    nav_s.iloc[0] = 1.0
    holdings  = {}
    prev_date = all_bdays[0]
    rebal_log = []

    for ri, rdate in enumerate(rebal_dates):
        prog.progress(max(1, int(ri / n_rebal * 88)),
                      text=f"리밸런싱 [{ri+1}/{n_rebal}] {rdate.date()} ...")

        rstr   = rdate.strftime("%Y%m%d")
        lb_str = (rdate - timedelta(days=310)).strftime("%Y%m%d")
        pstr   = prev_date.strftime("%Y%m%d")

        k_val   = kospi.get(rdate, np.nan)
        m_val   = kospi_ma.get(rdate, np.nan)
        go_cash = (use_ma and not (np.isnan(k_val) or np.isnan(m_val)) and k_val < m_val)

        period_idx = all_bdays[(all_bdays >= prev_date) & (all_bdays <= rdate)]
        if len(period_idx) > 1 and holdings:
            base   = nav_s.get(prev_date, 1.0)
            n_hold = len(holdings)
            ep_snap = dict(holdings)
            for d in period_idx[1:]:
                dstr     = d.strftime("%Y%m%d")
                port_ret = 0.0
                for tk, ep in ep_snap.items():
                    cp_s = load_price(tk, pstr, dstr)
                    cp   = cp_s.get(d, np.nan) if len(cp_s) else np.nan
                    if not np.isnan(cp) and ep > 0:
                        r = cp / ep - 1
                        if use_stop and r <= stop_pct:
                            r = stop_pct
                        port_ret += r / n_hold
                nav_s[d] = base * (1 + port_ret)

        cur_nav    = nav_s.get(rdate, nav_s.get(prev_date, 1.0))
        new_hold   = {}
        mom_scores = {}
        top_tks    = []

        if not go_cash:
            uni = load_universe(rstr, markets)
            if use_cap and uni:
                cap_s = load_market_cap(rstr, markets)
                if len(cap_s) > 0:
                    threshold = cap_s.median()
                    uni = [t for t in uni if cap_s.get(t, threshold + 1) <= threshold]

            for tk in uni:
                try:
                    pr = load_price(tk, lb_str, rstr)
                    if len(pr) < mom_long + 3:
                        continue
                    r_l  = pr.iloc[-1] / pr.iloc[-mom_long]  - 1
                    r_s  = pr.iloc[-1] / pr.iloc[-mom_short] - 1
                    r_1m = pr.iloc[-1] / pr.iloc[-21] - 1
                    if skip_1m and r_1m < 0:
                        continue
                    if r_l > -0.95:
                        mom_scores[tk] = w_long * r_l + w_short * r_s
                except Exception:
                    pass

            top_tks = sorted(mom_scores, key=mom_scores.get, reverse=True)[:top_n]

            prev_set = set(holdings.keys())
            new_set  = set(top_tks)
            turnover = len(prev_set.symmetric_difference(new_set)) / max(len(prev_set | new_set), 1)
            nav_s[rdate] = cur_nav * (1 - turnover * fee_rate * 2)

            for tk in top_tks:
                pr = load_price(tk, rstr, rstr)
                if len(pr):
                    new_hold[tk] = pr.iloc[-1]

        avg_mom = np.mean([mom_scores[t] for t in top_tks]) if top_tks else 0.0
        rebal_log.append({
            "날짜":       rdate.date(),
            "모드":       "현금" if go_cash else "투자",
            "종목수":     0 if go_cash else len(new_hold),
            "KOSPI/MA":   f"{k_val:,.0f}/{m_val:,.0f}" if not (np.isnan(k_val) or np.isnan(m_val)) else "-",
            "평균모멘텀": f"{avg_mom:.1%}" if not go_cash else "-",
        })
        holdings  = new_hold
        prev_date = rdate

    nav_s = nav_s.ffill().dropna()
    prog.progress(100, text="완료!")
    time.sleep(0.3)
    prog.empty()

    bm = kospi.reindex(nav_s.index).ffill()
    bm = bm / bm.iloc[0]
    s  = calc_metrics(nav_s)
    sb = calc_metrics(bm)

    st.divider()
    st.subheader("📊 성과 요약")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    def m(col, lbl, val, bval=None, fmt=".1%"):
        col.metric(lbl, f"{val:{fmt}}", f"BM {bval:{fmt}}" if bval is not None else None)
    m(c1, "총 수익률",  s["total"],   sb["total"])
    m(c2, "연 수익률",  s["cagr"],    sb["cagr"])
    m(c3, "MDD",        s["mdd"],     sb["mdd"])
    m(c4, "Sharpe",     s["sharpe"],  sb["sharpe"],  ".2f")
    m(c5, "Sortino",    s["sortino"], sb["sortino"], ".2f")
    m(c6, "월 승률",    s["win_m"],   None)

    st.subheader("📈 누적 수익률")
    st.line_chart(pd.DataFrame({"전략": nav_s, "KOSPI": bm}), height=320)

    st.subheader("📉 낙폭")
    st.area_chart(pd.DataFrame({"전략 DD": s["dd"], "KOSPI DD": sb["dd"]}), height=200)

    st.subheader("📅 연도별 수익률")
    ann    = nav_s.resample("YE").last().pct_change().dropna()
    bm_ann = bm.resample("YE").last().pct_change().dropna()
    ann_df = pd.DataFrame({"전략": ann, "KOSPI": bm_ann}).dropna()
    ann_df.index = ann_df.index.year
    st.bar_chart(ann_df, height=240)

    cash_cnt = sum(1 for r in rebal_log if r["모드"] == "현금")
    if cash_cnt:
        st.info(f"🛡 현금 전환 {cash_cnt}회 발생")

    with st.expander("🗒 리밸런싱 이력"):
        st.dataframe(pd.DataFrame(rebal_log), use_container_width=True, hide_index=True)

    st.divider()
    dc1, dc2 = st.columns(2)
    with dc1:
        csv = nav_s.reset_index()
        csv.columns = ["날짜", "NAV"]
        st.download_button("NAV CSV", csv.to_csv(index=False).encode("utf-8-sig"),
                           "nav_v3.csv", "text/csv")
    with dc2:
        st.download_button("리밸런싱 로그 CSV",
                           pd.DataFrame(rebal_log).to_csv(index=False).encode("utf-8-sig"),
                           "rebal_v3.csv", "text/csv")

else:
    st.info("👈 사이드바에서 로그인 후 파라미터를 설정하고 **▶ 백테스트 실행** 을 누르세요.")
    with st.expander("📋 전략 개요", expanded=True):
        st.markdown("""
| 항목 | 내용 |
|------|------|
| 유니버스 | KOSPI / KOSDAQ / 소형주 선택 |
| 팩터 | 6M × 0.7 + 3M × 0.3 가중 모멘텀 |
| 방어 | KOSPI 200일선 하회 시 전량 현금 |
| 소형주 | 시총 하위 50% 자동 필터 |
| 거래비용 | Turnover 기반 반영 |
        """)