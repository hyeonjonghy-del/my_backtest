"""
차트박사 라운드넘버존 실시간 알림봇
KIS 모의투자 API + 텔레그램 알림

실행 방법:
  1. .env 파일에 키 설정
  2. pip install requests python-dotenv pykrx schedule
  3. python chartdoctor_alert_bot.py
"""

import os
import time
import requests
import schedule
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pykrx import stock

# ────────────────────────────────────────────────────────────
# 환경변수 로드
# ────────────────────────────────────────────────────────────
load_dotenv()

KIS_APP_KEY    = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
KIS_ACCOUNT    = os.getenv("KIS_ACCOUNT")      # 501XXXXXXX
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID") # 6458302331

# 모의투자 URL
BASE_URL = "https://openapivts.koreainvestment.com:29443"

# ────────────────────────────────────────────────────────────
# 전략 파라미터 (백테스트와 동일하게)
# ────────────────────────────────────────────────────────────
TRIGGER_PCT  = 0.04   # 다음 라운드 -4%
BUY1_PCT     = 0.04   # 1차 매수: 이전 라운드 +4%
ADD_DROP_PCT = 0.10   # 추가매수 트리거 하락폭
TARGET_PCT   = 0.15   # 목표 수익률
STOP_PCT     = 0.05   # 손절 기준 (3차 이후)
MIN_CAP_억   = 5000   # 최소 시총
MAX_STOCKS   = 50     # 종목 수

# ────────────────────────────────────────────────────────────
# 라운드넘버 유틸
# ────────────────────────────────────────────────────────────
def get_round_unit(price: float) -> int:
    if price < 5_000:     return 1_000
    elif price < 50_000:  return 5_000
    elif price < 100_000: return 10_000
    elif price < 500_000: return 50_000
    else:                 return 100_000

def get_round_numbers(price: float):
    unit   = get_round_unit(price)
    prev_r = int(price // unit) * unit
    if prev_r == 0:
        prev_r = unit
    return prev_r, prev_r + unit

# ────────────────────────────────────────────────────────────
# 텔레그램
# ────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT,
            "text":    message,
            "parse_mode": "HTML"
        }, timeout=10)
        return resp.ok
    except Exception as e:
        print(f"[텔레그램 오류] {e}")
        return False

# ────────────────────────────────────────────────────────────
# KIS API - 액세스 토큰 발급
# ────────────────────────────────────────────────────────────
_access_token = None
_token_expire = None

def get_access_token() -> str:
    global _access_token, _token_expire

    if _access_token and _token_expire and datetime.now() < _token_expire:
        return _access_token

    url  = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type":    "client_credentials",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
    }
    resp = requests.post(url, json=body, timeout=10)
    data = resp.json()

    if "access_token" not in data:
        raise Exception(f"토큰 발급 실패: {data}")

    _access_token = data["access_token"]
    _token_expire = datetime.now() + timedelta(hours=23)
    print(f"[토큰] 발급 완료 (만료: {_token_expire.strftime('%H:%M')})")
    return _access_token

# ────────────────────────────────────────────────────────────
# KIS API - 현재가 조회
# ────────────────────────────────────────────────────────────
def get_current_price(ticker: str) -> dict:
    """현재가, 고가, 저가 반환"""
    token = get_access_token()
    url   = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         "VTTC0101R",  # 모의투자 현재가
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD":         ticker,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        data = resp.json()
        if data.get("rt_cd") != "0":
            return {}
        output = data.get("output", {})
        return {
            "current": float(output.get("stck_prpr", 0)),
            "high":    float(output.get("stck_hgpr", 0)),
            "low":     float(output.get("stck_lwpr", 0)),
            "volume":  int(output.get("acml_vol", 0)),
        }
    except Exception as e:
        print(f"[현재가 오류] {ticker}: {e}")
        return {}

# ────────────────────────────────────────────────────────────
# 종목 유니버스 로드
# ────────────────────────────────────────────────────────────
def load_universe() -> dict:
    """시총 상위 종목 로드 → {ticker: name}"""
    today = datetime.now().strftime("%Y%m%d")

    # 최근 거래일 찾기
    for offset in range(10):
        date_str = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            cap_df = stock.get_market_cap(date_str, market="KOSPI")
            if cap_df is None or cap_df.empty:
                continue
            filtered = cap_df[cap_df["시가총액"] >= MIN_CAP_억 * 1e8]
            filtered = filtered.sort_values("시가총액", ascending=False)
            tickers  = filtered.index.tolist()[:MAX_STOCKS]
            if tickers:
                universe = {}
                for t in tickers:
                    try:
                        universe[t] = stock.get_market_ticker_name(t)
                    except:
                        universe[t] = t
                print(f"[유니버스] {len(universe)}개 종목 로드 완료 ({date_str} 기준)")
                return universe
        except:
            continue
    return {}

# ────────────────────────────────────────────────────────────
# 신호 상태 관리
# ────────────────────────────────────────────────────────────
triggered_tickers = set()   # 트리거 발동된 종목
alerted_tickers   = set()   # 오늘 이미 알림 보낸 종목

def reset_daily():
    """장 시작 시 상태 초기화"""
    global triggered_tickers, alerted_tickers
    triggered_tickers = set()
    alerted_tickers   = set()
    print(f"[초기화] {datetime.now().strftime('%Y-%m-%d')} 상태 초기화 완료")

# ────────────────────────────────────────────────────────────
# 핵심 스캔 함수
# ────────────────────────────────────────────────────────────
universe = {}

def scan_signals():
    """전체 종목 스캔 → 라운드넘버존 신호 감지"""
    global universe

    if not universe:
        print("[스캔] 유니버스 없음, 스킵")
        return

    now = datetime.now()
    print(f"[스캔] {now.strftime('%H:%M:%S')} — {len(universe)}개 종목 스캔 중...")

    signal_count = 0

    for ticker, name in universe.items():
        if ticker in alerted_tickers:
            continue

        price_data = get_current_price(ticker)
        if not price_data or price_data["current"] <= 0:
            time.sleep(0.05)
            continue

        c = price_data["current"]
        h = price_data["high"]

        prev_r, next_r = get_round_numbers(c)
        trigger_price  = next_r * (1 - TRIGGER_PCT)
        buy1_price     = prev_r * (1 + BUY1_PCT)
        buy2_price     = buy1_price * (1 - ADD_DROP_PCT)
        buy3_price     = buy2_price * (1 - ADD_DROP_PCT)
        target_price   = buy1_price * (1 + TARGET_PCT)

        # 트리거 감지: 고가가 다음 라운드 -4% 터치
        if h >= trigger_price and ticker not in triggered_tickers:
            triggered_tickers.add(ticker)
            print(f"[트리거] {name}({ticker}) 트리거 발동! 현재가: {c:,.0f}")

        # 1차 매수 신호: 트리거 발동 후 현재가가 이전라운드+4% 이하
        if ticker in triggered_tickers and c <= buy1_price:
            if ticker not in alerted_tickers:
                alerted_tickers.add(ticker)
                signal_count += 1

                msg = f"""🚨 <b>[라운드넘버존 신호]</b>

📌 <b>{name}</b> ({ticker})
⏰ {now.strftime('%H:%M:%S')}

💰 현재가:    <b>{c:>10,.0f}원</b>
🔵 이전 라운드: {prev_r:>10,.0f}원
🔴 다음 라운드: {next_r:>10,.0f}원

📋 <b>매수 계획</b>
 1차 매수가: <b>{buy1_price:>10,.0f}원</b> (이전라운드+{BUY1_PCT*100:.0f}%)
 2차 매수가:  {buy2_price:>10,.0f}원 (1차가-{ADD_DROP_PCT*100:.0f}%)
 3차 매수가:  {buy3_price:>10,.0f}원 (2차가-{ADD_DROP_PCT*100:.0f}%)

🎯 목표가:   <b>{target_price:>10,.0f}원</b> (+{TARGET_PCT*100:.0f}%)
🛑 손절가:    {buy1_price*(1-STOP_PCT):>10,.0f}원 (-{STOP_PCT*100:.0f}%, 3차후)

📊 라운드 단위: {get_round_unit(c):,}원"""

                send_telegram(msg)
                print(f"[알림] {name}({ticker}) 신호 전송 완료!")

        time.sleep(0.1)  # API 호출 간격

    if signal_count > 0:
        print(f"[스캔 완료] {signal_count}개 신호 발생")
    else:
        print(f"[스캔 완료] 신호 없음")

# ────────────────────────────────────────────────────────────
# 장 시작/종료 알림
# ────────────────────────────────────────────────────────────
def morning_setup():
    """09:00 — 유니버스 로드 + 장 시작 알림"""
    global universe
    print("[장 시작] 유니버스 로드 중...")
    reset_daily()
    universe = load_universe()

    msg = f"""🌅 <b>장 시작 알림</b>
{datetime.now().strftime('%Y-%m-%d %H:%M')}

📊 분석 대상: <b>{len(universe)}개 종목</b>
💡 시총 {MIN_CAP_억:,}억 이상 KOSPI 상위 {MAX_STOCKS}개

스캔 주기: 5분마다
전략: 라운드넘버존 진입 감지"""

    send_telegram(msg)
    print(f"[장 시작] 유니버스 {len(universe)}개 로드 완료")

def market_close():
    """15:30 — 장 종료 알림"""
    msg = f"""🌆 <b>장 종료 알림</b>
{datetime.now().strftime('%Y-%m-%d %H:%M')}

오늘 발생한 신호: <b>{len(alerted_tickers)}건</b>
{chr(10).join([f"• {universe.get(t, t)}({t})" for t in alerted_tickers]) if alerted_tickers else "• 신호 없음"}

내일 장 시작(09:00)에 다시 스캔 시작합니다."""

    send_telegram(msg)
    print("[장 종료] 알림 전송 완료")

# ────────────────────────────────────────────────────────────
# 스케줄 설정
# ────────────────────────────────────────────────────────────
def setup_schedule():
    # 장 시작: 09:00 유니버스 로드
    schedule.every().day.at("09:00").do(morning_setup)

    # 장중 스캔: 09:05 ~ 15:20, 5분마다
    for h in range(9, 16):
        for m in range(0, 60, 5):
            if h == 9 and m < 5:
                continue
            if h == 15 and m > 20:
                continue
            t = f"{h:02d}:{m:02d}"
            schedule.every().day.at(t).do(scan_signals)

    # 장 종료: 15:30
    schedule.every().day.at("15:30").do(market_close)

    print("[스케줄] 설정 완료")
    print("  - 유니버스 로드: 09:00")
    print("  - 스캔: 09:05 ~ 15:20 (5분 간격)")
    print("  - 장 종료: 15:30")

# ────────────────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  차트박사 라운드넘버존 알림봇 시작")
    print("=" * 50)

    # 키 확인
    if not all([KIS_APP_KEY, KIS_APP_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT]):
        print("[오류] .env 파일의 키를 확인해 주세요!")
        exit(1)

    # 시작 알림
    send_telegram("🤖 <b>차트박사 알림봇 시작!</b>\n설정 확인 중...")

    # 토큰 테스트
    try:
        token = get_access_token()
        send_telegram("✅ KIS API 연결 성공!")
        print("[KIS] API 연결 성공")
    except Exception as e:
        send_telegram(f"❌ KIS API 연결 실패: {e}")
        print(f"[KIS] API 연결 실패: {e}")
        exit(1)

    # 장중이면 즉시 유니버스 로드 + 스캔
    now = datetime.now()
    if now.weekday() < 5 and 9 <= now.hour < 16:
        print("[즉시 실행] 장중 감지 → 바로 시작!")
        morning_setup()
        scan_signals()

    # 스케줄 실행
    setup_schedule()

    print("\n[봇 실행 중] Ctrl+C 로 종료\n")

    while True:
        schedule.run_pending()
        time.sleep(30)
