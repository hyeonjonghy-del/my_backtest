"""
한국 공격적 모멘텀 전략 백테스터 v2
────────────────────────────────────
- KRX 로그인: Streamlit Secrets (KRX_ID / KRX_PW)
- 유니버스: KOSPI 전체 / KOSDAQ 전체 / 소형주 특화
- 팩터: 6M+3M 가중 모멘텀
- 방어: KOSPI 200일선 하회 시 전량 현금
- 리밸런싱: 월 1회

[Streamlit Cloud secrets.toml 설정]
KRX_ID = "your_krx_id"
KRX_PW = "your_krx_pw"
"""

import os
import warnings
import time
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import numpy as np
from pykrx import stock

warnings.filterwarnings("ignore")

# ── KRX 로그인 (Streamlit Secrets → 환경변수) ──────────────
def krx_login():
    try:
        if "KRX_ID" in st.secrets and "KRX_PW" in st.secrets:
            os.environ["KRX_ID"] = st.secrets["KRX_ID"]
            os.environ["KRX_PW"] = st.secrets["KRX_PW"]
            return True
    except Exception:
        pass
    return False

krx_ok = krx_login()

# ── 페이지 설정 ─────────────────────────────────────────────
st.set_page_config(
    page_title="한국 모멘텀 전략",
    page_icon="📈",
    layout="wide",
)

st.title("📈 한국 공격적 모멘텀 전략 v2")
st.caption("KRX 로그인 · 소형주 포함 전체 유니버스 · 6M+3M 모멘텀 · 200일선 현금 방어")

# KRX 로그인 상태 표시
if krx_ok:
    st.success("✅ KRX 로그인 성공 (Secrets 연결 완료)", icon="🔑")
else:
    st.warning(
        "⚠️ KRX 로그인 정보가 없습니다. "
        "Streamlit Cloud → 앱 우하단 ⚙️ → Secrets 에서 KRX_ID / KRX_PW 를 입력해 주세요.",
        icon="🔑",
    )

# ── 사이드바: 파라미터 ──────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 전략 파라미터")

    st.subheader("📅 기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2019, 1, 2))
    with c2:
        end_date = st.date_input("종료", datetime(2024, 12, 31))

    st.subheader("🌐 유니버스")
    market_sel = st.selectbox(
        "시장",
        ["KOSPI 전체", "KOSDAQ 전체", "KOSPI+KOSDAQ 전체", "KOSPI 소형주", "KOSDAQ 소형주"],
        index=1,
    )
    # 시총 필터 (소형주 전략용)
    use_cap_filter = "소형주" in market_sel
    if use_cap_filter:
        st.caption("소형주 = 시총 하위 50% 종목 자동 필터링")
    min_vol_b = st.number_input("최소 일평균 거래대금 (억)", value=3, step=1,
                                help="너무 낮으면 유동성 부족 위험")
    min_vol   = min_vol_b * 1_0000_0000

    st.subheader("📈 모멘텀")
    top_n     = st.slider("보유 종목 수", 5, 30, 20)
    mom_long  = st.slider("장기 모멘텀 (거래일)", 60, 200, 120, 5,
                          help="약 6개월 = 120거래일")
    mom_short = st.slider("단기 모멘텀 (거래일)", 20, 100, 60, 5,
                          help="약 3개월 = 60거래일")
    w_long    = st.slider("장기 가중치", 0.0, 1.0, 0.7, 0.1)
    w_short   = round(1.0 - w_long, 1)
    st.caption(f"단기 가중치: {w_short} (자동)")

    # 모멘텀 크래시 방어: 1개월 수익률 역전 제외
    skip_1m_crash = st.checkbox(
        "1M 역전 종목 제외",
        value=True,
        help="6M 모멘텀은 좋지만 최근 1개월 수익률이 마이너스인 종목 제외 (모멘텀 크래시 방어)",
    )

    st.subheader("🛡 리스크 관리")
    use_ma_filter = st.checkbox("KOSPI 이평선 현금 전환", value=True)
    ma_days = st.slider("이평선 일수", 60, 250, 200, 10, disabled=not use_ma_filter)
    use_stop = st.checkbox("종목별 손절선", value=False)
    stop_pct = st.slider("손절 기준 (%)", -40, -5, -20, 1,
                         disabled=not use_stop) / 100

    st.subheader("💸 거래비용")
    fee_rate = st.number_input(
        "편도 수수료+슬리피지 (%)", value=0.5, step=0.1,
        help="소형주는 슬리피지 크므로 0.5~1.0% 권장",
    ) / 100

    run_btn = st.button("▶ 백테스트 실행", type="primary", use_container_width=True)


# ── 유니버스 파싱 ────────────────────────────────────────────
def parse_market(sel: str):
    if "KOSPI+KOSDAQ" in sel:
        return ["KOSPI", "KOSDAQ"]
    elif "KOSPI" in sel:
        return ["KOSPI"]
    else:
        return ["KOSDAQ"]


# ── 캐시 함수들 ──────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=3600)
def load_kospi_index(start_str: str, end_str: str) -> pd.Series:
    """KOSPI 지수 종가. 실패 시 KODEX 200(069500) 대체."""
    for method in ["index", "kodex200", "tiger200"]:
        try:
            if method == "index":
                df = stock.get_index_ohlcv_by_date(start_str, end_str, "1001")
                return df["종가"].rename("KOSPI")
            elif method == "kodex200":
                df = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")
                return df["종가"].rename("KOSPI")
            else:
                df = stock.get_market_ohlcv_by_date(start_str, end_str, "102110")
                return df["종가"].rename("KOSPI")
        except Exception:
            continue
    raise RuntimeError("KOSPI 데이터 로딩 실패. KRX 로그인 및 네트워크를 확인하세요.")


@st.cache_data(show_spinner=False, ttl=3600)
def load_price(ticker: str, start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
        return df["종가"]
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(show_spinner=False, ttl=86400)
def load_universe_raw(date_str: str, markets: tuple) -> list:
    tickers = []
    for m in markets:
        try:
            tickers.extend(stock.get_market_ticker_list(date_str, market=m))
        except Exception:
            pass
    return list(set(tickers))


@st.cache_data(show_spinner=False, ttl=86400)
def load_market_cap(date_str: str, markets: tuple) -> pd.Series:
    """시총 데이터 (소형주 필터용)"""
    caps = {}
    for m in markets:
        try:
            df = stock.get_market_cap_by_ticker(date_str, market=m)
            if "시가총액" in df.columns:
                caps.update(df["시가총액"].to_dict())
        except Exception:
            pass
    return pd.Series(caps)


# ── 성과 계산 ────────────────────────────────────────────────
def calc_metrics(nav: pd.Series) -> dict:
    ret    = nav.pct_change().dropna()
    n_yr   = len(nav) / 252
    cagr   = (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_yr) - 1 if n_yr > 0 else 0
    total  = nav.iloc[-1] / nav.iloc[0] - 1
    roll   = nav.cummax()
    dd     = (nav - roll) / roll
    mdd    = dd.min()
    sharpe = (ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0
    sortino_denom = ret[ret < 0].std()
    sortino = (ret.mean() / sortino_denom * np.sqrt(252)) if sortino_denom > 0 else 0
    win_m  = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return dict(
        total=total, cagr=cagr, mdd=mdd,
        sharpe=sharpe, sortino=sortino, win_m=win_m, dd=dd
    )


# ── 메인 백테스트 ────────────────────────────────────────────
if run_btn:
    if not krx_ok:
        st.error("KRX 로그인 정보가 없으면 백테스트를 실행할 수 없습니다. Secrets 설정을 먼저 해주세요.")
        st.stop()

    START_STR = start_date.strftime("%Y%m%d")
    END_STR   = end_date.strftime("%Y%m%d")
    BM_START  = (start_date - timedelta(days=310)).strftime("%Y%m%d")
    markets   = tuple(parse_market(market_sel))

    prog = st.progress(0, text="KOSPI 지수 로딩 중...")

    # 1. KOSPI 기준 지수
    try:
        kospi    = load_kospi_index(BM_START, END_STR)
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    kospi_ma  = kospi.rolling(ma_days).mean()
    all_bdays = kospi.loc[START_STR:END_STR].index

    # 2. 월별 리밸런싱 날짜 (매월 첫 영업일)
    rebal_dates, prev_m = [], None
    for d in all_bdays:
        if d.month != prev_m:
            rebal_dates.append(d)
            prev_m = d.month

    n_rebal   = len(rebal_dates)
    nav_s     = pd.Series(index=all_bdays, dtype=float)
    nav_s.iloc[0] = 1.0
    holdings  = {}   # {ticker: entry_price}
    prev_date = all_bdays[0]
    rebal_log = []

    # 3. 루프
    for ri, rdate in enumerate(rebal_dates):
        prog.progress(
            max(1, int(ri / n_rebal * 88)),
            text=f"리밸런싱 [{ri+1}/{n_rebal}] {rdate.date()} ..."
        )

        rstr     = rdate.strftime("%Y%m%d")
        lb_str   = (rdate - timedelta(days=310)).strftime("%Y%m%d")
        prev_str = prev_date.strftime("%Y%m%d")

        # ── 시장 국면 필터 ─────────────────────────
        k_val    = kospi.get(rdate, np.nan)
        m_val    = kospi_ma.get(rdate, np.nan)
        go_cash  = (
            use_ma_filter
            and not (np.isnan(k_val) or np.isnan(m_val))
            and k_val < m_val
        )

        # ── 보유 포트폴리오 기간 수익률 계산 ────────
        period_idx = all_bdays[
            (all_bdays >= prev_date) & (all_bdays <= rdate)
        ]
        if len(period_idx) > 1 and holdings:
            base    = nav_s.get(prev_date, 1.0)
            n_hold  = len(holdings)
            ep_snap = dict(holdings)
            for d in period_idx[1:]:
                dstr      = d.strftime("%Y%m%d")
                port_ret  = 0.0
                for tk, ep in ep_snap.items():
                    cp_s = load_price(tk, prev_str, dstr)
                    cp   = cp_s.get(d, np.nan) if len(cp_s) else np.nan
                    if not np.isnan(cp) and ep > 0:
                        r = cp / ep - 1
                        if use_stop and r <= stop_pct:
                            r = stop_pct
                        port_ret += r / n_hold
                nav_s[d] = base * (1 + port_ret)

        cur_nav = nav_s.get(rdate, nav_s.get(prev_date, 1.0))

        # ── 모멘텀 스코어 계산 ───────────────────────
        new_hold    = {}
        mom_scores  = {}
        top_tks     = []

        if not go_cash:
            # 유니버스 로드
            uni = load_universe_raw(rstr, markets)

            # 소형주 필터 (시총 하위 50%)
            if use_cap_filter and uni:
                cap_s = load_market_cap(rstr, markets)
                if len(cap_s) > 0:
                    threshold = cap_s.median()
                    uni = [t for t in uni if cap_s.get(t, threshold + 1) <= threshold]

            # 모멘텀 스코어
            for tk in uni:
                try:
                    pr = load_price(tk, lb_str, rstr)
                    if len(pr) < mom_long + 3:
                        continue

                    r_l = pr.iloc[-1] / pr.iloc[-mom_long]  - 1
                    r_s = pr.iloc[-1] / pr.iloc[-mom_short] - 1
                    r_1m = pr.iloc[-1] / pr.iloc[-21] - 1  # 1개월

                    # 모멘텀 크래시 방어: 최근 1M 마이너스면 제외
                    if skip_1m_crash and r_1m < 0:
                        continue

                    if r_l > -0.95:
                        score = w_long * r_l + w_short * r_s
                        mom_scores[tk] = score
                except Exception:
                    pass

            top_tks = sorted(mom_scores, key=mom_scores.get, reverse=True)[:top_n]

            # 거래비용 (turnover 기반)
            prev_set = set(holdings.keys())
            new_set  = set(top_tks)
            changed  = len(prev_set.symmetric_difference(new_set))
            total_pos = max(len(prev_set | new_set), 1)
            turnover = changed / total_pos
            cost     = turnover * fee_rate * 2
            nav_s[rdate] = cur_nav * (1 - cost)

            # 편입가 기록
            for tk in top_tks:
                pr = load_price(tk, rstr, rstr)
                if len(pr):
                    new_hold[tk] = pr.iloc[-1]

        avg_mom = (
            np.mean([mom_scores[t] for t in top_tks]) if top_tks else 0.0
        )
        rebal_log.append({
            "날짜":       rdate.date(),
            "모드":       "현금" if go_cash else "투자",
            "종목수":     0 if go_cash else len(new_hold),
            "KOSPI/MA":   f"{k_val:,.0f} / {m_val:,.0f}" if not (np.isnan(k_val) or np.isnan(m_val)) else "-",
            "평균모멘텀": f"{avg_mom:.1%}" if not go_cash else "-",
        })

        holdings  = new_hold
        prev_date = rdate

    nav_s = nav_s.ffill().dropna()
    prog.progress(100, text="완료!")
    time.sleep(0.3)
    prog.empty()

    # ── 벤치마크 정렬 ───────────────────────────────
    bm   = kospi.reindex(nav_s.index).ffill()
    bm   = bm / bm.iloc[0]

    s  = calc_metrics(nav_s)
    sb = calc_metrics(bm)

    # ────────────────────────────────────────────────
    #  대시보드
    # ────────────────────────────────────────────────
    st.divider()
    st.subheader("📊 성과 요약")

    def show_metric(col, label, val, bval=None, fmt=".1%", reverse=False):
        fval  = f"{val:{fmt}}"
        delta = None
        if bval is not None:
            diff  = val - bval
            sign  = "+" if diff >= 0 else ""
            delta = f"BM대비 {sign}{diff:{fmt}}"
        col.metric(label, fval, delta)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    show_metric(c1, "총 수익률",    s["total"],   sb["total"])
    show_metric(c2, "연 수익률",    s["cagr"],    sb["cagr"])
    show_metric(c3, "MDD",          s["mdd"],     sb["mdd"])
    show_metric(c4, "Sharpe",       s["sharpe"],  sb["sharpe"],  ".2f")
    show_metric(c5, "Sortino",      s["sortino"], sb["sortino"], ".2f")
    show_metric(c6, "월 승률",      s["win_m"],   None)

    # NAV 차트
    st.subheader("📈 누적 수익률")
    nav_df = pd.DataFrame({"전략": nav_s, "KOSPI(BM)": bm})
    st.line_chart(nav_df, height=320)

    # 낙폭 차트
    st.subheader("📉 낙폭 (Drawdown)")
    dd_df = pd.DataFrame({"전략 DD": s["dd"], "KOSPI DD": sb["dd"]})
    st.area_chart(dd_df, height=200)

    # 연도별 수익률
    st.subheader("📅 연도별 수익률")
    ann   = nav_s.resample("YE").last().pct_change().dropna()
    bm_an = bm.resample("YE").last().pct_change().dropna()
    ann_df = pd.DataFrame({"전략": ann, "KOSPI": bm_an}).dropna()
    ann_df.index = ann_df.index.year
    st.bar_chart(ann_df, height=240)

    # 현금 전환 구간 표시
    cash_periods = [r["날짜"] for r in rebal_log if r["모드"] == "현금"]
    if cash_periods:
        st.info(f"🛡 현금 전환 발생: {len(cash_periods)}회 "
                f"({cash_periods[0]} ~ {cash_periods[-1]})")

    # 리밸런싱 로그
    with st.expander("🗒 리밸런싱 이력"):
        log_df = pd.DataFrame(rebal_log)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

    # 다운로드
    st.divider()
    dc1, dc2 = st.columns(2)
    with dc1:
        csv1 = nav_s.reset_index()
        csv1.columns = ["날짜", "NAV"]
        st.download_button(
            "NAV CSV 다운로드",
            csv1.to_csv(index=False).encode("utf-8-sig"),
            "nav_korea_momentum_v2.csv", "text/csv",
        )
    with dc2:
        st.download_button(
            "리밸런싱 로그 CSV",
            pd.DataFrame(rebal_log).to_csv(index=False).encode("utf-8-sig"),
            "rebal_log_v2.csv", "text/csv",
        )

else:
    # ── 초기 안내 ────────────────────────────────────
    st.info("👈 왼쪽 사이드바에서 파라미터를 설정하고 **▶ 백테스트 실행** 버튼을 누르세요.")

    with st.expander("📋 전략 개요", expanded=True):
        st.markdown("""
| 항목 | 내용 |
|------|------|
| 유니버스 | KOSPI / KOSDAQ / 소형주 선택 가능 |
| 팩터 | 6M × 0.7 + 3M × 0.3 가중 모멘텀 |
| 방어 | KOSPI 200일선 하회 → 전량 현금 |
| 소형주 필터 | 시총 하위 50% 자동 선별 |
| 모멘텀 크래시 방어 | 최근 1개월 수익률 음수 종목 제외 |
| 거래비용 | Turnover 기반 편도 수수료+슬리피지 반영 |

**KRX 로그인 설정 방법**
1. Streamlit Cloud 앱 우하단 **⚙️ Manage app** 클릭
2. **Secrets** 탭 선택
3. 아래 내용 입력 후 저장:
```toml
KRX_ID = "본인 KRX ID"
KRX_PW = "본인 KRX PW"
```
        """)