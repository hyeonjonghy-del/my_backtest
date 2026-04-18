"""
한국 공격적 모멘텀 전략 백테스터 v5
────────────────────────────────────
[v5 버그 수정]
- 포트폴리오 수익률 정규화 버그 수정 (유효 종목만으로 평균 계산)
- 초기 lookback 부족 기간 처리 (데이터 없으면 첫 신호 스킵)
- 진단 정보 추가 (각 리밸런싱별 유효 종목 수)
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
    page_title="한국 모멘텀 전략 v5",
    page_icon="📈",
    layout="wide",
)

st.title("📈 한국 공격적 모멘텀 전략 v5")
st.caption("Bulk 로딩 · 버그 수정 · KOSPI/KOSDAQ · 6M+3M 모멘텀 · 200일선 현금 방어")


# ── KRX 로그인 ───────────────────────────────────────────────
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
        return False, "❌ 로그인 실패 — ID/PW를 확인하세요"
    except ImportError:
        return False, "❌ pykrx 업그레이드 필요: pip install --upgrade pykrx"
    except Exception as e:
        if "Expecting value" in str(e):
            return False, "❌ KRX 서버 오류. 잠시 후 재시도하세요."
        return False, f"❌ 로그인 오류: {e}"


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


# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")

    if st.session_state.get("krx_from_secrets"):
        with st.expander("🔐 KRX 로그인", expanded=False):
            if st.session_state.get("krx_ok"):
                st.success("🟢 자동 로그인됨 (Secrets)")
            else:
                st.error(st.session_state.get("krx_msg", "자동 로그인 실패"))
            if st.button("🔄 캐시 초기화"):
                st.cache_data.clear()
                st.toast("캐시 초기화 완료!")
    else:
        with st.expander("🔐 KRX 로그인 (필수)", expanded=True):
            st.markdown("[data.krx.co.kr](https://data.krx.co.kr) 무료 가입 후 입력")
            krx_id = st.text_input("KRX 아이디")
            krx_pw = st.text_input("KRX 비밀번호", type="password")
            if st.button("🔓 로그인"):
                if krx_id and krx_pw:
                    ok, msg = try_krx_login(krx_id, krx_pw)
                    st.session_state.update(krx_ok=ok, krx_msg=msg)
                else:
                    st.warning("아이디와 비밀번호를 입력하세요.")
            if st.session_state.get("krx_ok"):
                st.success("🟢 로그인됨")
                if st.button("🔄 캐시 초기화", key="cc2"):
                    st.cache_data.clear()
            elif st.session_state.get("krx_msg"):
                st.error(st.session_state["krx_msg"])

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
    skip_1m   = st.checkbox("1M 역전 종목 제외", value=True)

    st.subheader("🛡 리스크 관리")
    use_ma   = st.checkbox("KOSPI 이평선 현금 전환", value=True)
    ma_days  = st.slider("이평선 일수", 60, 250, 200, 10, disabled=not use_ma)
    use_stop = st.checkbox("종목별 손절선", value=False)
    stop_pct = st.slider("손절 기준 (%)", -40, -5, -20, 1, disabled=not use_stop) / 100

    st.subheader("💸 거래비용")
    fee_rate = st.number_input("편도 수수료+슬리피지 (%)", value=0.5, step=0.1) / 100

    run_btn = st.button("▶ 백테스트 실행", type="primary", use_container_width=True)


# ── 로그인 전 안내 ────────────────────────────────────────────
if not st.session_state.get("krx_ok"):
    st.info("""
    ### 🔐 KRX 로그인 후 사용 가능합니다
    Streamlit Cloud → 앱 우하단 ⚙️ Manage app → Secrets:
    ```toml
    KRX_ID = "본인 KRX ID"
    KRX_PW = "본인 KRX PW"
    ```
    """)
    st.stop()

from pykrx import stock


# ── 데이터 로더 ──────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=3600)
def bulk_load_date(date_str: str, markets: tuple) -> dict:
    """날짜별 전 종목 종가 한 번에 반환 {ticker: close}"""
    result = {}
    for mkt in markets:
        try:
            df = stock.get_market_ohlcv_by_ticker(date_str, market=mkt)
            if "종가" in df.columns:
                result.update(df["종가"].to_dict())
        except Exception:
            pass
    return result


@st.cache_data(show_spinner=False, ttl=3600)
def load_kospi_index(start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_index_ohlcv_by_date(start_str, end_str, "1001")
        return df["종가"].rename("KOSPI")
    except Exception:
        df = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")
        return df["종가"].rename("KOSPI")


def calc_metrics(nav: pd.Series) -> dict:
    ret     = nav.pct_change().dropna()
    n_yr    = len(nav) / 252
    cagr    = (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_yr) - 1 if n_yr > 0 else 0
    total   = nav.iloc[-1] / nav.iloc[0] - 1
    roll    = nav.cummax()
    dd      = (nav - roll) / roll
    mdd     = dd.min()
    sharpe  = (ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0
    sd      = ret[ret < 0].std()
    sortino = (ret.mean() / sd * np.sqrt(252)) if sd > 0 else 0
    win_m   = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return dict(total=total, cagr=cagr, mdd=mdd,
                sharpe=sharpe, sortino=sortino, win_m=win_m, dd=dd)


# ── 백테스트 ─────────────────────────────────────────────────
if run_btn:
    START_STR = start_date.strftime("%Y%m%d")
    END_STR   = end_date.strftime("%Y%m%d")
    # lookback 충분히 확보 (6개월 + 버퍼)
    BM_START  = (start_date - timedelta(days=400)).strftime("%Y%m%d")
    markets   = tuple(["KOSPI"] if "KOSPI" in market_sel and "KOSDAQ" not in market_sel
                      else ["KOSDAQ"] if "KOSDAQ" in market_sel and "KOSPI" not in market_sel
                      else ["KOSPI", "KOSDAQ"])

    prog = st.progress(0, text="KOSPI 지수 로딩 중...")

    try:
        kospi_full = load_kospi_index(BM_START, END_STR)
    except Exception as e:
        st.error(f"KOSPI 데이터 로딩 실패: {e}")
        st.stop()

    kospi_ma_full = kospi_full.rolling(ma_days).mean()

    # 전체 영업일 (lookback 포함)
    all_bdays_full = list(kospi_full.index)
    # 백테스트 구간 영업일
    all_bdays = kospi_full.loc[START_STR:END_STR].index

    # 월별 리밸런싱 날짜
    rebal_dates, prev_m = [], None
    for d in all_bdays:
        if d.month != prev_m:
            rebal_dates.append(d)
            prev_m = d.month

    # ── 필요한 날짜 계산 (전체 bdays_full 기준) ──────────────
    needed_dates = set()
    rebal_lookbacks = {}

    for rdate in rebal_dates:
        idx = all_bdays_full.index(rdate)
        # lookback이 충분한지 확인
        has_long  = idx >= mom_long
        has_short = idx >= mom_short
        has_1m    = idx >= 21

        lb_long  = all_bdays_full[idx - mom_long]  if has_long  else None
        lb_short = all_bdays_full[idx - mom_short] if has_short else None
        lb_1m    = all_bdays_full[idx - 21]        if has_1m    else None

        rebal_lookbacks[rdate] = dict(
            cur=rdate,
            long=lb_long,
            short=lb_short,
            m1=lb_1m,
            valid=has_long and has_short  # 충분한 데이터가 있는 경우만 신호 사용
        )
        needed_dates.add(rdate)
        if lb_long:  needed_dates.add(lb_long)
        if lb_short: needed_dates.add(lb_short)
        if lb_1m:    needed_dates.add(lb_1m)

    needed_sorted = sorted(needed_dates)
    n_dates = len(needed_sorted)

    # ── Bulk 데이터 로딩 ──────────────────────────────────────
    price_db = {}
    for i, date in enumerate(needed_sorted):
        prog.progress(
            int(i / n_dates * 80),
            text=f"📥 시장 데이터 로딩 [{i+1}/{n_dates}] {date.date()} ..."
        )
        dstr = date.strftime("%Y%m%d")
        price_db[date] = bulk_load_date(dstr, markets)

    # ── 백테스트 루프 ─────────────────────────────────────────
    nav_points = {}
    cur_nav    = 1.0
    holdings   = {}   # {ticker: entry_price}
    rebal_log  = []
    n_rebal    = len(rebal_dates)

    for ri, rdate in enumerate(rebal_dates):
        prog.progress(
            80 + int(ri / n_rebal * 18),
            text=f"📊 백테스트 [{ri+1}/{n_rebal}] {rdate.date()} ..."
        )

        lbs        = rebal_lookbacks[rdate]
        cur_prices = price_db.get(rdate, {})

        # ── [버그수정] 포트폴리오 수익률: 유효 종목만으로 평균 ─
        if ri > 0 and holdings:
            valid_rets = []
            for tk, ep in holdings.items():
                cp = cur_prices.get(tk, np.nan)
                if not np.isnan(cp) and ep > 0 and cp > 0:
                    r = cp / ep - 1
                    if use_stop and r <= stop_pct:
                        r = stop_pct
                    valid_rets.append(r)
            if valid_rets:
                port_ret = float(np.mean(valid_rets))  # 유효 종목 평균
                cur_nav  = cur_nav * (1 + port_ret)

        # ── 시장 국면 필터 ────────────────────────────────────
        k_val   = kospi_full.get(rdate, np.nan)
        m_val   = kospi_ma_full.get(rdate, np.nan)
        go_cash = (use_ma
                   and not (np.isnan(k_val) or np.isnan(m_val))
                   and k_val < m_val)

        # ── 모멘텀 스코어 ────────────────────────────────────
        new_hold   = {}
        mom_scores = {}
        top_tks    = []
        n_scored   = 0

        if not go_cash and lbs["valid"]:
            long_prices  = price_db.get(lbs["long"],  {})
            short_prices = price_db.get(lbs["short"], {})
            m1_prices    = price_db.get(lbs["m1"],    {}) if lbs["m1"] else {}

            for tk, cp in cur_prices.items():
                if cp <= 0 or np.isnan(cp):
                    continue
                lp = long_prices.get(tk, np.nan)
                sp = short_prices.get(tk, np.nan)
                mp = m1_prices.get(tk, np.nan) if m1_prices else np.nan

                if np.isnan(lp) or lp <= 0 or np.isnan(sp) or sp <= 0:
                    continue

                r_l  = cp / lp  - 1
                r_s  = cp / sp  - 1
                r_1m = (cp / mp - 1) if (not np.isnan(mp) and mp > 0) else 0

                if skip_1m and r_1m < 0:
                    continue
                if r_l > -0.95:
                    mom_scores[tk] = w_long * r_l + w_short * r_s
                    n_scored += 1

            top_tks = sorted(mom_scores, key=mom_scores.get, reverse=True)[:top_n]

            # 거래비용 (turnover 기반)
            prev_set = set(holdings.keys())
            new_set  = set(top_tks)
            turnover = (len(prev_set.symmetric_difference(new_set))
                        / max(len(prev_set | new_set), 1))
            cur_nav  = cur_nav * (1 - turnover * fee_rate * 2)

            for tk in top_tks:
                ep = cur_prices.get(tk, np.nan)
                if not np.isnan(ep) and ep > 0:
                    new_hold[tk] = ep

        elif not go_cash and not lbs["valid"]:
            # lookback 부족 → 현금 유지 (신호 없음)
            go_cash = True

        nav_points[rdate] = cur_nav
        avg_mom = (np.mean([mom_scores[t] for t in top_tks])
                   if top_tks else 0.0)

        rebal_log.append({
            "날짜":         rdate.date(),
            "모드":         "현금" if go_cash else "투자",
            "편입종목수":   0 if go_cash else len(new_hold),
            "스코어산출수": n_scored,
            "KOSPI":        f"{k_val:,.0f}" if not np.isnan(k_val) else "-",
            "200MA":        f"{m_val:,.0f}" if not np.isnan(m_val) else "-",
            "평균모멘텀":   f"{avg_mom:.1%}" if not go_cash and top_tks else "-",
            "NAV":          f"{cur_nav:.4f}",
        })
        holdings = new_hold

    prog.progress(100, text="완료!")
    time.sleep(0.3)
    prog.empty()

    # ── NAV 시계열 (월별 → 일별 보간) ────────────────────────
    nav_monthly = pd.Series(nav_points).sort_index()
    nav_s = (nav_monthly
             .reindex(all_bdays)
             .interpolate(method="time")
             .ffill()
             .bfill())

    bm = kospi_full.reindex(nav_s.index).ffill()
    bm = bm / bm.iloc[0]
    s  = calc_metrics(nav_s)
    sb = calc_metrics(bm)

    # ── 대시보드 ──────────────────────────────────────────────
    st.divider()
    st.subheader("📊 성과 요약")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    def show_m(col, lbl, val, bval=None, fmt=".1%"):
        col.metric(lbl, f"{val:{fmt}}",
                   f"BM {bval:{fmt}}" if bval is not None else None)
    show_m(c1, "총 수익률",  s["total"],   sb["total"])
    show_m(c2, "연 수익률",  s["cagr"],    sb["cagr"])
    show_m(c3, "MDD",        s["mdd"],     sb["mdd"])
    show_m(c4, "Sharpe",     s["sharpe"],  sb["sharpe"],  ".2f")
    show_m(c5, "Sortino",    s["sortino"], sb["sortino"], ".2f")
    show_m(c6, "월 승률",    s["win_m"],   None)

    st.subheader("📈 누적 수익률")
    st.line_chart(pd.DataFrame({"전략": nav_s, "KOSPI(BM)": bm}), height=320)

    st.subheader("📉 낙폭 (Drawdown)")
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

    with st.expander("🗒 리밸런싱 이력 (상세)"):
        log_df = pd.DataFrame(rebal_log)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

    st.divider()
    dc1, dc2 = st.columns(2)
    with dc1:
        csv = nav_s.reset_index()
        csv.columns = ["날짜", "NAV"]
        st.download_button("NAV CSV 다운로드",
                           csv.to_csv(index=False).encode("utf-8-sig"),
                           "nav_v5.csv", "text/csv")
    with dc2:
        st.download_button("리밸런싱 로그 CSV",
                           log_df.to_csv(index=False).encode("utf-8-sig"),
                           "rebal_v5.csv", "text/csv")

else:
    st.info("👈 사이드바에서 파라미터를 설정하고 **▶ 백테스트 실행** 을 누르세요.")
    with st.expander("📋 v5 수정 내역", expanded=True):
        st.markdown("""
**버그 수정 내역**

| 문제 | 기존 v4 | v5 수정 |
|------|---------|---------|
| 수익률 정규화 | 전체 종목 수로 나눔 → 가격 없는 종목 손실 처리 | 유효 종목만의 평균으로 정확 계산 |
| 초기 lookback | 부족해도 신호 생성 → 쓸모없는 종목 편입 | lookback 충분할 때만 신호 사용 |
| 영업일 기준 | START_STR부터만 → lookback 짧아짐 | BM_START(400일전)부터 전체 영업일 확보 |

**전략 개요**

| 항목 | 내용 |
|------|------|
| 유니버스 | KOSPI / KOSDAQ / 소형주 |
| 팩터 | 6M×0.7 + 3M×0.3 가중 모멘텀 |
| 방어 | KOSPI 200일선 하회 시 현금 |
| 속도 | Bulk 로딩 → 5~10분 |
        """)