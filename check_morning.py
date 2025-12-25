import time
import sys

# [안전장치] 프로그램 시작부터 에러를 잡기 위한 설정
try:
    import requests
    import json
    import pandas as pd
    from datetime import datetime
    import ctypes
    import traceback
    import yfinance as yf # 여기서 에러나면 바로 잡힘

    # =========================================================
    # [사용자 설정]
    # =========================================================
    APP_KEY = "PSKt6L8yAHf3UzLE0QgRdsaq5bpEVXXqaPRE"
    APP_SECRET = "ilAbHXcqmvdzZmmXjl/xdBGCaR4hEo8wL7COom87V6POUMuQP8XvdFpzaPE4KUKDqHVAV/T2TBW+Z1QUUYPMoKykqHywPm+H6Skrv40uUzsVgAZC9hOqvLJtoE4LxH0coxnBp0hMrzw6XBLaYw0HuSJiVGQOWwH9v4j+RpdMIpMqNNp6LzQ="
    CANO = "63775153"
    ACNT_PRDT_CD = "01"
    MA_WINDOW = 200
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    # =========================================================

    def show_popup(title, message):
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x1 | 0x1000)

    def get_access_token():
        headers = {"content-type": "application/json"}
        body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
        res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
        return res.json()['access_token']

    def get_spy_ma200():
        print("📊 시장 데이터 분석 중 (yfinance)...")
        df = yf.download(['SPY'], period='2y', progress=False)
        if df.empty: raise Exception("yfinance 데이터 다운로드 실패")
        
        # 컬럼 처리 (yfinance 버전 차이 대응)
        if isinstance(df.columns, pd.MultiIndex):
            series = df['Close']['SPY']
        elif 'Close' in df.columns:
            series = df['Close']
        else:
            series = df

        if len(series) < MA_WINDOW: raise Exception(f"데이터 부족 ({len(series)}일)")

        ma200 = float(series.rolling(window=MA_WINDOW).mean().iloc[-1])
        current_price = float(series.iloc[-1])
        return current_price, ma200

    def get_holdings(token):
        print("💼 잔고 조회 중...")
        headers = {
            "content-type": "application/json", "authorization": f"Bearer {token}",
            "appKey": APP_KEY, "appSecret": APP_SECRET, "tr_id": "JTTT3012R"
        }
        params = {
            "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "OVRS_EXCG_CD": "NAS",
            "TR_CRCY_CD": "USD", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        res = requests.get(f"{URL_BASE}/uapi/overseas-stock/v1/trading/inquire-balance", headers=headers, params=params)
        
        holdings = {"SPY": 0, "UPRO": 0}
        if res.status_code == 200:
            data = res.json()
            if 'output1' in data:
                for item in data['output1']:
                    sym = item['ovrs_pdno']
                    qty = int(item['ovrs_cblc_qty'])
                    if sym in holdings: holdings[sym] = qty
        return holdings

    def main():
        print(f"\n⏰ [아침 점검] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 1. 시장 분석
        try:
            spy_price, ma200 = get_spy_ma200()
            is_bull = spy_price >= ma200
            market_status = "상승장 (UPRO 매수)" if is_bull else "하락장 (SPY 매수)"
            print(f"   - SPY: ${spy_price:.2f} | 200일선: ${ma200:.2f}")
            print(f"   -> 시장: {market_status}")
        except Exception as e:
            print(f"❌ 시장 데이터 오류: {e}")
            return

        # 2. 잔고 확인
        try:
            token = get_access_token()
            if not token: raise Exception("토큰 발급 실패")
            my_stocks = get_holdings(token)
            spy_qty = my_stocks['SPY']
            upro_qty = my_stocks['UPRO']
        except Exception as e:
            print(f"❌ 계좌 조회 오류: {e}")
            return

        # 3. 진단
        current_holding = "현금"
        if spy_qty > 0: current_holding = f"SPY {spy_qty}주"
        elif upro_qty > 0: current_holding = f"UPRO {upro_qty}주"
        
        print("-" * 40)
        print(f"🎯 목표: {market_status}")
        print(f"💼 보유: {current_holding}")
        print("-" * 40)

        action_needed = False
        msg = ""

        if is_bull:
            if upro_qty == 0:
                action_needed = True
                msg = f"🚨 [매매 실패 의심!]\n시장: 상승장 (UPRO)\n보유: {current_holding}\n확인 필요!"
            else:
                msg = f"✅ [정상] 상승장에 UPRO 보유 중."
        else:
            if spy_qty == 0:
                action_needed = True
                msg = f"🚨 [매매 실패 의심!]\n시장: 하락장 (SPY)\n보유: {current_holding}\n확인 필요!"
            else:
                msg = f"✅ [정상] 하락장에 SPY 방어 중."

        print(f"📢 결과: {msg}")
        
        if action_needed:
            print('\a')
            show_popup("⚠️ 자동매매 긴급 점검", msg)

except Exception as e:
    print("\n❌ [치명적 오류 발생] ❌")
    print(traceback.format_exc())
    print("-" * 40)

# [중요] 어떤 상황에서도 엔터키를 누르기 전까진 안 꺼짐
input("\n👉 엔터 키를 누르면 종료합니다...")