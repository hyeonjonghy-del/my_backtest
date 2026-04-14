import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import io
import warnings
import calendar
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from datetime import datetime

warnings.filterwarnings('ignore')
plt.style.use('ggplot')
plt.rcParams['axes.unicode_minus'] = False

st.set_page_config(page_title="Safe/Risky/Cash Mix Strategy v4", page_icon="🛡️", layout="wide")

st.title("🛡️ Safe/Risky/Cash Mix Strategy v4")
st.markdown("""
**v4 기본값 — Walk-Forward 10년 검증 최적화 반영 (2015~2026)**

| 파라미터 | 기본값 | 근거 |
|---------|--------|------|
| Bull 진입 MA | **150일** | 10년 전체 Top 100 독점 |
| Bear 퇴출 MA | **90일** (×0.60) | 10년 최적화 1위 (기존 112→90 변경) |
| 금리 MA | **120일** | 10년 최적화 1위 (기존 90→120 변경) |
| Bull_Mix 비중 | **40%** | 10년 최적화 1위 (기존 60→40% 변경) |
| 예상 CAGR | **27.28%** | Sharpe 0.834 / MDD -42.05% |

**v4 추가 기능:** 📡 오늘 신호만 확인 | 🚨 신호 변경 감지 | 📧 이메일 알림 | 💬 카카오톡 알림
""")
st.markdown("---")

# ── 사이드바 ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("1. 투자 종목 설정")
    ticker_safe  = st.text_input("안전 자산 (Bear)",  value="SPY")
    ticker_risky = st.text_input("공격 자산 (Bull)",  value="UPRO")
    ticker_cash  = st.text_input("현금 파킹 (Cash)",  value="SGOV")

    st.header("2. 전략 기본 옵션")
    start_date      = st.date_input("시작일", pd.to_datetime("2020-01-01"))
    initial_capital = st.number_input("초기 자본", value=100_000_000, step=1_000_000)
    fee_rate        = st.number_input("매매 수수료 (%)", value=0.02, step=0.01) / 100.0
    apply_tax       = st.checkbox("양도소득세 22% 차감", value=False)
    ma_window         = st.number_input("Bull 진입 이평선 (일)", value=150, min_value=5,
                                        help="최적화 결과: 150일")
    use_asymmetric_ma = st.checkbox("비대칭 MA 사용 (빠른 퇴출)", value=True)
    if use_asymmetric_ma:
        ma_exit_window = st.number_input("Bear 퇴출 이평선 (일)", value=90, min_value=5,
                                          help="Walk-Forward 10년 최적화: 진입 MA × 0.60 (150×0.60=90일)")
        st.caption(f"ℹ️ Entry MA{int(ma_window)} / Exit MA{int(ma_exit_window)}")
    else:
        ma_exit_window = ma_window

    st.header("3. 금리 리스크 필터")
    use_rate_filter = st.checkbox("금리 필터 사용", value=True)
    ticker_rate     = st.text_input("금리 지표", value="^TNX")
    rate_ma_window  = st.number_input("금리 이평선 (일)", value=120,
                                       help="Walk-Forward 10년 최적화: 120일")

    st.header("4. Whipsaw 필터")
    use_whipsaw  = st.checkbox("Whipsaw 필터 사용", value=False)
    confirm_days = st.number_input("신호 확정 기간 (일)", value=1, min_value=1, max_value=10)

    st.header("5. 변동성 기반 포지션 사이징")
    use_vol_sizing       = st.checkbox("변동성 기반 비중 조절", value=True)
    vol_window           = st.number_input("변동성 계산 기간 (일)", value=20, min_value=5)
    target_vol           = st.number_input("목표 변동성 (%)", value=25.0, step=1.0) / 100.0
    max_risky_w          = st.slider("공격자산 최대 비중", 0.3, 1.0, 1.0, 0.1)
    vol_rebal_threshold  = st.number_input("리밸런싱 최소 변화 (%)", value=5.0, step=1.0) / 100.0
    apply_vol_on_bull_full = st.checkbox("Bull_Full에도 변동성 사이징 적용", value=False)

    st.header("6. 보조 시장 필터")
    use_aux_signal = st.checkbox("보조 시장 신호 (QQQ)", value=False)
    ticker_aux     = st.text_input("보조 시장 티커", value="QQQ")
    aux_ma_window  = st.number_input("보조 이평선 (일)", value=120)

    st.header("7. Bull_Mix 비중 설정")
    exposure_ratio = st.slider("리스크 시 공격비중", 0.0, 1.0, 0.4, 0.1,
                                help="Walk-Forward 10년 최적화: 40% (더 보수적, MDD 개선)")

    # ── 알림 설정 ─────────────────────────────────────────────────────────────
    st.header("8. 🔔 알림 설정")

    st.subheader("📧 이메일 알림 (Gmail)")
    use_email     = st.checkbox("이메일 알림 사용", value=False)
    if use_email:
        email_sender   = st.text_input("발신 Gmail 주소", placeholder="yourmail@gmail.com")
        email_password = st.text_input("Gmail 앱 비밀번호", type="password",
                                        help="Gmail → 보안 → 2단계인증 → 앱 비밀번호 생성")
        email_receiver = st.text_input("수신 이메일 주소", placeholder="receive@email.com")
        st.caption("ℹ️ Gmail 앱 비밀번호: myaccount.google.com → 보안 → 앱 비밀번호")

    st.subheader("💬 카카오톡 알림")
    use_kakao   = st.checkbox("카카오톡 알림 사용", value=False)
    if use_kakao:
        kakao_token = st.text_input("카카오 액세스 토큰", type="password",
                                     help="https://developers.kakao.com → 나에게 보내기 토큰")
        st.caption("ℹ️ 카카오 토큰: developers.kakao.com → REST API → 나에게 보내기")

# ══════════════════════════════════════════════════════════════════════════════
# 알림 함수
# ══════════════════════════════════════════════════════════════════════════════
def send_email(sender, password, receiver, subject, body):
    """Gmail SMTP로 이메일 발송"""
    try:
        msg = MIMEMultipart()
        msg['From']    = sender
        msg['To']      = receiver
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html', 'utf-8'))

        # 포트 587 (STARTTLS) 먼저 시도 — Streamlit Cloud 호환
        try:
            with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(sender, password)
                server.sendmail(sender, receiver, msg.as_string())
            return True, "✅ 이메일 발송 성공 (포트 587)"
        except Exception as e1:
            # 포트 465 (SSL) 재시도
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15) as server:
                server.login(sender, password)
                server.sendmail(sender, receiver, msg.as_string())
            return True, "✅ 이메일 발송 성공 (포트 465)"
    except Exception as e:
        return False, f"❌ 이메일 오류: {str(e)}"

def send_kakao(token, message):
    """카카오톡 나에게 보내기"""
    try:
        url  = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        data = {
            "template_object": str({
                "object_type": "text",
                "text": message,
                "link": {"web_url": "", "mobile_web_url": ""}
            })
        }
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post(url, headers=headers, data=data, timeout=10)
        if resp.status_code == 200:
            return True, "카카오톡 발송 성공"
        else:
            return False, f"카카오톡 오류: {resp.status_code} {resp.text}"
    except Exception as e:
        return False, f"카카오톡 오류: {str(e)}"

def build_alert_message(current_state, last_date, last_safe_p, last_safe_m,
                         last_rate_p, last_rate_m, last_vol,
                         risky_now, cash_now, safe_now,
                         ticker_safe, ticker_risky, ticker_cash, fmt="email"):
    """알림 메시지 생성"""
    state_kr = {"Bear": "🛑 하락장 방어", "Bull_Full": "🚀 강한 상승장", "Bull_Mix": "⚠️ 리스크 관리"}
    action_map = {
        "Bear":      f"{ticker_safe} {safe_now*100:.0f}% 보유",
        "Bull_Full": f"{ticker_risky} {risky_now*100:.0f}% 보유",
        "Bull_Mix":  f"{ticker_risky} {risky_now*100:.0f}% / {ticker_cash} {cash_now*100:.0f}%",
    }
    if fmt == "email":
        return f"""
        <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #00703C;">🛡️ Safe/Risky Mix 전략 — 오늘의 신호</h2>
        <p style="color: #555;">기준일: <b>{last_date}</b></p>
        <hr>
        <table style="width:100%; border-collapse:collapse;">
          <tr style="background:#00703C; color:white;">
            <th style="padding:10px;">항목</th><th style="padding:10px;">값</th>
          </tr>
          <tr><td style="padding:8px; border:1px solid #ddd;">시장 상태</td>
              <td style="padding:8px; border:1px solid #ddd;"><b>{state_kr.get(current_state, current_state)}</b></td></tr>
          <tr><td style="padding:8px; border:1px solid #ddd;">📌 추천 행동</td>
              <td style="padding:8px; border:1px solid #ddd; color:#C0392B;"><b>{action_map.get(current_state, '')}</b></td></tr>
          <tr><td style="padding:8px; border:1px solid #ddd;">{ticker_safe} 현재가</td>
              <td style="padding:8px; border:1px solid #ddd;">{last_safe_p:,.2f}</td></tr>
          <tr><td style="padding:8px; border:1px solid #ddd;">{ticker_safe} vs MA{int(ma_window)}</td>
              <td style="padding:8px; border:1px solid #ddd;">{last_safe_p - last_safe_m:+.2f}</td></tr>
          <tr><td style="padding:8px; border:1px solid #ddd;">금리 ({ticker_rate})</td>
              <td style="padding:8px; border:1px solid #ddd;">{last_rate_p:.3f}% (MA: {last_rate_m:.3f}%)</td></tr>
          <tr><td style="padding:8px; border:1px solid #ddd;">시장 변동성</td>
              <td style="padding:8px; border:1px solid #ddd;">{last_vol*100:.1f}% (연환산)</td></tr>
        </table>
        <p style="color:#888; font-size:12px; margin-top:20px;">
          ※ 이 메일은 Safe/Risky Mix Strategy Streamlit 앱에서 자동 발송되었습니다.
        </p>
        </body></html>
        """
    else:  # 카카오톡 (단문)
        return (
            f"🛡️ Safe/Risky Mix 전략 신호\n"
            f"📅 {last_date}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"시장: {state_kr.get(current_state, current_state)}\n"
            f"📌 {action_map.get(current_state, '')}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"SPY: {last_safe_p:,.2f} (vs MA: {last_safe_p - last_safe_m:+.2f})\n"
            f"금리: {last_rate_p:.3f}% (MA: {last_rate_m:.3f}%)\n"
            f"변동성: {last_vol*100:.1f}%"
        )

# ══════════════════════════════════════════════════════════════════════════════
# 데이터 로드
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600 * 24)
def load_data(safe, risky, rate, cash, aux):
    tickers = list(dict.fromkeys([safe, risky, rate, cash, aux]))
    raw = yf.download(tickers, start="2000-01-01", progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        df = raw['Close'].copy()
    else:
        df = raw.copy()
    return df.loc[~df.index.duplicated(keep='first')].sort_index()

# ══════════════════════════════════════════════════════════════════════════════
# 유틸 함수
# ══════════════════════════════════════════════════════════════════════════════
def sharpe_ratio(daily_returns, rf=0.05):
    excess = daily_returns - rf / 252
    return float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0.0

def calmar_ratio(cagr, mdd):
    return abs(cagr / mdd) if mdd != 0 else 0.0

def win_rate_and_avg_hold(res_df):
    switches = res_df[res_df['Action'] == 'SWITCH'].index.tolist()
    if len(switches) < 2: return 0.0, 0
    wins, hold_days = 0, []
    for i in range(len(switches) - 1):
        s, e = switches[i], switches[i + 1]
        seg = res_df.loc[s:e, 'Equity']
        if len(seg) >= 2 and seg.iloc[0] > 0 and seg.iloc[-1] > seg.iloc[0]:
            wins += 1
        hold_days.append((e - s).days)
    total = len(switches) - 1
    return (wins / total * 100) if total > 0 else 0.0, int(np.mean(hold_days)) if hold_days else 0

# ══════════════════════════════════════════════════════════════════════════════
# ── 오늘 신호만 빠르게 확인하는 버튼 ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("### 📢 오늘의 투자 신호 (빠른 확인)")
col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    signal_only = st.button("📡 오늘 신호만 확인", use_container_width=True)

if signal_only:
    with st.spinner("신호 확인 중..."):
        quick_df = load_data(ticker_safe, ticker_risky, ticker_rate, ticker_cash, ticker_aux)
        quick_df = quick_df.ffill()

        s_safe  = quick_df[ticker_safe]
        ma_e    = s_safe.rolling(int(ma_window)).mean()
        ma_x    = s_safe.rolling(int(ma_exit_window)).mean()
        s_rate  = quick_df[ticker_rate]
        ma_r    = s_rate.rolling(int(rate_ma_window)).mean()
        spy_vol = s_safe.pct_change().rolling(20).std() * np.sqrt(252)

        # 비대칭 MA 상태
        entry_sig = s_safe > ma_e
        exit_sig  = s_safe < ma_x
        st_arr = np.zeros(len(s_safe), dtype=int)
        for i in range(1, len(s_safe)):
            st_arr[i] = (1 if entry_sig.iloc[i] else 0) if st_arr[i-1] == 0 \
                        else (0 if exit_sig.iloc[i] else 1)
        is_bull_sig = pd.Series(st_arr == 1, index=quick_df.index)
        is_hike_sig = s_rate > ma_r

        last_date   = quick_df.index[-1]
        last_safe_p = float(s_safe.iloc[-1])
        last_safe_m = float(ma_e.iloc[-1])
        last_rate_p = float(s_rate.iloc[-1])
        last_rate_m = float(ma_r.iloc[-1])
        last_vol    = float(spy_vol.iloc[-1]) if not np.isnan(spy_vol.iloc[-1]) else 0.15
        is_bull_now = bool(is_bull_sig.iloc[-1])
        is_hike_now = bool(is_hike_sig.iloc[-1])

        # 현재 상태
        if is_bull_now:
            cur_state = "Bull_Mix" if (use_rate_filter and is_hike_now) else "Bull_Full"
        else:
            cur_state = "Bear"

        # 전일 상태 (신호 변경 감지)
        prev_state = None
        if len(is_bull_sig) >= 2:
            prev_bull = bool(is_bull_sig.iloc[-2])
            prev_hike = bool(is_hike_sig.iloc[-2])
            if prev_bull:
                prev_state = "Bull_Mix" if (use_rate_filter and prev_hike) else "Bull_Full"
            else:
                prev_state = "Bear"

        signal_changed = (cur_state != prev_state) if prev_state else False

        # 변동성 기반 비중 계산
        def quick_weights(state, vol):
            def vw(base):
                if use_vol_sizing and not np.isnan(vol) and vol > 0:
                    return min(target_vol / (vol * 3.0), base)
                return base
            if state == "Bear":   return {ticker_safe: 1.0}
            elif state == "Bull_Full":
                rw = vw(max_risky_w) if apply_vol_on_bull_full else max_risky_w
                w  = {ticker_risky: rw}
                if 1.0 - rw > 1e-6: w[ticker_cash] = 1.0 - rw
                return w
            elif state == "Bull_Mix":
                rw = vw(max_risky_w * exposure_ratio)
                w  = {ticker_risky: rw}
                if 1.0 - rw > 1e-6: w[ticker_cash] = 1.0 - rw
                return w
            return {ticker_safe: 1.0}

        today_w   = quick_weights(cur_state, last_vol)
        risky_now = today_w.get(ticker_risky, 0)
        cash_now  = today_w.get(ticker_cash,  0)
        safe_now  = today_w.get(ticker_safe,  0)

        # ── 신호 변경 배너 ────────────────────────────────────────────────────
        if signal_changed:
            st.error(f"🚨 **신호 변경 감지!** {prev_state} → **{cur_state}**  ← 매매 필요!")
        else:
            st.info(f"✅ 신호 유지 중: **{cur_state}** (전일과 동일 — 매매 불필요)")

        st.caption(f"기준: {last_date.strftime('%Y-%m-%d')} 종가")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{ticker_safe} 현재가", f"{last_safe_p:,.2f}",
                  f"{last_safe_p - last_safe_m:+.2f} (vs MA{int(ma_window)})")
        c2.metric("금리 vs MA", f"{last_rate_p:.3f}%",
                  f"{last_rate_p - last_rate_m:+.3f}% {'🔥 주의' if is_hike_now else '🍀 안정'}")
        c3.metric("시장 변동성", f"{last_vol*100:.1f}%", "연환산")
        c4.metric(f"{ticker_risky} 권고 비중", f"{risky_now*100:.0f}%")

        if cur_state == "Bear":
            st.error(f"🛑 **[하락장 방어]** → {ticker_safe} {safe_now*100:.0f}% 보유")
        elif cur_state == "Bull_Full":
            st.success(f"🚀 **[강한 상승장]** → {ticker_risky} {risky_now*100:.0f}% / {ticker_cash} {cash_now*100:.0f}%")
        elif cur_state == "Bull_Mix":
            st.warning(f"⚠️ **[리스크 관리]** → {ticker_risky} {risky_now*100:.0f}% / {ticker_cash} {cash_now*100:.0f}%")

        # ── 알림 발송 ─────────────────────────────────────────────────────────
        if use_email or use_kakao:
            st.markdown("---")
            st.markdown("#### 🔔 알림 발송")

            subj = f"[투자신호] {cur_state} — {last_date.strftime('%Y-%m-%d')}"
            if signal_changed:
                subj = f"🚨 [신호변경] {prev_state}→{cur_state} — {last_date.strftime('%Y-%m-%d')}"

            col_n1, col_n2 = st.columns(2)

            with col_n1:
                if use_email and email_sender and email_password and email_receiver:
                    if st.button("📧 이메일 발송", use_container_width=True):
                        body = build_alert_message(
                            cur_state, last_date.strftime('%Y-%m-%d'),
                            last_safe_p, last_safe_m, last_rate_p, last_rate_m, last_vol,
                            risky_now, cash_now, safe_now,
                            ticker_safe, ticker_risky, ticker_cash, fmt="email"
                        )
                        ok, msg = send_email(email_sender, email_password, email_receiver, subj, body)
                        st.success(msg) if ok else st.error(msg)

            with col_n2:
                if use_kakao and kakao_token:
                    if st.button("💬 카카오톡 발송", use_container_width=True):
                        text = build_alert_message(
                            cur_state, last_date.strftime('%Y-%m-%d'),
                            last_safe_p, last_safe_m, last_rate_p, last_rate_m, last_vol,
                            risky_now, cash_now, safe_now,
                            ticker_safe, ticker_risky, ticker_cash, fmt="kakao"
                        )
                        ok, msg = send_kakao(kakao_token, text)
                        st.success(msg) if ok else st.error(msg)

            # 신호 변경 시 자동 알림
            if signal_changed:
                if use_email and email_sender and email_password and email_receiver:
                    body = build_alert_message(
                        cur_state, last_date.strftime('%Y-%m-%d'),
                        last_safe_p, last_safe_m, last_rate_p, last_rate_m, last_vol,
                        risky_now, cash_now, safe_now,
                        ticker_safe, ticker_risky, ticker_cash, fmt="email"
                    )
                    ok, msg = send_email(email_sender, email_password, email_receiver, subj, body)
                    st.info(f"📧 신호 변경 → 이메일 자동 발송: {msg}")
                if use_kakao and kakao_token:
                    text = build_alert_message(
                        cur_state, last_date.strftime('%Y-%m-%d'),
                        last_safe_p, last_safe_m, last_rate_p, last_rate_m, last_vol,
                        risky_now, cash_now, safe_now,
                        ticker_safe, ticker_risky, ticker_cash, fmt="kakao"
                    )
                    ok, msg = send_kakao(kakao_token, text)
                    st.info(f"💬 신호 변경 → 카카오톡 자동 발송: {msg}")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ── 백테스트 전체 실행 ────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
if st.button("🚀 Run Full Backtest", type="primary", use_container_width=True):
    with st.spinner("백테스트 실행 중..."):

        full_df = load_data(ticker_safe, ticker_risky, ticker_rate, ticker_cash, ticker_aux)
        missing = [c for c in [ticker_safe, ticker_risky, ticker_rate] if c not in full_df.columns]
        if missing:
            st.error(f"데이터 없는 티커: {missing}")
            st.stop()

        cash_available = ticker_cash in full_df.columns
        aux_available  = ticker_aux  in full_df.columns and use_aux_signal
        df_raw         = full_df.ffill()

        series_safe  = df_raw[ticker_safe]
        ma_safe      = series_safe.rolling(int(ma_window)).mean()
        ma_safe_exit = series_safe.rolling(int(ma_exit_window)).mean()
        series_rate  = df_raw[ticker_rate]
        ma_rate      = series_rate.rolling(int(rate_ma_window)).mean()
        series_aux   = df_raw[ticker_aux] if aux_available else series_safe
        ma_aux       = series_aux.rolling(int(aux_ma_window)).mean() if aux_available else ma_safe
        spy_vol      = series_safe.pct_change().rolling(int(vol_window)).std() * np.sqrt(252)
        returns_df   = df_raw.pct_change()

        # 비대칭 MA
        entry_bull = series_safe > ma_safe
        exit_bear  = series_safe < ma_safe_exit
        st_arr = np.zeros(len(series_safe), dtype=int)
        for i in range(1, len(series_safe)):
            st_arr[i] = (1 if entry_bull.iloc[i] else 0) if st_arr[i-1] == 0 \
                        else (0 if exit_bear.iloc[i] else 1)
        raw_bull = pd.Series(st_arr == 1, index=df_raw.index)
        raw_hike = series_rate > ma_rate
        raw_aux  = (series_aux > ma_aux) if aux_available else pd.Series(True, index=df_raw.index)

        if use_whipsaw:
            cd = int(confirm_days)
            bull_c = raw_bull.rolling(cd).min().fillna(0).astype(bool)
            bear_c = (~raw_bull).rolling(cd).min().fillna(0).astype(bool)
            sig = pd.Series(np.nan, index=df_raw.index, dtype='float64')
            sig = sig.where(~bull_c, other=1.0).where(~bear_c, other=0.0)
            is_bull = sig.ffill().fillna(0.0).astype(bool)
        else:
            is_bull = raw_bull

        conditions = [
            ~is_bull,
            is_bull & raw_aux & (~raw_hike | ~use_rate_filter),
            is_bull & (~raw_aux | (raw_aux & raw_hike & use_rate_filter)),
        ]
        raw_state   = pd.Series(np.select(conditions, ["Bear","Bull_Full","Bull_Mix"], "Bear"), index=df_raw.index)
        trade_state = raw_state.shift(1)

        sim_start   = max(pd.to_datetime(start_date), df_raw.index[0])
        df_sim      = df_raw.loc[sim_start:].copy()
        trade_state = trade_state.loc[sim_start:].fillna("Bear")
        returns_sim = returns_df.loc[sim_start:]
        spy_vol_sim = spy_vol.loc[sim_start:]

        def state_to_weights(state, vol):
            def vw(base):
                if use_vol_sizing and not np.isnan(vol) and vol > 0:
                    return min(target_vol / (vol * 3.0), base)
                return base
            if state == "Bear":   return {ticker_safe: 1.0}
            elif state == "Bull_Full":
                rw = vw(max_risky_w) if (use_vol_sizing and apply_vol_on_bull_full) else max_risky_w
                w  = {ticker_risky: rw}
                if 1.0 - rw > 1e-6 and cash_available: w[ticker_cash] = 1.0 - rw
                return w
            elif state == "Bull_Mix":
                rw = vw(max_risky_w * exposure_ratio)
                w  = {ticker_risky: rw}
                if 1.0 - rw > 1e-6 and cash_available: w[ticker_cash] = 1.0 - rw
                return w
            return {ticker_safe: 1.0}

        equity = float(initial_capital)
        peak   = equity
        history = []
        first_vol = spy_vol_sim.iloc[0] if not spy_vol_sim.empty else np.nan
        curr_w  = {k: v for k, v in state_to_weights(trade_state.iloc[0], first_vol).items() if v > 0}
        equity -= equity * fee_rate

        for i in range(len(df_sim)):
            today    = df_sim.index[i]
            state    = trade_state.iloc[i]
            vol_now  = spy_vol_sim.iloc[i] if i < len(spy_vol_sim) else np.nan
            target_w = {k: v for k, v in state_to_weights(state, vol_now).items() if v > 0}

            day_ret = 0.0
            if i > 0:
                for tk, w in curr_w.items():
                    if tk in returns_sim.columns:
                        r = returns_sim.loc[today, tk]
                        day_ret += w * (0.0 if pd.isna(r) else float(r))
            equity *= (1.0 + day_ret)

            s_chg = (curr_w.keys() != target_w.keys())
            w_chg = any(abs(curr_w.get(k,0) - target_w.get(k,0)) >= vol_rebal_threshold
                        for k in set(list(curr_w.keys()) + list(target_w.keys())))
            action = ""
            if s_chg or w_chg:
                action = "SWITCH"
                equity -= equity * fee_rate
                curr_w  = target_w

            if equity > peak: peak = equity
            dd = (equity - peak) / peak if peak > 0 else 0.0

            history.append({
                "Date":            today,
                "State":           state,
                "Position":        {"Bear": f"Bear({ticker_safe})", "Bull_Full": f"Bull Full({ticker_risky})",
                                    "Bull_Mix": f"Bull Mix({ticker_risky}+{ticker_cash})"}.get(state, state),
                "Risky_Weight(%)": round(curr_w.get(ticker_risky, 0) * 100, 1),
                "Action":          action,
                "Equity":          round(equity),
                "Daily_Return(%)": round(day_ret * 100, 4),
                "Drawdown(%)":     round(dd * 100, 4),
                "SPY_Vol(ann,%)":  round(vol_now * 100, 2) if not np.isnan(vol_now) else np.nan,
                "Safe_Price":      df_sim[ticker_safe].iloc[i],
                "Safe_MA":         ma_safe.loc[today] if today in ma_safe.index else np.nan,
            })

        res_df = pd.DataFrame(history).set_index("Date")
        res_df['Benchmark'] = (1 + returns_sim[ticker_safe].fillna(0)).cumprod() * initial_capital

        # 성과 계산
        final_pre = float(res_df['Equity'].iloc[-1])
        profit    = final_pre - initial_capital
        tax_amt   = max(profit, 0) * 0.22 if (apply_tax and profit > 0) else 0.0
        final_bal = final_pre - tax_amt
        final_bm  = float(res_df['Benchmark'].iloc[-1])
        days      = (res_df.index[-1] - res_df.index[0]).days
        cagr   = (final_bal / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
        cagr_b = (final_bm  / initial_capital) ** (365.0 / days) - 1 if days > 0 else 0.0
        mdd    = res_df['Drawdown(%)'].min() / 100.0
        sharpe = sharpe_ratio(res_df['Daily_Return(%)'] / 100.0)
        calmar = calmar_ratio(cagr, mdd)
        wr, avg_hold = win_rate_and_avg_hold(res_df)
        n_trades = len(res_df[res_df['Action'] == 'SWITCH'])

        # 성과 요약
        st.markdown("### 📊 성과 요약")
        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        lbl = "Final Balance (After Tax)" if apply_tax else "Final Balance"
        r1c1.metric(lbl,             f"{final_bal:,.0f}", delta=f"세금: -{tax_amt:,.0f}" if tax_amt > 0 else None)
        r1c2.metric("CAGR",          f"{cagr*100:.2f}%",  delta=f"{(cagr-cagr_b)*100:.2f}%p vs BM")
        r1c3.metric("MDD",           f"{mdd*100:.2f}%")
        r1c4.metric("벤치마크 CAGR", f"{cagr_b*100:.2f}%")

        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        r2c1.metric("Sharpe Ratio",     f"{sharpe:.2f}")
        r2c2.metric("Calmar Ratio",     f"{calmar:.2f}")
        r2c3.metric("매매 승률",         f"{wr:.1f}%")
        r2c4.metric("총 매매 / 평균보유", f"{n_trades}회 / {avg_hold}일")
        st.divider()

        # 월별 수익률 피벗
        m_equity = res_df[['Equity']].resample('ME').last()
        def calc_annual(df_eq):
            annual = {}
            for yr in df_eq.index.year.unique():
                yd  = df_eq[df_eq.index.year == yr]['Equity']
                bef = df_eq[df_eq.index.year < yr]['Equity']
                sv  = float(bef.iloc[-1]) if len(bef) > 0 else float(initial_capital)
                annual[yr] = float(yd.iloc[-1]) / sv - 1.0
            return pd.Series(annual)

        annual_ret  = calc_annual(res_df[['Equity']])
        m_ret       = m_equity['Equity'].pct_change()
        pivot_table = m_ret.groupby([m_equity.index.year, m_equity.index.month]).sum().unstack()
        pivot_table.columns = [calendar.month_abbr[i] for i in pivot_table.columns]
        pivot_table['Total'] = annual_ret

        def color_map(val):
            if pd.isna(val): return ''
            return f'color: {"red" if val < 0 else "green"}'

        tab1, tab2, tab3, tab4 = st.tabs(["📊 Chart", "📝 Trade Logs", "📅 Monthly Returns", "⚖️ Vol Sizing"])

        with tab1:
            fig = plt.figure(figsize=(14, 24))
            gs  = gridspec.GridSpec(5, 1, height_ratios=[2,1,1,1,1], hspace=0.4)
            ax  = [fig.add_subplot(gs[i]) for i in range(5)]

            ax[0].plot(res_df.index, res_df['Equity'],    color='firebrick', lw=1.5, label='Strategy')
            ax[0].plot(res_df.index, res_df['Benchmark'], color='gray',      lw=1.0, ls='--', alpha=0.7, label=f'B&H {ticker_safe}')
            ax[0].set_yscale('log')
            ax[0].set_title("1. Equity Curve (Log Scale)", fontsize=12)
            ax[0].legend()
            ax[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))

            spy_cum  = (1 + returns_sim[ticker_safe].fillna(0)).cumprod()
            spy_dd   = ((spy_cum - spy_cum.cummax()) / spy_cum.cummax() * 100)
            ax[1].fill_between(res_df.index, res_df['Drawdown(%)'], 0, color='blue', alpha=0.20)
            ax[1].plot(res_df.index, res_df['Drawdown(%)'], color='blue',   lw=1.0, label=f'Strategy (MDD {mdd*100:.1f}%)')
            ax[1].plot(spy_dd.index, spy_dd,                color='tomato', lw=1.0, ls='--', alpha=0.8,
                       label=f'{ticker_safe} B&H (MDD {spy_dd.min():.1f}%)')
            ax[1].set_title("2. Drawdown (%) — Strategy vs SPY", fontsize=12)
            ax[1].axhline(0, color='black', lw=0.5)
            ax[1].legend(loc='lower left', fontsize=9)

            ax[2].plot(res_df.index, res_df['Safe_Price'], color='black',  lw=1.0, label=f'{ticker_safe} Price')
            ax[2].plot(res_df.index, res_df['Safe_MA'],    color='orange', lw=1.5, ls='--', label=f'Entry MA{int(ma_window)}')
            if use_asymmetric_ma:
                ax[2].plot(res_df.index, ma_safe_exit.loc[res_df.index], color='red', lw=1.2, ls=':', label=f'Exit MA{int(ma_exit_window)}')
            ax[2].set_title(f"3. Trend Signal — {'Asymmetric MA ON' if use_asymmetric_ma else 'Asymmetric MA OFF'} "
                            f"(Entry MA{int(ma_window)} / Exit MA{int(ma_exit_window)})", fontsize=12)
            ax[2].legend()

            ax[3].plot(res_df.index, df_raw.loc[res_df.index, ticker_rate], color='purple', lw=1.0, label=f'{ticker_rate}')
            ax[3].plot(res_df.index, ma_rate.loc[res_df.index],             color='green',  lw=1.5, ls='--', label=f'MA{rate_ma_window}')
            ax[3].set_title(f"4. Rate Signal ({ticker_rate})", fontsize=12)
            ax[3].legend()

            vol_s = res_df['SPY_Vol(ann,%)'].dropna()
            ax[4].plot(vol_s.index, vol_s, color='teal', lw=1.0, label='SPY Ann. Vol (%)')
            ax4b = ax[4].twinx()
            ax4b.fill_between(res_df.index, res_df['Risky_Weight(%)'], 0, color='firebrick', alpha=0.15, label='Risky Weight (%)')
            ax4b.set_ylabel('Risky Weight (%)', color='firebrick')
            ax[4].set_title("5. Volatility & Risky Asset Weight", fontsize=12)
            ax[4].legend(loc='upper left')
            ax4b.legend(loc='upper right')

            plt.tight_layout()
            st.pyplot(fig)

        with tab2:
            st.dataframe(res_df.sort_index(ascending=False), use_container_width=True)

        with tab3:
            st.dataframe(pivot_table.style.map(color_map).format("{:.2%}", na_rep=""), use_container_width=True)

        with tab4:
            st.markdown("#### Vol Sizing Analysis")
            st.line_chart(res_df[['SPY_Vol(ann,%)', 'Risky_Weight(%)']].dropna())
            st.caption("Vol UP → Risky Weight DOWN  /  Vol DOWN → Risky Weight UP")

        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                res_df.to_excel(writer, sheet_name='Daily_Log')
                pivot_table.to_excel(writer, sheet_name='Monthly_Returns')
        except Exception:
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                res_df.to_excel(writer, sheet_name='Daily_Log')
                pivot_table.to_excel(writer, sheet_name='Monthly_Returns')

        st.download_button("📥 엑셀 결과 다운로드", data=output.getvalue(),
                           file_name=f"Mix_v4_{ticker_safe}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")