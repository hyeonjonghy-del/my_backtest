import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import FinanceDataReader as fdr
import requests
import pandas as pd
from bs4 import BeautifulSoup
import time
import threading
import os
import datetime

# ========================================================
# 1. 데이터 수집 함수
# ========================================================
def get_stock_data(code):
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=3)
        soup = BeautifulSoup(response.text, 'html.parser')

        def get_val(id_name):
            try:
                text = soup.select_one(f'#{id_name}').text.replace(',', '').replace('%', '').strip()
                if not text: return None
                return float(text)
            except:
                return None

        per = get_val('_per')
        pbr = get_val('_pbr')
        div_yield = get_val('_dvr') # 배당률
        
        # 시가총액 (억 단위 변환)
        try:
            market_cap_text = soup.select_one('#_market_sum').text
            market_cap_val = market_cap_text.replace(',', '').replace('조', '').strip().split()
            if len(market_cap_val) == 1: market_cap = float(market_cap_val[0]) * 10000
            else: market_cap = float(market_cap_val[0]) * 10000 + float(market_cap_val[1])
        except:
            market_cap = 0

        # 재무제표
        try:
            html_table = soup.select('div.section.cop_analysis div.sub_section table')
            if not html_table: return None
            
            df_fin = pd.read_html(str(html_table), encoding='euc-kr')[0]
            col_idx = 3 
            
            def safe_float(val):
                try: return float(val)
                except: return None

            revenue = safe_float(df_fin.iloc[0, col_idx])
            roe = safe_float(df_fin.iloc[5, col_idx])
            eps_curr = safe_float(df_fin.iloc[9, col_idx])
            eps_prev = safe_float(df_fin.iloc[9, col_idx - 1])
        except:
            return None

        psr = round(market_cap / revenue, 2) if (revenue and revenue > 0) else None
        
        peg = 999
        if eps_prev and eps_prev > 0 and per:
            growth = (eps_curr - eps_prev) / eps_prev * 100
            if growth > 0:
                peg = round(per / growth, 2)

        return {
            '종목코드': code, 
            'PER': per, 
            'PBR': pbr, 
            'ROE': roe, 
            'PSR': psr, 
            'PEG': peg,
            '배당률': div_yield
        }
    except:
        return None

# ========================================================
# 2. 윈도우 프로그램 UI
# ========================================================
class StockApp:
    def __init__(self, root):
        self.root = root
        self.root.title("저평가 우량주 발굴기 (통합 UI 버전)")
        self.root.geometry("600x650")

        lbl_title = tk.Label(root, text="통합 조건 검색기", font=("맑은 고딕", 16, "bold"))
        lbl_title.pack(pady=15)

        # 상단 설정 (검색 개수만 남김)
        frame_top = tk.Frame(root)
        frame_top.pack(pady=5)
        tk.Label(frame_top, text="검색할 전체 시총 상위 기업 수: ").pack(side="left")
        self.entry_count = tk.Entry(frame_top, width=10)
        self.entry_count.insert(0, "50")
        self.entry_count.pack(side="left")
        tk.Label(frame_top, text="개 (KRX 통합)").pack(side="left")

        # 조건 테이블
        frame_table = tk.Frame(root)
        frame_table.pack(pady=10)

        headers = ["구분", "AND/OR", "부등호", "값 입력"]
        for col, text in enumerate(headers):
            lbl = tk.Label(frame_table, text=text, width=12, relief="solid", bg="#e1e1e1", font=("맑은 고딕", 9, "bold"))
            lbl.grid(row=0, column=col, padx=1, pady=1)

        # 항목 정의 (이름, 기본값, 기본부등호, 타입)
        # 타입: 'num'(숫자입력), 'market'(시장선택)
        items = [
            ("시장", "KOSPI", "=", "market"),  # [통합] 시장 선택이 표 안으로 들어옴
            ("PER", 20.0, "이하 (<=)", "num"), 
            ("PBR", 1.5, "이하 (<=)", "num"), 
            ("ROE", 10.0, "이상 (>=)", "num"), 
            ("배당률", 3.0, "이상 (>=)", "num"),
            ("PSR", 2.0, "이하 (<=)", "num"), 
            ("PEG", 0.5, "이하 (<=)", "num"),
        ]
        self.widgets = {}

        for row_idx, (name, default_val, default_sign, input_type) in enumerate(items, start=1):
            # 1. 이름
            tk.Label(frame_table, text=name, width=12, relief="solid", bg="white").grid(row=row_idx, column=0, padx=1, pady=1)
            
            # 2. 로직 (시장인 경우 기본값을 AND로)
            cb_logic = ttk.Combobox(frame_table, values=["사용안함", "AND", "OR"], width=10, state="readonly")
            if name == "시장": cb_logic.current(1) # AND
            else: cb_logic.current(0) # 사용안함
            cb_logic.grid(row=row_idx, column=1, padx=1, pady=1)

            # 3. 부등호
            if input_type == "market":
                # 시장은 '같음(=)' 밖에 없으므로 고정
                cb_sign = ttk.Combobox(frame_table, values=["같음 (=)"], width=10, state="readonly")
                cb_sign.current(0)
            else:
                cb_sign = ttk.Combobox(frame_table, values=["이하 (<=)", "이상 (>=)"], width=10, state="readonly")
                if "이상" in default_sign: cb_sign.current(1)
                else: cb_sign.current(0)
            
            cb_sign.grid(row=row_idx, column=2, padx=1, pady=1)

            # 4. 값 입력 (타입에 따라 다르게)
            if input_type == "market":
                # 콤보박스로 시장 선택
                entry_val = ttk.Combobox(frame_table, values=["KOSPI", "KOSDAQ"], width=10, state="readonly")
                entry_val.set(default_val)
            else:
                # 일반 숫자 입력
                entry_val = tk.Entry(frame_table, width=12)
                entry_val.insert(0, str(default_val))
            
            entry_val.grid(row=row_idx, column=3, padx=1, pady=1)

            self.widgets[name] = (cb_logic, cb_sign, entry_val, input_type)

        btn_run = tk.Button(root, text="검색 시작", bg="#0052cc", fg="white", font=("맑은 고딕", 12, "bold"), command=self.start_thread)
        btn_run.pack(fill="x", padx=20, pady=15)

        self.log_text = tk.Text(root, height=10, state="disabled", bg="#f9f9f9")
        self.log_text.pack(fill="both", padx=10, pady=5)

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def start_thread(self):
        t = threading.Thread(target=self.run_analysis)
        t.daemon = True
        t.start()

    def run_analysis(self):
        try:
            count_val = self.entry_count.get()
            if not count_val.isdigit():
                messagebox.showwarning("입력 오류", "숫자만 입력해주세요.")
                return
            count = int(count_val)

            self.log("="*45)
            self.log(f"🚀 한국 전체 시장(KRX) 시총 상위 {count}개 스캔...")
            
            # [변경] 통합 리스트(KRX)를 가져옵니다.
            df_krx = fdr.StockListing('KRX') 
            top_list = df_krx.sort_values(by='Marcap', ascending=False).head(count)
            
            results = []
            
            for idx, (i, row) in enumerate(top_list.iterrows()):
                if idx % 5 == 0:
                    self.log(f"[{idx+1}/{count}] {row['Name']} 분석 중...")
                
                # 1. 기본 데이터(시장 정보 등) 준비
                stock_market = row['Market'] # KOSPI, KOSDAQ GLOBAL, KOSDAQ 등
                
                # 2. 상세 데이터 수집
                try:
                    data = get_stock_data(row['Code'])
                except: continue
                
                if data:
                    is_and_pass = True
                    is_or_pass = False
                    has_and = False
                    has_or = False

                    # 모든 조건(시장 포함) 체크 루프
                    for key, (cb_logic, cb_sign, entry_val, input_type) in self.widgets.items():
                        logic = cb_logic.get()
                        if logic == "사용안함": continue
                        
                        # [조건 판별 로직]
                        is_meet = False
                        
                        if input_type == "market":
                            # 시장 조건 체크 (문자열 비교)
                            target_market = entry_val.get()
                            # 데이터의 시장 정보에 타겟 텍스트가 포함되는지 (예: 'KOSDAQ GLOBAL'에는 'KOSDAQ'이 포함됨)
                            if target_market in stock_market:
                                is_meet = True
                            else:
                                is_meet = False
                        else:
                            # 숫자 조건 체크
                            try: target_val = float(entry_val.get())
                            except: continue
                            
                            sign = cb_sign.get()
                            current_val = data.get(key)

                            if current_val is None:
                                is_meet = False
                            else:
                                if "이하" in sign: is_meet = (current_val <= target_val)
                                else: is_meet = (current_val >= target_val)

                        # AND / OR 로직 적용
                        if logic == "AND":
                            has_and = True
                            if not is_meet: is_and_pass = False
                        elif logic == "OR":
                            has_or = True
                            if is_meet: is_or_pass = True
                    
                    # 최종 합격 판정
                    final_pass = False
                    if not has_and and not has_or: final_pass = True 
                    elif has_and and not has_or: final_pass = is_and_pass
                    elif not has_and and has_or: final_pass = is_or_pass
                    else: final_pass = is_and_pass or is_or_pass

                    if final_pass:
                        data['종목명'] = row['Name']
                        data['시장'] = stock_market
                        results.append(data)
                        self.log(f"  ✨ {row['Name']} 합격! ({stock_market})")
                
                time.sleep(0.05)

            if results:
                # 컬럼 순서
                cols = ['종목명', '종목코드', '시장', 'PER', 'PBR', 'ROE', '배당률', 'PSR', 'PEG']
                df = pd.DataFrame(results)[cols]
                
                base_filename = "통합_투자유망종목"
                filename = f"{base_filename}.xlsx"
                
                try:
                    df.to_excel(filename, index=False)
                except PermissionError:
                    timestamp = datetime.datetime.now().strftime("%H%M%S")
                    filename = f"{base_filename}_{timestamp}.xlsx"
                    df.to_excel(filename, index=False)

                self.log(f"✅ 완료! 총 {len(results)}개 종목 발굴.")
                try: os.startfile(filename)
                except: pass
                    
                messagebox.showinfo("성공", f"{len(results)}개 종목을 찾았습니다!")
            else:
                self.log("😭 조건에 맞는 기업이 없습니다.")
                messagebox.showinfo("결과 없음", "조건에 맞는 기업이 없습니다.")

        except Exception as e:
            self.log(f"오류 발생: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = StockApp(root)
    root.mainloop()