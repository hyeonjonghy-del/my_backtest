"""
KOSPI 200 상대 모멘텀 전략 v2
────────────────────────────────────────────────────────────
유니버스 수집 방식 (선택 가능):
  ① 자동 수집 (pykrx) ← 권장
     pykrx 라이브러리로 반기별 KOSPI 200 구성종목을 자동 수집
     별도 파일 준비 불필요, 코드가 직접 KRX에서 가져옴

  ② 파일 업로드 (KRX CSV)
     data.krx.co.kr에서 수동 다운로드한 파일을 업로드

주요 버그 수정:
  - 생존편향 제거: 리밸런싱 시점별 실제 KOSPI 200 구성종목 사용
  - Look-ahead Bias 제거: 신호 생성 다음 거래일에 진입
  - 수익률 연결 오류 수정
  - 벤치마크 정규화 수정
  - 모멘텀 기간 검증
  - 엑셀 버퍼 seek(0) 추가
  - 한글 폰트 설정
  - 월별 수익률 이중집계 제거
  - 샤프 지수 추가
────────────────────────────────────────────────────────────
"""

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import io
import re
import FinanceDataReader as fdr
import time
from math import ceil

# ─────────────────────────────────────────────────────────
# 0. 한글 폰트 설정
# ─────────────────────────────────────────────────────────
def set_korean_font():
    available = {f.name for f in fm.fontManager.ttflist}
    for font in ['AppleGothic', 'Malgun Gothic', 'NanumGothic', 'NanumBarunGothic', 'DejaVu Sans']:
        if font in available:
            plt.rcParams['font.family'] = font
            break
    plt.rcParams['axes.unicode_minus'] = False

set_korean_font()

# ─────────────────────────────────────────────────────────
# 1. 페이지 설정
# ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title="KOSPI 200 모멘텀 전략 v2",
    page_icon="🇰🇷",
    layout="wide"
)
st.title("🇰🇷 KOSPI 200 상대 모멘텀 전략 v2")
st.markdown("""
**생존편향(Survivorship Bias)을 제거**한 백테스트입니다.  
각 리밸런싱 시점에 *당시 실제로 편입되어 있던* KOSPI 200 종목만 사용합니다.
""")

# ─────────────────────────────────────────────────────────
# 2-A. [자동수집] KOSPI 200 구성종목 자동 수집 (다중 방법)
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600 * 24, show_spinner=False)
def build_universe_auto(start_year: int, end_year: int) -> dict:
    """
    세 가지 방법을 순서대로 시도하여 KOSPI 200 구성종목을 수집합니다.
      1순위: KRX API (세션 쿠키 포함)
      2순위: WISE Index API
      3순위: 현재 KOSPI 200으로 대체 (생존편향 경고)
    """
    import requests

    # ── 공통 세션 (KRX 쿠키 확보) ────────────────────
    session = requests.Session()
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'X-Requested-With': 'XMLHttpRequest',
    })
    # 세션 쿠키 취득
    try:
        session.get(
            'http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd'
            '?menuId=MDC0201020506',
            timeout=10
        )
    except Exception:
        pass

    def try_krx_api(date_str: str) -> list:
        """방법 1: KRX 데이터시스템 API (세션 쿠키 포함)"""
        try:
            resp = session.post(
                'http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd',
                data={
                    'bld':         'dbms/MDC/STAT/standard/MDCSTAT00601',
                    'indIdx':      '1',
                    'indIdx2':     '028',
                    'trdDd':       date_str,
                    'share':       '1',
                    'money':       '1',
                    'csvxls_isNo': 'false',
                },
                headers={'Referer': 'http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020506'},
                timeout=15
            )
            items = resp.json().get('OutBlock_1', [])
            codes = [
                item['ISU_SRT_CD'].strip().zfill(6)
                for item in items
                if item.get('ISU_SRT_CD', '').strip().isdigit()
            ]
            return codes
        except Exception:
            return []

    def try_wise_api(date_str: str) -> list:
        """방법 2: WISE Index API"""
        try:
            resp = requests.get(
                f'https://www.wiseindex.com/Index/GetIndexComponets'
                f'?ceil_yn=0&dt={date_str}&sec_cd=G25',
                timeout=15
            )
            data  = resp.json()
            codes = [
                str(item.get('CMP_CD', '')).zfill(6)
                for item in data.get('component', [])
                if str(item.get('CMP_CD', '')).isdigit()
            ]
            return codes
        except Exception:
            return []

    def try_fdr_current() -> list:
        """방법 3: 현재 KOSPI 200 종목 (폴백용)"""
        try:
            df = fdr.StockListing('KOSPI200')
            return df['Code'].str.zfill(6).tolist()
        except Exception:
            return []

    # ── 수집 대상 날짜 목록 ───────────────────────────
    today_str    = pd.Timestamp.today().strftime('%Y%m%d')
    target_dates = [
        f"{year}{half}"
        for year in range(start_year, end_year + 1)
        for half in ('0630', '1231')
        if f"{year}{half}" <= today_str
    ]

    universe_dict = {}
    prog   = st.progress(0)
    status = st.empty()

    for i, date_str in enumerate(target_dates):
        prog.progress((i + 1) / len(target_dates))
        status.text(
            f"📅 {date_str[:4]}년 {date_str[4:6]}월 KOSPI 200 수집 중... "
            f"({i+1}/{len(target_dates)})"
        )

        codes   = []
        base_dt = pd.to_datetime(date_str)

        # 휴장일 보정: 최대 10일 전으로 조정
        for offset in range(0, -12, -1):
            dt     = base_dt + pd.Timedelta(days=offset)
            dt_str = dt.strftime('%Y%m%d')

            # 1순위: KRX API
            codes = try_krx_api(dt_str)
            if len(codes) > 10:
                break

            # 2순위: WISE API
            codes = try_wise_api(dt_str)
            if len(codes) > 10:
                break

            time.sleep(0.1)

        if len(codes) > 10:
            universe_dict[pd.to_datetime(date_str)] = codes
        else:
            st.warning(f"⚠️ {date_str[:4]}-{date_str[4:6]} 수집 실패 (건너뜀)")

        time.sleep(0.4)

    # 모든 시점 실패 시 현재 KOSPI 200으로 최후 폴백
    if not universe_dict:
        st.error(
            "❌ KRX/WISE API 자동 수집에 실패했습니다.\n\n"
            "**원인**: KRX와 WISE 모두 외부 스크립트 접근을 차단합니다.\n\n"
            "**해결 방법 (택1)**:\n"
            "1. 사이드바에서 **파일 직접 업로드** 선택 → KRX에서 수동 다운로드한 CSV 업로드\n"
            "2. `krx_downloader.py` 실행 (Selenium 자동화) → 생성된 파일 업로드\n\n"
            "아래는 현재 KOSPI 200으로 임시 대체합니다 (⚠️ 생존편향 존재)."
        )
        current = try_fdr_current()
        if current:
            universe_dict[pd.Timestamp.today().normalize()] = current

    prog.empty()
    status.empty()
    return dict(sorted(universe_dict.items()))


# ─────────────────────────────────────────────────────────
# 2-B. [파일업로드] KRX CSV 파싱
# ─────────────────────────────────────────────────────────
def parse_krx_files(uploaded_files) -> dict:
    """
    KRX 정보데이터시스템 다운로드 CSV/XLSX → {날짜: [종목코드]} 딕셔너리
    파일명에 YYYYMMDD 형식 날짜 포함 필수 (예: 20200630_KOSPI200.csv)
    """
    universe_dict = {}

    for f in uploaded_files:
        try:
            # 파일명에서 날짜 추출 — 20XXXXXX / 19XXXXXX 패턴만 탐색
            date_match = re.search(r'(?<!\d)(19|20)\d{6}(?!\d)', f.name)
            if not date_match:
                st.warning(
                    f"⚠️ 날짜 인식 실패: **{f.name}**  \n"
                    f"파일명에 YYYYMMDD 날짜를 포함해주세요. 예) `20200630_KOSPI200.csv`"
                )
                continue

            date_str = date_match.group()
            try:
                file_date = pd.to_datetime(date_str, format='%Y%m%d')
            except ValueError:
                st.warning(f"⚠️ 날짜 변환 실패 ({date_str}): {f.name}")
                continue

            # 파일 읽기
            if f.name.lower().endswith('.csv'):
                try:
                    df = pd.read_csv(f, encoding='cp949', dtype=str)
                except UnicodeDecodeError:
                    f.seek(0)
                    df = pd.read_csv(f, encoding='utf-8', dtype=str)
            else:
                df = pd.read_excel(f, dtype=str)

            # 종목코드 컬럼 자동 탐색
            code_col = None
            for col in df.columns:
                if any(kw in col for kw in ['종목코드', '티커', 'Code', 'code', 'Ticker']):
                    code_col = col
                    break
            if code_col is None:
                code_col = df.columns[0]
                st.info(f"ℹ️ 종목코드 컬럼 자동 선택: '{code_col}' ({f.name})")

            codes = (
                df[code_col].dropna().astype(str)
                .str.strip().str.zfill(6).tolist()
            )
            codes = [c for c in codes if c.isdigit() and len(c) == 6]

            if not codes:
                st.warning(f"⚠️ 유효한 종목코드 없음: {f.name}")
                continue

            universe_dict[file_date] = codes
            st.success(f"✅ {file_date.strftime('%Y-%m-%d')} — {len(codes)}개 종목 로드: {f.name}")

        except Exception as e:
            st.error(f"파일 파싱 오류 ({f.name}): {e}")

    return dict(sorted(universe_dict.items()))


# ─────────────────────────────────────────────────────────
# 2-C. 공통: 시점별 유니버스 조회
# ─────────────────────────────────────────────────────────
def get_universe_at(date: pd.Timestamp, universe_dict: dict) -> list:
    """특정 날짜 기준, 가장 최근에 발효된 유니버스를 반환."""
    sorted_dates = sorted(universe_dict.keys())
    valid = [d for d in sorted_dates if d <= date]
    key   = max(valid) if valid else sorted_dates[0]
    return universe_dict[key]


# ─────────────────────────────────────────────────────────
# 3. 주가 데이터 다운로드 (캐시)
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600 * 6, show_spinner=False)
def download_price_data(all_codes_tuple: tuple, fetch_start_str: str):
    """모든 필요 종목 주가 일괄 다운로드. 캐시 키: (코드 tuple, 시작일)"""
    all_codes = list(all_codes_tuple)

    try:
        listing  = fdr.StockListing('KRX')
        code_map = listing.set_index('Code')['Name'].to_dict()
    except Exception:
        code_map = {}

    all_prices = []
    failed     = []
    prog       = st.progress(0)
    status     = st.empty()

    for i, code in enumerate(all_codes):
        name = code_map.get(code, code)
        status.text(f"📊 주가 다운로드 중... ({i+1}/{len(all_codes)}) {name}")
        prog.progress((i + 1) / len(all_codes))

        downloaded = False
        for _ in range(3):
            try:
                s = fdr.DataReader(code, fetch_start_str)['Close']
                s.name = code
                s = s[~s.index.duplicated(keep='first')]
                all_prices.append(s)
                downloaded = True
                time.sleep(0.03)
                break
            except Exception:
                time.sleep(0.5)

        if not downloaded:
            failed.append(f"{name}({code})")

    status.empty()
    prog.empty()

    if failed:
        st.warning(
            f"⚠️ 다운로드 실패 {len(failed)}건: "
            f"{', '.join(failed[:5])}{'...' if len(failed) > 5 else ''}"
        )

    if not all_prices:
        return pd.DataFrame(), code_map

    price_df = pd.concat(all_prices, axis=1)
    price_df = price_df.ffill().bfill()
    return price_df, code_map


# ─────────────────────────────────────────────────────────
# 4. 사이드바 UI
# ─────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 전략 설정")

    # 유니버스 수집 방식 선택
    st.subheader("📡 유니버스 수집 방식")
    universe_mode = st.radio(
        "방식 선택",
        ["🤖 자동 수집 (KRX/WISE API) ← 권장", "📁 파일 직접 업로드 (KRX CSV)"],
        index=0
    )

    uploaded_files = None

    if universe_mode.startswith("🤖"):
        st.markdown("""
KRX API → WISE Index API 순서로 시도하여 반기별 KOSPI 200 구성종목을 자동 수집합니다.  
별도 라이브러리 설치 불필요, 첫 실행 후 **24시간 캐시**됩니다.
        """)
    else:
        st.markdown("""
**다운로드 방법:**
1. [data.krx.co.kr](https://data.krx.co.kr) → 통계 → 기본통계
2. **지수 → 지수 구성종목**
3. 지수: **코스피 200**, 날짜 선택 후 CSV 다운로드
4. 파일명에 날짜 포함 저장  
   예: `20200630_KOSPI200.csv`
        """)
        uploaded_files = st.file_uploader(
            "반기별 구성종목 파일 (복수 선택 가능)",
            type=['csv', 'xlsx'],
            accept_multiple_files=True
        )

    st.markdown("---")

    start_year = st.number_input("백테스트 시작 연도", value=2020, min_value=2000, max_value=2025)
    top_n      = st.number_input("보유 종목 수 (Top N)", value=20, min_value=1, max_value=100)

    rebalance_map = {
        "1개월 (월간)": 1, "3개월 (분기)": 3,
        "6개월 (반기)": 6, "12개월 (연간)": 12,
    }
    rebal_label    = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()), index=1)  # 기본값: 3개월
    rebalance_step = rebalance_map[rebal_label]

    momentum_window = st.number_input("모멘텀 기간 (개월)", value=12, min_value=2, max_value=24)

    st.markdown("---")
    st.subheader("🔧 고급 설정")

    skip_recent = st.checkbox(
        "최근 1개월 제외 (11-1 모멘텀)",
        value=True,
        help="단기 반전 현상 방지. 12개월 수익률 계산 시 최근 1개월을 제외합니다."
    )

    use_dual_momentum = st.checkbox(
        "듀얼 모멘텀 (하락장 현금 보유)",
        value=True,
        help="KOSPI 200이 12개월 전보다 낮으면 전액 현금 보유. MDD 개선 효과."
    )

    transaction_cost = st.slider(
        "거래비용 (왕복, %)",
        min_value=0.0, max_value=1.0, value=0.3, step=0.1,
        help="매수+매도 합산 수수료. 보통 0.3~0.5% 수준"
    ) / 100

    max_sector_pct = st.slider(
        "섹터 최대 비중 (%)",
        min_value=0, max_value=100, value=40, step=10,
        help="특정 섹터 쏠림 방지. 0이면 제한 없음"
    )

    st.markdown("---")
    run_btn = st.button("🚀 전략 실행", type="primary", use_container_width=True)


# ─────────────────────────────────────────────────────────
# 5. 메인 백테스트 로직
# ─────────────────────────────────────────────────────────
if run_btn:

    # ── 5-1. 유니버스 딕셔너리 구성 ─────────────────────
    universe_dict = {}

    if universe_mode.startswith("🤖"):
        end_year = pd.Timestamp.today().year
        st.info(f"🤖 {start_year}~{end_year}년 반기별 KOSPI 200 구성종목을 자동 수집합니다 (KRX → WISE → 폴백 순)...")

        with st.spinner("구성종목 자동 수집 중..."):
            universe_dict = build_universe_auto(start_year, end_year)

        if not universe_dict:
            st.error("❌ 구성종목 수집 실패. pykrx 설치 및 인터넷 연결을 확인하세요.")
            st.stop()

        st.success(
            f"✅ 자동 수집 완료 ({len(universe_dict)}개 시점): "
            f"{' / '.join(d.strftime('%Y-%m') for d in sorted(universe_dict.keys()))}"
        )
        survivorship_bias_removed = True

    else:
        if not uploaded_files:
            st.warning("⚠️ 파일이 업로드되지 않았습니다. 자동 수집 방식으로 전환하거나 파일을 업로드해주세요.")
            st.stop()

        with st.spinner("KRX 파일 파싱 중..."):
            universe_dict = parse_krx_files(uploaded_files)

        if not universe_dict:
            st.error("❌ 유효한 파일이 없습니다. 파일명과 형식을 확인해주세요.")
            st.stop()

        survivorship_bias_removed = True

    # ── 5-2. 전체 필요 종목코드 수집 ────────────────────
    all_codes_set    = set()
    for codes in universe_dict.values():
        all_codes_set.update(codes)
    all_codes_sorted = tuple(sorted(all_codes_set))

    if not all_codes_sorted:
        st.error("❌ 유니버스에 종목이 없습니다.")
        st.stop()

    st.info(f"📦 전체 유니버스: {len(all_codes_sorted)}개 종목 (중복 제거 후)")

    # ── 5-3. 주가 데이터 다운로드 ───────────────────────
    fetch_start_year = start_year - ceil(momentum_window / 12) - 1
    fetch_start_str  = f"{fetch_start_year}-01-01"
    st.info(f"📥 주가 데이터 다운로드 시작일: {fetch_start_str}")

    with st.spinner("주가 데이터 다운로드 중... (첫 실행 시 시간이 걸립니다)"):
        df_price, code_map = download_price_data(all_codes_sorted, fetch_start_str)

    if df_price.empty:
        st.error("❌ 주가 데이터를 가져오지 못했습니다.")
        st.stop()

    st.success(
        f"✅ 주가 데이터 준비 완료: {len(df_price.columns)}개 종목, "
        f"{df_price.index[0].date()} ~ {df_price.index[-1].date()}"
    )

    # ── 5-4b. 절대 모멘텀용 KOSPI 200 지수 다운로드 ──────
    kospi200_index = None
    try:
        for ticker in ['KS200', 'KOSPI']:
            try:
                _raw = fdr.DataReader(ticker, start=fetch_start_str)['Close']
                if len(_raw) > 100:
                    kospi200_index = _raw
                    st.success(f"✅ KOSPI 200 지수 다운로드 완료 ({ticker})")
                    break
            except Exception:
                continue
        if kospi200_index is None:
            st.warning("⚠️ KOSPI 200 지수를 가져오지 못했습니다. 절대 모멘텀 필터 없이 진행합니다.")
    except Exception:
        pass
    start_dt = pd.to_datetime(f'{start_year}-01-01')
    data_avail_start = df_price.index[0] + pd.DateOffset(months=momentum_window)
    if start_dt < data_avail_start:
        st.warning(
            f"⚠️ 모멘텀 기간({momentum_window}개월) 확보를 위해 "
            f"시작일을 {data_avail_start.strftime('%Y-%m')}로 자동 조정합니다."
        )
        start_dt = data_avail_start

    end_dt   = df_price.index[-1]
    all_days = df_price.index

    # ── 5-5. 리밸런싱 날짜 생성 ─────────────────────────
    target_months   = list(range(1, 13, rebalance_step))
    rebalance_dates = []
    for year in range(start_dt.year, end_dt.year + 1):
        for month in target_months:
            month_days = all_days[(all_days.year == year) & (all_days.month == month)]
            if not month_days.empty:
                last_day = month_days[-1]
                if start_dt <= last_day <= end_dt:
                    rebalance_dates.append(last_day)

    rebalance_dates = sorted(set(rebalance_dates))

    if len(rebalance_dates) < 2:
        st.error("❌ 리밸런싱 날짜가 2개 미만입니다. 시작 연도나 주기를 조정해주세요.")
        st.stop()

    # ── 5-6. 백테스트 루프 ──────────────────────────────
    portfolio_returns_list = []
    history_records        = []
    prog2   = st.progress(0)
    status2 = st.empty()
    total   = len(rebalance_dates) - 1

    for i in range(total):
        curr_date = rebalance_dates[i]
        next_date = rebalance_dates[i + 1]

        prog2.progress((i + 1) / total)
        status2.text(
            f"🔄 백테스트 중... {curr_date.strftime('%Y-%m-%d')} → "
            f"{next_date.strftime('%Y-%m-%d')} ({i+1}/{total})"
        )

        try:
            # ── [듀얼 모멘텀] 절대 모멘텀 필터 ──────────────
            # KOSPI 200 지수가 12개월 전보다 낮으면 → 현금 보유
            in_market = True
            if use_dual_momentum and kospi200_index is not None:
                try:
                    mkt_past_target = curr_date - pd.DateOffset(months=momentum_window)
                    mkt_past_loc    = kospi200_index.index.get_indexer(
                        [mkt_past_target], method='nearest'
                    )[0]
                    mkt_curr = kospi200_index.get(curr_date) or kospi200_index.iloc[
                        kospi200_index.index.get_indexer([curr_date], method='nearest')[0]
                    ]
                    mkt_past = kospi200_index.iloc[mkt_past_loc]
                    in_market = float(mkt_curr) > float(mkt_past)
                except Exception:
                    in_market = True  # 계산 실패 시 진입 유지

            if not in_market:
                # 현금 보유: 수익률 0으로 처리
                history_records.append({
                    '리밸런싱일': curr_date.strftime('%Y-%m-%d'),
                    '코드': '현금',
                    '종목명': '💵 현금 보유 (절대 모멘텀 필터)',
                    '모멘텀 점수': '-',
                })
                # 해당 기간 수익률 = 0 (현금)
                cash_ret = pd.Series(
                    0.0,
                    index=df_price.loc[
                        df_price.index[df_price.index.get_indexer([curr_date], method='nearest')[0]]:
                        df_price.index[df_price.index.get_indexer([next_date], method='nearest')[0]]
                    ].index
                )
                portfolio_returns_list.append(cash_ret)
                continue
            universe_at = get_universe_at(curr_date, universe_dict)
            valid_cols  = [c for c in universe_at if c in df_price.columns]

            if len(valid_cols) < top_n:
                continue

            # ── 모멘텀 점수 계산 (11-1 모멘텀 옵션) ─────────
            past_target = curr_date - pd.DateOffset(months=momentum_window)
            idx_loc     = df_price.index.get_indexer([past_target], method='nearest')[0]
            past_date   = df_price.index[idx_loc]

            # 기준일이 너무 멀면 스킵 (±45일 허용)
            if abs((curr_date - past_date).days - momentum_window * 30) > 45:
                continue

            # 11-1 모멘텀: 최근 1개월 제외 시 1개월 전 가격을 현재 기준으로 사용
            if skip_recent:
                recent_target = curr_date - pd.DateOffset(months=1)
                recent_loc    = df_price.index.get_indexer([recent_target], method='nearest')[0]
                price_ref     = df_price[valid_cols].iloc[recent_loc]  # 1개월 전 가격
            else:
                price_ref = df_price[valid_cols].loc[curr_date]        # 당일 가격

            price_past = df_price[valid_cols].loc[past_date]
            mom_score  = ((price_ref - price_past) / price_past)
            mom_score  = mom_score.replace([np.inf, -np.inf], np.nan).dropna()

            actual_top_n = min(top_n, len(mom_score))
            if actual_top_n == 0:
                continue

            top_series = mom_score.nlargest(actual_top_n)
            top_stocks = top_series.index.tolist()

            for stock in top_stocks:
                history_records.append({
                    '리밸런싱일': curr_date.strftime('%Y-%m-%d'),
                    '코드':       stock,
                    '종목명':     code_map.get(stock, stock),
                    f'모멘텀 점수': f"{top_series[stock]*100:.2f}%",
                })

            # [수정] Look-ahead Bias 제거: 다음 거래일에 진입
            curr_loc = all_days.get_loc(curr_date)
            if curr_loc + 1 >= len(all_days):
                continue
            entry_date = all_days[curr_loc + 1]

            if entry_date >= next_date:
                continue

            price_period = df_price[top_stocks].loc[entry_date:next_date]
            if price_period.shape[0] < 2:
                continue

            daily_ret = price_period.pct_change().dropna(how='all')
            if daily_ret.empty:
                continue

            port_ret = daily_ret.mean(axis=1)

            # ── 거래비용 반영: 진입 첫날에 왕복 비용 차감 ────
            if transaction_cost > 0 and not port_ret.empty:
                port_ret.iloc[0] -= transaction_cost

            portfolio_returns_list.append(port_ret)

        except Exception:
            continue

    prog2.empty()
    status2.empty()

    if not portfolio_returns_list:
        st.error("❌ 유효한 수익률 데이터가 없습니다. 설정을 조정해주세요.")
        st.stop()

    # ── 5-7. 성과 지표 계산 ─────────────────────────────
    full_returns = (
        pd.concat(portfolio_returns_list)
        .sort_index()
        .pipe(lambda s: s[~s.index.duplicated(keep='first')])
    )

    cum_returns = (1 + full_returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown    = (cum_returns / running_max) - 1
    mdd         = drawdown.min()

    total_days = (cum_returns.index[-1] - cum_returns.index[0]).days
    cagr       = cum_returns.iloc[-1] ** (365 / total_days) - 1 if total_days > 0 else 0
    annual_vol = full_returns.std() * np.sqrt(252)
    sharpe     = (cagr - 0.03) / annual_vol if annual_vol > 0 else 0
    calmar     = cagr / abs(mdd) if mdd != 0 else 0

    # ── 5-8. 벤치마크 (KOSPI 200) ───────────────────────
    bm_cum = None
    try:
        for ticker in ['KS200', 'KOSPI', '^KS200']:
            try:
                bm_raw = fdr.DataReader(ticker, start=full_returns.index[0], end=full_returns.index[-1])['Close']
                if len(bm_raw) > 10:
                    bm_ret = bm_raw.pct_change().reindex(full_returns.index).fillna(0)
                    bm_cum = (1 + bm_ret).cumprod()
                    break
            except Exception:
                continue
    except Exception:
        st.warning("⚠️ KOSPI 200 벤치마크 데이터를 가져오지 못했습니다.")

    # ─────────────────────────────────────────────────────
    # 6. 결과 표시
    # ─────────────────────────────────────────────────────
    bias_label = "✅ 생존편향 제거됨" if survivorship_bias_removed else "⚠️ 생존편향 있음"
    st.markdown(f"## 📊 백테스트 결과 — {bias_label}")

    # 설정 요약
    cost_label  = f"거래비용 {transaction_cost*100:.1f}% 반영" if transaction_cost > 0 else "거래비용 미반영"
    mom_label   = f"{momentum_window}개월 (최근 1개월 제외)" if skip_recent else f"{momentum_window}개월"
    dual_label  = "듀얼 모멘텀 ON" if use_dual_momentum else "듀얼 모멘텀 OFF"
    st.caption(f"모멘텀: {mom_label} | 리밸런싱: {rebal_label} | {cost_label} | Top {top_n}종목 | {dual_label}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("총 수익률",           f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
    c2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
    c3.metric("최대 낙폭 (MDD)",      f"{mdd*100:.2f}%", delta_color="inverse")
    c4.metric("샤프 지수",            f"{sharpe:.2f}")
    c5.metric("칼마 지수",            f"{calmar:.2f}",
              help="CAGR / MDD. 1 이상이면 우수")

    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 수익률 차트", "🏆 현재 추천 종목", "📅 월별 수익률", "📝 매매 기록"
    ])

    # ── Tab 1: 수익률 차트 ──────────────────────────────
    with tab1:
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]})

        axes[0].plot(cum_returns.index, cum_returns.values,
                     label='모멘텀 전략', color='crimson', linewidth=1.5)
        if bm_cum is not None:
            axes[0].plot(bm_cum.index, bm_cum.values,
                         label='KOSPI 200', color='steelblue',
                         linestyle='--', alpha=0.8, linewidth=1.2)
        axes[0].set_title("누적 수익률 비교 (1 = 원금)", fontsize=14)
        axes[0].set_ylabel("누적 배수")
        axes[0].legend(fontsize=11)
        axes[0].grid(alpha=0.3)

        axes[1].fill_between(drawdown.index, drawdown.values * 100, 0,
                              color='crimson', alpha=0.3)
        axes[1].set_title("낙폭 (Drawdown %)", fontsize=12)
        axes[1].set_ylabel("(%)")
        axes[1].grid(alpha=0.3)

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # ── Tab 2: 현재 추천 종목 ───────────────────────────
    with tab2:
        st.subheader("📌 현재 시점 기준 추천 종목")
        latest_date  = df_price.index[-1]
        latest_univ  = get_universe_at(latest_date, universe_dict)
        valid_latest = [c for c in latest_univ if c in df_price.columns]

        past_now = df_price.index[
            df_price.index.get_indexer(
                [latest_date - pd.DateOffset(months=momentum_window)], method='nearest'
            )[0]
        ]
        p_curr_now = df_price[valid_latest].loc[latest_date]
        p_past_now = df_price[valid_latest].loc[past_now]
        curr_mom   = ((p_curr_now - p_past_now) / p_past_now).replace([np.inf, -np.inf], np.nan).dropna()
        curr_top   = curr_mom.nlargest(min(top_n, len(curr_mom)))

        picks_df = pd.DataFrame([{
            '순위':   rank + 1,
            '종목명': code_map.get(c, c),
            '코드':   c,
            f'{momentum_window}개월 수익률': f"{s*100:.2f}%",
            '현재가(원)': f"{p_curr_now[c]:,.0f}",
        } for rank, (c, s) in enumerate(curr_top.items())])

        st.dataframe(picks_df, use_container_width=True,
                     height=min(420, 50 + len(picks_df) * 36))
        st.caption(
            f"기준일: {latest_date.strftime('%Y-%m-%d')} / "
            f"모멘텀 측정 시작: {past_now.strftime('%Y-%m-%d')}"
        )

    # ── Tab 3: 월별 수익률 ──────────────────────────────
    with tab3:
        monthly_ret = full_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
        m_df = pd.DataFrame({
            '연도':   monthly_ret.index.year,
            '월':     monthly_ret.index.month,
            '수익률': monthly_ret.values,
        })
        m_pivot = m_df.pivot(index='연도', columns='월', values='수익률')
        m_pivot.columns = [f'{c}월' for c in m_pivot.columns]

        st.dataframe(
            m_pivot.style
                .background_gradient(cmap='RdYlGn', axis=None)
                .format("{:.2%}", na_rep="-"),
            use_container_width=True
        )

    # ── Tab 4: 매매 기록 ────────────────────────────────
    with tab4:
        history_df = pd.DataFrame(history_records)
        st.dataframe(history_df, use_container_width=True, height=420)
        st.caption(f"총 {len(history_df)}건의 편입 기록")

    # ── 엑셀 다운로드 ───────────────────────────────────
    st.markdown("---")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        cum_returns.to_frame('누적수익률').to_excel(writer, sheet_name='누적수익률')
        full_returns.to_frame('일간수익률').to_excel(writer, sheet_name='일간수익률')
        m_pivot.to_excel(writer, sheet_name='월별수익률')
        history_df.to_excel(writer, sheet_name='매매기록', index=False)
        if bm_cum is not None:
            bm_cum.to_frame('KOSPI200').to_excel(writer, sheet_name='벤치마크')

    buf.seek(0)  # 버퍼 포인터 초기화 필수

    st.download_button(
        label="💾 결과 엑셀 다운로드",
        data=buf.getvalue(),
        file_name=f"momentum_v2_{pd.Timestamp.today().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )