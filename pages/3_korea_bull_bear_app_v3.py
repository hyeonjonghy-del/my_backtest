"""
한국판 Bull/Bull Mix/Bear 전략 v2
────────────────────────────────────────
추세 기준 : KOSPI200 vs MA150
금리 기준 : ^TNX (미국 10년물) vs MA120

상태 판단:
  Bear     : KOSPI200 < MA150
  Bull Mix : KOSPI200 > MA150 & TNX > TNX MA120
  Bull Full: KOSPI200 > MA150 & TNX ≤ TNX MA120

포트폴리오:
  Bull Full → KODEX 200 (069500) 100%
  Bull Mix  → KODEX 레버리지 (122630) N% + KODEX 200 (100-N)%
  Bear      → KODEX 단기채권 (153130) 100%
"""

import os
import warnings
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="한국 Bull/Bear 전략",
    page_icon="🐂",
    layout="wide",
)

st.title("🐂 한국 Bull / Bull Mix / Bear 전략")
st.caption("KODEX200 추세 + TNX 금리 필터 · KODEX ETF 3종 자동 전환")


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

TODAY = datetime.today().date()

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
    st.subheader("📅 백테스트 기간")
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("시작", datetime(2016, 1, 4))
    with c2:
        end_date = st.date_input("종료", TODAY)   # ← 오늘이 기본값

    st.divider()
    st.subheader("📊 추세 필터")
    ma_trend = st.slider("KODEX200 이평선 (일)", 60, 250, 60, 10)

    st.subheader("💹 금리 필터 (^TNX)")
    use_tnx = st.checkbox("금리 필터 사용", value=True)
    ma_tnx  = st.slider("TNX 이평선 (일)", 60, 200, 120, 10, disabled=not use_tnx)

    st.divider()
    st.subheader("📦 포트폴리오 비중")

    st.markdown("**🐂 Bull Full** (최적 상승장)")
    bf_lev_ratio = st.slider("Bull Full 레버리지 비중 (%)", 0, 70, 0, 5, key="bf_lev")
    st.caption(f"→ KODEX200 {100-bf_lev_ratio}% + 레버리지 {bf_lev_ratio}%")

    st.markdown("---")
    st.markdown("**⚠️ Bull Mix** (상승장 + 금리 주의)")
    lev_ratio = st.slider("Bull Mix 레버리지 비중 (%)", 10, 70, 50, 5, key="bm_lev")
    st.caption(f"→ KODEX200 {100-lev_ratio}% + 레버리지 {lev_ratio}%")

    st.markdown("---")
    st.markdown("**🐻 Bear** (하락장 방어)")
    st.caption("→ 현금 100% (채권 미운용)")

    st.subheader("💸 거래비용")
    fee = st.number_input("편도 수수료+슬리피지 (%)", value=0.15, step=0.05) / 100

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
    with st.expander("📋 전략 개요", expanded=True):
        st.markdown("""
| 상태 | 조건 | 포트폴리오 |
|------|------|-----------|
| 🐂 Bull Full | KODEX200 > MA60 & TNX ≤ MA120 | KODEX200 100% + 레버리지 0% |
| ⚠️ Bull Mix | KODEX200 > MA60 & TNX > MA120 | 레버리지 50% + KODEX200 50% |
| 🐻 Bear | KODEX200 < MA60 | 현금 100% |
        """)
    st.stop()

from pykrx import stock


# ── 데이터 로더 ──────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=3600)
def load_etf_price(ticker: str, start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
        return df["종가"].rename(ticker)
    except Exception as e:
        st.warning(f"ETF {ticker} 로딩 실패: {e}")
        return pd.Series(dtype=float)


@st.cache_data(show_spinner=False, ttl=3600)
def load_kospi200(start_str: str, end_str: str) -> pd.Series:
    try:
        df = stock.get_market_ohlcv_by_date(start_str, end_str, "069500")
        return df["종가"].rename("KODEX200")
    except Exception as e:
        st.warning(f"KODEX200 로딩 실패: {e}")
        return pd.Series(dtype=float)


@st.cache_data(show_spinner=False, ttl=3600)
def load_tnx(start_str: str, end_str: str) -> pd.Series:
    try:
        s = datetime.strptime(start_str, "%Y%m%d") - timedelta(days=220)
        df = yf.download(
            "^TNX",
            start=s.strftime("%Y-%m-%d"),
            end=(datetime.strptime(end_str, "%Y%m%d") + timedelta(days=2)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        return df["Close"].squeeze().rename("TNX")
    except Exception as e:
        st.warning(f"TNX 데이터 로딩 실패: {e}")
        return pd.Series(dtype=float)


def calc_metrics(nav: pd.Series) -> dict:
    ret     = nav.pct_change().dropna()
    n_yr    = len(nav) / 252
    cagr    = (nav.iloc[-1] / nav.iloc[0]) ** (1 / n_yr) - 1 if n_yr > 0 else 0
    total   = nav.iloc[-1] / nav.iloc[0] - 1
    roll    = nav.cummax()
    dd      = (nav - roll) / roll
    mdd     = dd.min()
    sharpe  = (ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0
    calmar  = abs(cagr / mdd) if mdd != 0 else 0
    win_m   = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return dict(total=total, cagr=cagr, mdd=mdd,
                sharpe=sharpe, calmar=calmar, win_m=win_m, dd=dd)


# ── 백테스트 ─────────────────────────────────────────────────
if run_btn:
    START_STR = start_date.strftime("%Y%m%d")
    END_STR   = end_date.strftime("%Y%m%d")
    EXT_START = (start_date - timedelta(days=300)).strftime("%Y%m%d")

    prog = st.progress(0, text="데이터 로딩 중...")

    prog.progress(10, text="KOSPI200 로딩 중...")
    kospi200 = load_kospi200(EXT_START, END_STR)
    if kospi200.empty:
        st.error("KOSPI200 데이터 로딩 실패")
        st.stop()

    prog.progress(25, text="KODEX 200 로딩 중...")
    etf_200  = load_etf_price("069500", EXT_START, END_STR)
    prog.progress(40, text="KODEX 레버리지 로딩 중...")
    etf_lev  = load_etf_price("122630", EXT_START, END_STR)
    prog.progress(55, text="KODEX 단기채권 로딩 중...")
    etf_bond = load_etf_price("153130", EXT_START, END_STR)

    prog.progress(70, text="TNX 금리 로딩 중...")
    tnx_raw = load_tnx(START_STR, END_STR) if use_tnx else pd.Series(dtype=float)

    prog.progress(80, text="신호 계산 중...")

    kospi200_ma = kospi200.rolling(ma_trend).mean()
    common_idx  = etf_200.loc[START_STR:END_STR].dropna().index

    if len(common_idx) < 20:
        st.error("유효한 거래일 데이터가 부족합니다.")
        st.stop()

    if use_tnx and not tnx_raw.empty:
        full_idx    = pd.date_range(tnx_raw.index[0], common_idx[-1], freq="D")
        tnx_aligned = tnx_raw.reindex(full_idx).ffill().reindex(common_idx).ffill()
        tnx_ma      = tnx_raw.rolling(ma_tnx).mean().reindex(full_idx).ffill().reindex(common_idx).ffill()
    else:
        tnx_aligned = pd.Series(np.nan, index=common_idx)
        tnx_ma      = pd.Series(np.nan, index=common_idx)

    ret_200  = etf_200.pct_change().reindex(common_idx).fillna(0)
    ret_lev  = etf_lev.pct_change().reindex(common_idx).fillna(0)
    ret_bond = etf_bond.pct_change().reindex(common_idx).fillna(0)
    k200     = kospi200.reindex(common_idx).ffill()
    k200_ma  = kospi200_ma.reindex(common_idx).ffill()

    # ── 백테스트 루프 ─────────────────────────────────────────
    nav        = 1.0
    nav_list, state_list = [], []
    trade_log  = []
    prev_state = None
    w          = lev_ratio / 100        # Bull Mix 레버리지 비중
    bf_w       = bf_lev_ratio / 100     # Bull Full 레버리지 비중

    for date in common_idx:
        k  = k200[date]
        km = k200_ma[date]
        t  = tnx_aligned[date]
        tm = tnx_ma[date]

        if np.isnan(k) or np.isnan(km):
            state = prev_state or "Bear"
        elif k < km:
            state = "Bear"
        elif use_tnx and not np.isnan(t) and not np.isnan(tm) and t > tm:
            state = "Bull_Mix"
        else:
            state = "Bull_Full"

        if prev_state is not None and state != prev_state:
            nav *= (1 - fee * 2)
            trade_log.append({
                "날짜": date.date(),
                "이전": prev_state,
                "전환": state,
                "NAV":  round(nav, 4),
            })

        r200  = ret_200[date]
        rlev  = ret_lev[date]

        if state == "Bull_Full":
            daily_ret = bf_w * rlev + (1 - bf_w) * r200
        elif state == "Bull_Mix":
            daily_ret = w * rlev + (1 - w) * r200
        else:
            daily_ret = 0.0  # Bear = 현금, 수익률 0

        nav *= (1 + daily_ret)
        nav_list.append(nav)
        state_list.append(state)
        prev_state = state

    prog.progress(100, text="완료!")
    import time; time.sleep(0.3)
    prog.empty()

    nav_s   = pd.Series(nav_list, index=common_idx, name="전략")
    state_s = pd.Series(state_list, index=common_idx, name="상태")
    bm      = etf_200.reindex(common_idx).ffill()
    bm      = (bm / bm.iloc[0]).rename("KODEX200 B&H")
    s       = calc_metrics(nav_s)
    sb      = calc_metrics(bm)

    # ── 현재 상태 ─────────────────────────────────────────────
    cur_state = state_s.iloc[-1]
    cur_date  = state_s.index[-1].date()
    emoji_map = {"Bull_Full": "🐂", "Bull_Mix": "⚠️", "Bear": "🐻"}
    label_map = {
        "Bull_Full": f"Bull Full — KODEX200 {100-bf_lev_ratio}% + 레버리지 {bf_lev_ratio}%",
        "Bull_Mix":  f"Bull Mix — 레버리지 {lev_ratio}% + KODEX200 {100-lev_ratio}%",
        "Bear":      "Bear — 현금 100%",
    }
    color_map = {"Bull_Full": "success", "Bull_Mix": "warning", "Bear": "error"}
    getattr(st, color_map[cur_state])(
        f"{emoji_map[cur_state]} **현재 상태** ({cur_date}): {label_map[cur_state]}"
    )

    # ── 성과 요약 ─────────────────────────────────────────────
    st.divider()
    st.subheader("📊 성과 요약")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    def show_m(col, lbl, val, bval=None, fmt=".1%"):
        col.metric(lbl, f"{val:{fmt}}",
                   f"BM {bval:{fmt}}" if bval is not None else None)
    show_m(c1, "총 수익률",  s["total"],  sb["total"])
    show_m(c2, "연 수익률",  s["cagr"],   sb["cagr"])
    show_m(c3, "MDD",        s["mdd"],    sb["mdd"])
    show_m(c4, "Sharpe",     s["sharpe"], sb["sharpe"], ".2f")
    show_m(c5, "Calmar",     s["calmar"], sb["calmar"], ".2f")
    show_m(c6, "월 승률",    s["win_m"],  None)

    # ── 상태별 체류 기간 ──────────────────────────────────────
    state_counts = state_s.value_counts()
    total_days   = len(state_s)
    sc1, sc2, sc3 = st.columns(3)
    for col, sname, emoji in [
        (sc1, "Bull_Full", "🐂"),
        (sc2, "Bull_Mix",  "⚠️"),
        (sc3, "Bear",      "🐻"),
    ]:
        cnt = state_counts.get(sname, 0)
        col.metric(f"{emoji} {sname.replace('_',' ')}",
                   f"{cnt}일", f"{cnt/total_days*100:.1f}%")

    st.divider()

    # ── 탭: Chart / Trade Logs / Monthly Returns ───────────────
    tab1, tab2, tab3 = st.tabs(["📈 Chart", "📋 Trade Logs", "📅 Monthly Returns"])

    # ── Tab 1: Chart ──────────────────────────────────────────
    with tab1:
        import plotly.graph_objects as go

        # 누적 수익률
        st.subheader("누적 수익률")
        fig_nav = go.Figure()
        fig_nav.add_trace(go.Scatter(
            x=nav_s.index, y=(nav_s * 100 - 100).round(1),
            name="전략", line=dict(color="#185FA5", width=2)
        ))
        fig_nav.add_trace(go.Scatter(
            x=bm.index, y=(bm * 100 - 100).round(1),
            name="KODEX200 B&H", line=dict(color="#85B7EB", width=1.5, dash="dash")
        ))
        fig_nav.update_layout(
            height=340, margin=dict(l=0, r=0, t=10, b=0),
            yaxis=dict(ticksuffix="%"),
            legend=dict(orientation="h", y=1.08),
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        fig_nav.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
        fig_nav.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
        st.plotly_chart(fig_nav, use_container_width=True)

        # 연도별 수익률 비교 (그룹 막대)
        st.subheader("연도별 수익률 비교")
        ann    = nav_s.resample("YE").last().pct_change().dropna()
        bm_ann = bm.resample("YE").last().pct_change().dropna()
        ann_df = pd.DataFrame({"전략": ann, "KODEX200": bm_ann}).dropna()
        ann_df.index = ann_df.index.year

        fig_ann = go.Figure()
        fig_ann.add_trace(go.Bar(
            x=ann_df.index.astype(str),
            y=(ann_df["전략"] * 100).round(1),
            name="전략",
            marker_color="#185FA5",
            text=(ann_df["전략"] * 100).round(1).astype(str) + "%",
            textposition="outside",
        ))
        fig_ann.add_trace(go.Bar(
            x=ann_df.index.astype(str),
            y=(ann_df["KODEX200"] * 100).round(1),
            name="KODEX200 B&H",
            marker_color="#85B7EB",
            text=(ann_df["KODEX200"] * 100).round(1).astype(str) + "%",
            textposition="outside",
        ))
        fig_ann.update_layout(
            barmode="group",
            height=360, margin=dict(l=0, r=0, t=30, b=0),
            yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor="rgba(128,128,128,0.4)"),
            legend=dict(orientation="h", y=1.08),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            bargap=0.2, bargroupgap=0.05,
        )
        fig_ann.update_xaxes(showgrid=False)
        fig_ann.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
        st.plotly_chart(fig_ann, use_container_width=True)

        # MDD 비교
        st.subheader("낙폭 (MDD) 비교")
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=s["dd"].index, y=(s["dd"] * 100).round(2),
            name="전략 DD", fill="tozeroy",
            line=dict(color="#185FA5", width=1.5),
            fillcolor="rgba(24,95,165,0.15)",
        ))
        fig_dd.add_trace(go.Scatter(
            x=sb["dd"].index, y=(sb["dd"] * 100).round(2),
            name="KODEX200 DD", fill="tozeroy",
            line=dict(color="#E24B4A", width=1.5, dash="dash"),
            fillcolor="rgba(226,75,74,0.1)",
        ))
        fig_dd.update_layout(
            height=260, margin=dict(l=0, r=0, t=10, b=0),
            yaxis=dict(ticksuffix="%"),
            legend=dict(orientation="h", y=1.12),
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        fig_dd.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
        fig_dd.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
        st.plotly_chart(fig_dd, use_container_width=True)

        if use_tnx and not tnx_aligned.dropna().empty:
            st.subheader("TNX 금리 & 이평선")
            tnx_chart = pd.DataFrame({
                "TNX": tnx_aligned,
                f"MA{ma_tnx}": tnx_ma,
            }).dropna()
            st.line_chart(tnx_chart, height=200)

    # ── Tab 2: Trade Logs ─────────────────────────────────────
    with tab2:
        st.subheader("상태 전환 이력")

        if trade_log:
            tr_df = pd.DataFrame(trade_log)
            tr_df["이전"] = tr_df["이전"].map(
                lambda x: emoji_map.get(x,"") + " " + x.replace("_"," "))
            tr_df["전환"] = tr_df["전환"].map(
                lambda x: emoji_map.get(x,"") + " " + x.replace("_"," "))
            st.dataframe(tr_df, use_container_width=True, hide_index=True)

            # 다음 리밸런싱 예정일 안내
            st.info(f"📌 총 전환 횟수: {len(tr_df)}회  |  "
                    f"현재 상태: {emoji_map[cur_state]} {cur_state.replace('_',' ')}")

            st.download_button(
                "📥 Trade Log CSV 다운로드",
                tr_df.to_csv(index=False).encode("utf-8-sig"),
                "korea_bull_bear_trades.csv",
                "text/csv",
            )
        else:
            st.info("상태 전환이 없었습니다.")

    # ── Tab 3: Monthly Returns ────────────────────────────────
    with tab3:
        st.subheader("월별 수익률")

        monthly_nav  = nav_s.resample("ME").last()
        monthly_ret  = monthly_nav.pct_change().dropna()
        monthly_bm   = bm.resample("ME").last().pct_change().dropna()

        # 연도 × 월 피벗 테이블
        mr_df = monthly_ret.to_frame("수익률")
        mr_df["연도"] = mr_df.index.year
        mr_df["월"]   = mr_df.index.month
        pivot = mr_df.pivot(index="연도", columns="월", values="수익률")
        pivot.columns = [f"{m}월" for m in pivot.columns]
        pivot["연간합계"] = (1 + monthly_ret).groupby(monthly_ret.index.year).prod() - 1
        pivot = pivot.map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")

        st.dataframe(pivot, use_container_width=True)

        # 연도별 수익률 비교 차트 (그룹 막대)
        st.subheader("연도별 수익률 비교")
        import plotly.graph_objects as go
        ann    = nav_s.resample("YE").last().pct_change().dropna()
        bm_ann = bm.resample("YE").last().pct_change().dropna()
        ann_df = pd.DataFrame({"전략": ann, "KODEX200": bm_ann}).dropna()
        ann_df.index = ann_df.index.year
        fig_m = go.Figure()
        fig_m.add_trace(go.Bar(
            x=ann_df.index.astype(str), y=(ann_df["전략"]*100).round(1),
            name="전략", marker_color="#185FA5",
            text=(ann_df["전략"]*100).round(1).astype(str)+"%", textposition="outside",
        ))
        fig_m.add_trace(go.Bar(
            x=ann_df.index.astype(str), y=(ann_df["KODEX200"]*100).round(1),
            name="KODEX200", marker_color="#85B7EB",
            text=(ann_df["KODEX200"]*100).round(1).astype(str)+"%", textposition="outside",
        ))
        fig_m.update_layout(
            barmode="group", height=320,
            margin=dict(l=0,r=0,t=30,b=0),
            yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor="rgba(128,128,128,0.4)"),
            legend=dict(orientation="h", y=1.08),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            bargap=0.2, bargroupgap=0.05,
        )
        fig_m.update_xaxes(showgrid=False)
        fig_m.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
        st.plotly_chart(fig_m, use_container_width=True)

        # 월별 수익률 vs 벤치마크
        st.subheader("월별 전략 vs BM 비교")
        cmp_df = pd.DataFrame({
            "전략":     monthly_ret,
            "KODEX200": monthly_bm,
        }).dropna()
        st.line_chart(cmp_df, height=220)

        st.download_button(
            "📥 Monthly Returns CSV 다운로드",
            cmp_df.reset_index().rename(columns={"index": "날짜"})
            .to_csv(index=False).encode("utf-8-sig"),
            "korea_bull_bear_monthly.csv",
            "text/csv",
        )
