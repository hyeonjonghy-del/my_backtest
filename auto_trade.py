import requests
import json
import pandas as pd
from datetime import datetime
import time
import math

# =========================================================
# [사용자 설정] 키 값 및 보유 달러 입력
# =========================================================
APP_KEY = "PSKt6L8yAHf3UzLE0QgRdsaq5bpEVXXqaPRE"
APP_SECRET = "ilAbHXcqmvdzZmmXjl/xdBGCaR4hEo8wL7COom87V6POUMuQP8XvdFpzaPE4KUKDqHVAV/T2TBW+Z1QUUYPMoKykqHywPm+H6Skrv40uUzsVgAZC9hOqvLJtoE4LxH0coxnBp0hMrzw6XBLaYw0HuSJiVGQOWwH9v4j+RpdMIpMqNNp6LzQ="

CANO = "63775153"        # 계좌번호 앞 8자리
ACNT_PRDT_CD = "01"      # 계좌번호 뒤 2자리

# [중요] 봇이 돈을 못 찾을 때를 대비한 수동 입력값
MANUAL_CASH = 1922.39  
# =========================================================

URL_BASE = "https://openapi.koreainvestment.com:9443"

def get_access_token():
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    try:
        res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
        return res.json()['access_token']
    except: return None

def get_hashkey(datas):
    url = f"{URL_BASE}/uapi/hashkey"
    headers = {"content-type": "application/json", "appKey": APP_KEY, "appSecret": APP_SECRET}
    res = requests.post(url, headers=headers, data=datas)
    return res.json()["HASH"]

def get_current_price(token, ticker):
    """현재가 조회"""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "HHDFS76200200"
    }
    # 시세 조회는 AMS, NAS, NYS 순으로 확인
    for exchange in ["AMS", "NAS", "NYS"]:
        params = {"AUTH": "", "EXCD": exchange, "SYMB": ticker}
        res = requests.get(f"{URL_BASE}/uapi/overseas-price/v1/quotations/price", headers=headers, params=params)
        if res.status_code == 200:
            data = res.json()
            if data['output'] and data['output']['last']:
                # 시세 조회된 거래소와 상관없이 가격만 리턴
                return float(data['output']['last'])
    return None

def get_spy_ma200(token):
    print("📊 SPY 차트 분석 중...")
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "HHDFS76240000"
    }
    today = datetime.now().strftime("%Y%m%d")
    
    for exc in ["NYS", "AMS", "NAS"]:
        params = {"EXCD": exc, "SYMB": "SPY", "GUBN": "D", "BYMD": today, "MODP": "1"}
        res = requests.get(f"{URL_BASE}/uapi/overseas-price/v1/quotations/dailyprice", headers=headers, params=params)
        data = res.json()
        
        if res.status_code == 200 and 'output2' in data and len(data['output2']) > 0:
            df = pd.DataFrame(data['output2'])
            df['clos'] = pd.to_numeric(df['clos'])
            ma200 = df['clos'].head(200).mean() if len(df) >= 200 else df['clos'].mean()
            current_price = float(data['output2'][0]['clos'])
            print(f"✅ 차트 조회 성공 ({exc})")
            return current_price, ma200
            
    print("❌ SPY 차트 데이터 조회 실패")
    return None, None

def send_order_robust(token, ticker, qty, side):
    """[핵심] 거래소를 바꿔가며 주문이 될 때까지 시도"""
    side_str = "매수" if side == "buy" else "매도"
    tr_id = "TTTT1002U" if side == "buy" else "TTTT1006U"
    
    # 1. 현재가 조회
    curr_price = get_current_price(token, ticker)
    if not curr_price:
        print(f"❌ {ticker} 가격 조회 실패")
        return False

    # 지정가 (매수+5%) -> 시장가처럼 체결됨
    if side == "buy": order_price = round(curr_price * 1.05, 2)
    else: order_price = round(curr_price * 0.95, 2)
    
    print(f"   ㄴ 💰 현재가: ${curr_price} -> 넉넉한 주문가: ${order_price}")

    # 2. [핵심] 거래소 3곳 모두 시도 (NAS -> NYS -> AMS)
    exchanges_to_try = ["NAS", "NYS", "AMS"]
    
    for exchange in exchanges_to_try:
        print(f"🔄 [{ticker}] ({exchange}) {side_str} 시도... ({qty}주)")

        order_dict = {
            "CANO": CANO,
            "ACNT_PRDT_CD": ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, # 거래소 변경
            "PDNO": ticker,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(order_price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }

        json_body = json.dumps(order_dict, separators=(',', ':'))
        hashkey = get_hashkey(json_body)

        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appKey": APP_KEY,
            "appSecret": APP_SECRET,
            "tr_id": tr_id,
            "hashkey": hashkey
        }

        res = requests.post(f"{URL_BASE}/uapi/overseas-stock/v1/trading/order", headers=headers, data=json_body)
        result = res.json()

        if result['rt_cd'] == '0':
            print(f"✅ {ticker} {side_str} 성공! (거래소: {exchange}, 번호: {result['output']['ODNO']})")
            return True
        else:
            print(f"   ㄴ 실패: {result['msg1']}")
            # 실패 시 다음 거래소로 넘어감

    print(f"❌ {ticker} 최종 주문 실패 (모든 거래소 거절됨)")
    return False

def main():
    print(f"🤖 [SPY 200일선 봇] 가동 (현금: ${MANUAL_CASH})")
    token = get_access_token()
    if not token: return

    # 1. 시장 판단
    spy_price, ma200 = get_spy_ma200(token)
    if not spy_price: return

    print(f"📊 SPY 현재가: ${spy_price} | 200일선: ${ma200:.2f}")
    is_bull_market = spy_price >= ma200
    
    # 잔고 표시는 생략 (API 오류 방지)

    # 3. 매매 실행
    if is_bull_market:
        print("🚀 [상승장] -> UPRO 매수")
        
        # UPRO 풀매수 시도
        upro_price = get_current_price(token, "UPRO")
        if upro_price:
            buy_qty = int(MANUAL_CASH / (upro_price * 1.05))
            if buy_qty > 0:
                print(f"💵 현금 ${MANUAL_CASH} -> UPRO {buy_qty}주 매수 시작")
                send_order_robust(token, "UPRO", buy_qty, "buy")
            else:
                print(f"❌ 현금 부족")
        else:
            print("❌ UPRO 시세 조회 실패")
            
    else:
        print("🛡️ [하락장] -> SPY 매수")
        spy_price_curr = get_current_price(token, "SPY")
        if spy_price_curr:
            buy_qty = int(MANUAL_CASH / (spy_price_curr * 1.05))
            if buy_qty > 0:
                print(f"💵 현금 ${MANUAL_CASH} -> SPY {buy_qty}주 매수 시작")
                send_order_robust(token, "SPY", buy_qty, "buy")

if __name__ == "__main__":
    main()