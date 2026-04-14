"""
KOSPI 200 상대 모멘텀 전략 v2
────────────────────────────────────────────────────────────
주요 개선사항:
  [핵심] 생존편향(Survivorship Bias) 제거
         - KRX 정보데이터시스템의 반기별 구성종목 파일을 사용
         - 리밸런싱 시점마다 당시 실제 KOSPI 200 종목으로 유니버스 구성
  [수정] Look-ahead Bias 제거: curr_date 다음 거래일에 진입
  [수정] 수익률 연결 오류 수정: entry_date ~ next_date 슬라이싱
  [수정] 벤치마크 정규화 수정: 동일 날짜 기준으로 1에서 시작
  [수정] 모멘텀 기간 검증: 데이터 충분히 쌓인 이후부터 시작
  [수정] 엑셀 버퍼 seek(0) 추가
  [수정] 한글 폰트 설정 추가
  [수정] 월별 수익률 이중집계 오류 수정
  [추가] 샤프 지수 추가
────────────────────────────────────────────────────────────
KRX 데이터 다운로드 방법:
  1. https://data.krx.co.kr 접속
  2. 기본통계 → 주식 → 세부안내 → 지수 구성종목
  3. 지수 = "코스피 200" 선택, 날짜 선택 후 CSV 다운로드
  4. 파일명에 날짜가 포함되도록 저장 (예: 20200601_KOSPI200.csv)
  5. 반기(6월말/12월말) 기준으로 백테스트 기간만큼 준비
────────────────────────────────────────────────────────────
"""

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import io
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
KRX 정보데이터시스템에서 다운로드한 **반기별 구성종목 파일**을 업로드하면,  
각 리밸런싱 시점에 *당시 실제로 편입되어 있던* KOSPI 200 종목만 사용합니다.
""")

# ─────────────────────────────────────────────────────────
# 2. KRX 구성종목 파일 파싱 함수
# ─────────────────────────────────────────────────────────
def parse_krx_files(uploaded_files):
    """
    KRX 정보데이터시스템 다운로드 파일 → {날짜: [종목코드 리스트]} 딕셔너리

    파일명 규칙: 날짜 8자리(YYYYMMDD) 포함 필수
    예) 20200630_KOSPI200.csv / KPI200_20231229.csv

    KRX CSV 주요 컬럼: '종목코드', '티커', 'Code' 중 하나
    """
    universe_dict = {}

    for f in uploaded_files:
        try:
            # ── 파일명에서 날짜 추출 ──────────────────
            digits = ''.join(filter(str.isdigit, f.name))
            date_str = digits[:8] if len(digits) >= 8 else None
            if not date_str:
                st.warning(f"⚠️ 날짜 인식 실패 (파일명에 YYYYMMDD 포함 필요): {f.name}")
                continue
            file_date = pd.to_datetime(date_str, format='%Y%m%d')

            # ── 파일 읽기 ─────────────────────────────
            if f.name.lower().endswith('.csv'):
                # KRX는 기본적으로 cp949 인코딩
                try:
                    df = pd.read_csv(f, encoding='cp949', dtype=str)
                except UnicodeDecodeError:
                    f.seek(0)
                    df = pd.read_csv(f, encoding='utf-8', dtype=str)
            else:
                df = pd.read_excel(f, dtype=str)

            # ── 종목코드 컬럼 자동 탐색 ──────────────
            code_col = None
            for col in df.columns:
                if any(kw in col for kw in ['종목코드', '티커', 'Code', 'code', 'Ticker']):
                    code_col = col
                    break
            if code_col is None:
                # 첫 번째 컬럼을 종목코드로 추정
                code_col = df.columns[0]
                st.info(f"ℹ️ 종목코드 컬럼을 자동 선택했습니다: '{code_col}' ({f.name})")

            codes = (
                df[code_col]
                .dropna()
                .astype(str)
                .str.strip()
                .str.zfill(6)  # 6자리 맞춤
                .tolist()
            )
            codes = [c for c in codes if c.isdigit() and len(c) == 6]

            if not codes:
                st.warning(f"⚠️ 유효한 종목코드가 없습니다: {f.name}")
                continue

            universe_dict[file_date] = codes
            st.success(f"✅ {file_date.strftime('%Y-%m-%d')} — {len(codes)}개 종목 로드: {f.name}")

        except Exception as e:
            st.error(f"파일 파싱 오류 ({f.name}): {e}")

    return dict(sorted(universe_dict.items()))


def get_universe_at(date, universe_dict):
    """
    특정 날짜 기준, 가장 최근에 발효된 유니버스를 반환.
    모든 파일 날짜보다 이전이면 가장 오래된 유니버스 사용.
    """
    sorted_dates = sorted(universe_dict.keys())
    valid = [d for d in sorted_dates if d <= date]
    key = max(valid) if valid else sorted_dates[0]
    return universe_dict[key]


# ─────────────────────────────────────────────────────────
# 3. 주가 데이터 다운로드 (캐시)
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600 * 6, show_spinner=False)
def download_price_data(all_codes_tuple, fetch_start_str):
    """
    필요한 모든 종목의 주가를 일괄 다운로드.
    캐시 키: (종목코드 tuple, 시작일 문자열)
    """
    all_codes = list(all_codes_tuple)

    # 종목코드 → 종목명 매핑
    try:
        listing = fdr.StockListing('KRX')
        code_map = listing.set_index('Code')['Name'].to_dict()
    except Exception:
        code_map = {}

    all_prices = []
    failed = []

    prog = st.progress(0)
    status = st.empty()

    for i, code in enumerate(all_codes):
        name = code_map.get(code, code)
        status.text(f"📊 주가 다운로드 중... ({i+1}/{len(all_codes)}) {name}")
        prog.progress((i + 1) / len(all_codes))

        downloaded = False
        for attempt in range(3):
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
        st.warning(f"⚠️ 다운로드 실패 {len(failed)}건: {', '.join(failed[:5])}{'...' if len(failed)>5 else ''}")

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

    # ── KRX 파일 업로드 ──────────────────────────
    st.subheader("📁 KRX 구성종목 파일 업로드")
    st.markdown("""
**다운로드 방법:**
1. [KRX 정보데이터시스템](https://data.krx.co.kr) 접속
2. 기본통계 → 주식 → 세부안내 → **지수 구성종목**
3. 지수: **코스피 200** 선택
4. 날짜 (6월 말/12월 말) 선택 후 **CSV 다운로드**
5. 파일명에 날짜 포함 저장  
   예: `20200630_KOSPI200.csv`

백테스트 기간에 맞게 **반기별 파일**을 모두 준비하세요.
    """)

    uploaded_files = st.file_uploader(
        "반기별 구성종목 파일 (복수 선택 가능)",
        type=['csv', 'xlsx'],
        accept_multiple_files=True
    )

    st.markdown("---")

    # ── 전략 파라미터 ─────────────────────────────
    start_year = st.number_input(
        "백테스트 시작 연도", value=2020, min_value=2000, max_value=2025
    )
    top_n = st.number_input(
        "보유 종목 수 (Top N)", value=20, min_value=1, max_value=100
    )

    rebalance_map = {
        "1개월 (월간)": 1,
        "3개월 (분기)": 3,
        "6개월 (반기)": 6,
        "12개월 (연간)": 12,
    }
    rebal_label = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()))
    rebalance_step = rebalance_map[rebal_label]

    momentum_window = st.number_input(
        "모멘텀 기간 (개월)", value=12, min_value=1, max_value=24
    )

    st.markdown("---")
    run_btn = st.button("🚀 전략 실행", type="primary", use_container_width=True)


# ─────────────────────────────────────────────────────────
# 5. 메인 백테스트 로직
# ─────────────────────────────────────────────────────────
if run_btn:

    # ── 5-1. 유니버스 딕셔너리 구성 ────────────────
    if uploaded_files:
        with st.spinner("KRX 파일 파싱 중..."):
            universe_dict = parse_krx_files(uploaded_files)

        if not universe_dict:
            st.error("❌ 유효한 파일이 없습니다. 파일명과 형식을 확인해주세요.")
            st.stop()

        st.info(
            f"📅 로드된 유니버스 시점: "
            f"{' / '.join(d.strftime('%Y-%m') for d in sorted(universe_dict.keys()))}"
        )
        survivorship_bias_removed = True

    else:
        # 파일 없을 때 현재 KOSPI 200으로 폴백 (생존편향 경고)
        st.warning(
            "⚠️ KRX 파일이 업로드되지 않았습니다. "
            "**현재 KOSPI 200** 종목으로 진행하며, 생존편향이 존재합니다."
        )
        survivorship_bias_removed = False

        import requests
        from bs4 import BeautifulSoup

        _status = st.empty()
        _status.text("🌐 현재 KOSPI 200 명단 수집 중...")
        kospi200_codes = []
        base_url = "https://finance.naver.com/sise/entryJongmok.naver?&page="
        try:
            for page in range(1, 11):
                r = requests.get(
                    base_url + str(page),
                    headers={'User-agent': 'Mozilla/5.0'},
                    timeout=5
                )
                soup = BeautifulSoup(r.text, 'html.parser')
                tds = soup.find_all('td', class_='ctg')
                if not tds:
                    break
                for td in tds:
                    try:
                        code = td.a['href'].split('code=')[-1]
                        kospi200_codes.append(code)
                    except Exception:
                        pass
        except Exception as e:
            st.error(f"크롤링 오류: {e}")
            st.stop()
        _status.empty()

        today = pd.Timestamp.today().normalize()
        universe_dict = {today: kospi200_codes}

    # ── 5-2. 전체 필요 종목코드 수집 ───────────────
    all_codes_set = set()
    for codes in universe_dict.values():
        all_codes_set.update(codes)
    all_codes_sorted = tuple(sorted(all_codes_set))

    if not all_codes_sorted:
        st.error("❌ 유니버스에 종목이 없습니다.")
        st.stop()

    st.info(f"📦 전체 유니버스: {len(all_codes_sorted)}개 종목 (중복 제거 후)")

    # ── 5-3. 주가 다운로드 ─────────────────────────
    # 모멘텀 기간을 커버할 수 있도록 충분히 이전부터 다운로드
    fetch_start_year = start_year - ceil(momentum_window / 12) - 1
    fetch_start_str = f"{fetch_start_year}-01-01"

    st.info(f"📥 주가 데이터 다운로드 시작일: {fetch_start_str}")

    with st.spinner("주가 데이터 다운로드 중... (첫 실행 시 시간이 걸립니다)"):
        df_price, code_map = download_price_data(all_codes_sorted, fetch_start_str)

    if df_price.empty:
        st.error("❌ 주가 데이터를 가져오지 못했습니다.")
        st.stop()

    st.success(f"✅ 주가 데이터 준비 완료: {len(df_price.columns)}개 종목, {df_price.index[0].date()} ~ {df_price.index[-1].date()}")

    # ── 5-4. 백테스트 날짜 범위 설정 ───────────────
    start_dt = pd.to_datetime(f'{start_year}-01-01')

    # [버그 수정] 모멘텀 기간만큼 데이터가 충분히 쌓인 후 시작
    data_avail_start = df_price.index[0] + pd.DateOffset(months=momentum_window)
    if start_dt < data_avail_start:
        st.warning(
            f"⚠️ 모멘텀 기간({momentum_window}개월) 확보를 위해 "
            f"시작일을 {data_avail_start.strftime('%Y-%m')}로 자동 조정합니다."
        )
        start_dt = data_avail_start

    end_dt = df_price.index[-1]
    all_days = df_price.index

    # ── 5-5. 리밸런싱 날짜 생성 ────────────────────
    target_months = list(range(1, 13, rebalance_step))
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
        st.error("❌ 리밸런싱 날짜가 2개 미만입니다. 시작 연도나 리밸런싱 주기를 조정해주세요.")
        st.stop()

    # ── 5-6. 백테스트 루프 ─────────────────────────
    portfolio_returns_list = []
    history_records = []

    prog2 = st.progress(0)
    status2 = st.empty()
    total_periods = len(rebalance_dates) - 1

    for i in range(total_periods):
        curr_date = rebalance_dates[i]
        next_date = rebalance_dates[i + 1]

        prog2.progress((i + 1) / total_periods)
        status2.text(
            f"🔄 백테스트 중... {curr_date.strftime('%Y-%m-%d')} → {next_date.strftime('%Y-%m-%d')} "
            f"({i+1}/{total_periods})"
        )

        try:
            # ── [핵심] 해당 시점의 실제 KOSPI 200 유니버스 ──
            universe_at = get_universe_at(curr_date, universe_dict)
            valid_cols = [c for c in universe_at if c in df_price.columns]

            if len(valid_cols) < top_n:
                # 유효 종목이 너무 적으면 스킵
                continue

            # ── 모멘텀 점수 계산 ─────────────────────────
            past_target = curr_date - pd.DateOffset(months=momentum_window)
            idx_loc = df_price.index.get_indexer([past_target], method='nearest')[0]
            past_date_real = df_price.index[idx_loc]

            # 모멘텀 기준일이 너무 멀리 벗어나면 스킵 (허용 범위: ±45일)
            if abs((curr_date - past_date_real).days - momentum_window * 30) > 45:
                continue

            price_curr = df_price[valid_cols].loc[curr_date]
            price_past = df_price[valid_cols].loc[past_date_real]

            mom_score = ((price_curr - price_past) / price_past)
            mom_score = mom_score.replace([np.inf, -np.inf], np.nan).dropna()

            # 실제 보유 종목 수 (top_n 초과 불가)
            actual_top_n = min(top_n, len(mom_score))
            if actual_top_n == 0:
                continue

            top_series = mom_score.nlargest(actual_top_n)
            top_stocks = top_series.index.tolist()

            # 매매 기록 저장
            for stock in top_stocks:
                history_records.append({
                    '리밸런싱일': curr_date.strftime('%Y-%m-%d'),
                    '코드': stock,
                    '종목명': code_map.get(stock, stock),
                    f'{momentum_window}개월 모멘텀': f"{top_series[stock]*100:.2f}%",
                })

            # ── [버그 수정] Look-ahead Bias 제거 ────────────
            # curr_date 당일 종가로 신호 계산 후,
            # 다음 거래일 시가(근사: 다음 거래일 종가)부터 수익률 측정
            curr_loc = all_days.get_loc(curr_date)
            if curr_loc + 1 >= len(all_days):
                continue
            entry_date = all_days[curr_loc + 1]  # 실제 진입 시점

            if entry_date >= next_date:
                continue

            # ── 기간 수익률 계산 ─────────────────────────
            price_period = df_price[top_stocks].loc[entry_date:next_date]

            if price_period.shape[0] < 2:
                continue

            # [버그 수정] pct_change dropna → 첫 행 0 희석 문제 제거
            daily_ret = price_period.pct_change().dropna(how='all')

            if daily_ret.empty:
                continue

            # 균등 비중 포트폴리오 수익률
            port_ret = daily_ret.mean(axis=1)
            portfolio_returns_list.append(port_ret)

        except Exception:
            continue

    prog2.empty()
    status2.empty()

    if not portfolio_returns_list:
        st.error("❌ 분석 기간 내 유효한 수익률 데이터가 없습니다. 설정을 조정해주세요.")
        st.stop()

    # ── 5-7. 전체 수익률 시리즈 계산 ───────────────
    full_returns = pd.concat(portfolio_returns_list)
    full_returns = full_returns.sort_index()

    # [버그 수정] 기간 경계 중복 제거 (keep='first'로 앞 기간 우선)
    full_returns = full_returns[~full_returns.index.duplicated(keep='first')]

    cum_returns = (1 + full_returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown = (cum_returns / running_max) - 1
    mdd = drawdown.min()

    total_days = (cum_returns.index[-1] - cum_returns.index[0]).days
    cagr = cum_returns.iloc[-1] ** (365 / total_days) - 1 if total_days > 0 else 0
    annual_vol = full_returns.std() * np.sqrt(252)
    sharpe = (cagr - 0.03) / annual_vol if annual_vol > 0 else 0  # 무위험수익률 3% 가정

    # ── 5-8. 벤치마크 (KOSPI 200) ──────────────────
    # [버그 수정] 전략과 동일 날짜 기준으로 1에서 시작
    bm_cum = None
    try:
        bm_raw = fdr.DataReader(
            'KS200',
            start=full_returns.index[0],
            end=full_returns.index[-1]
        )['Close']
        bm_ret = bm_raw.pct_change()
        bm_ret = bm_ret.reindex(full_returns.index).fillna(0)
        bm_cum = (1 + bm_ret).cumprod()  # 전략과 동일하게 1에서 시작
    except Exception:
        st.warning("⚠️ KOSPI 200 벤치마크 데이터를 가져오지 못했습니다.")

    # ─────────────────────────────────────────────
    # 6. 결과 표시
    # ─────────────────────────────────────────────
    bias_label = "✅ 생존편향 제거됨" if survivorship_bias_removed else "⚠️ 생존편향 있음 (KRX 파일 미업로드)"
    st.markdown(f"## 📊 백테스트 결과 — {bias_label}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 수익률", f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
    col2.metric("연평균 수익률 (CAGR)", f"{cagr*100:.2f}%")
    col3.metric("최대 낙폭 (MDD)", f"{mdd*100:.2f}%", delta_color="inverse")
    col4.metric("샤프 지수", f"{sharpe:.2f}")

    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 수익률 차트", "🏆 현재 추천 종목", "📅 월별 수익률", "📝 매매 기록"
    ])

    # ── Tab 1: 수익률 차트 ──────────────────────────
    with tab1:
        fig, axes = plt.subplots(
            2, 1, figsize=(12, 8),
            gridspec_kw={'height_ratios': [3, 1]}
        )

        axes[0].plot(
            cum_returns.index, cum_returns.values,
            label='모멘텀 전략', color='crimson', linewidth=1.5
        )
        if bm_cum is not None:
            axes[0].plot(
                bm_cum.index, bm_cum.values,
                label='KOSPI 200', color='steelblue',
                linestyle='--', alpha=0.8, linewidth=1.2
            )
        axes[0].set_title("누적 수익률 비교 (1 = 원금)", fontsize=14)
        axes[0].set_ylabel("누적 배수")
        axes[0].legend(fontsize=11)
        axes[0].grid(alpha=0.3)

        axes[1].fill_between(
            drawdown.index, drawdown.values * 100, 0,
            color='crimson', alpha=0.3, label='Drawdown'
        )
        axes[1].set_title("낙폭 (Drawdown %)", fontsize=12)
        axes[1].set_ylabel("(%)")
        axes[1].grid(alpha=0.3)

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # ── Tab 2: 현재 추천 종목 ────────────────────────
    with tab2:
        st.subheader("📌 현재 시점 기준 추천 종목")
        latest_date = df_price.index[-1]
        latest_universe = get_universe_at(latest_date, universe_dict)
        valid_latest = [c for c in latest_universe if c in df_price.columns]

        past_target_now = latest_date - pd.DateOffset(months=momentum_window)
        idx_now = df_price.index.get_indexer([past_target_now], method='nearest')[0]
        past_now = df_price.index[idx_now]

        p_curr_now = df_price[valid_latest].loc[latest_date]
        p_past_now = df_price[valid_latest].loc[past_now]
        curr_mom = ((p_curr_now - p_past_now) / p_past_now).replace([np.inf, -np.inf], np.nan).dropna()
        curr_top = curr_mom.nlargest(min(top_n, len(curr_mom)))

        picks_df = pd.DataFrame([{
            '순위': rank + 1,
            '종목명': code_map.get(c, c),
            '코드': c,
            f'{momentum_window}개월 수익률': f"{s*100:.2f}%",
            '현재가(원)': f"{p_curr_now[c]:,.0f}",
        } for rank, (c, s) in enumerate(curr_top.items())])

        st.dataframe(picks_df, use_container_width=True, height=min(400, 40 + len(picks_df) * 35))
        st.caption(f"기준일: {latest_date.strftime('%Y-%m-%d')} / 모멘텀 측정 시작: {past_now.strftime('%Y-%m-%d')}")

    # ── Tab 3: 월별 수익률 ───────────────────────────
    with tab3:
        # [버그 수정] 이중집계 제거 - resample 후 바로 pivot
        monthly_ret = full_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
        monthly_df = pd.DataFrame({
            '연도': monthly_ret.index.year,
            '월': monthly_ret.index.month,
            '수익률': monthly_ret.values
        })
        monthly_pivot = monthly_df.pivot(index='연도', columns='월', values='수익률')
        month_labels = {i: f'{i}월' for i in range(1, 13)}
        monthly_pivot.columns = [month_labels[c] for c in monthly_pivot.columns]

        st.dataframe(
            monthly_pivot.style
                .background_gradient(cmap='RdYlGn', axis=None)
                .format("{:.2%}", na_rep="-"),
            use_container_width=True
        )

    # ── Tab 4: 매매 기록 ─────────────────────────────
    with tab4:
        history_df = pd.DataFrame(history_records)
        st.dataframe(history_df, use_container_width=True, height=400)
        st.caption(f"총 {len(history_df)}건의 편입 기록")

    # ── 엑셀 다운로드 ────────────────────────────────
    st.markdown("---")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        cum_returns.to_frame('누적수익률').to_excel(writer, sheet_name='누적수익률')
        full_returns.to_frame('일간수익률').to_excel(writer, sheet_name='일간수익률')
        monthly_pivot.to_excel(writer, sheet_name='월별수익률')
        pd.DataFrame(history_records).to_excel(writer, sheet_name='매매기록', index=False)
        if bm_cum is not None:
            bm_cum.to_frame('KOSPI200').to_excel(writer, sheet_name='벤치마크')

    buffer.seek(0)  # [버그 수정] 버퍼 포인터 초기화 (없으면 빈 파일 다운로드)

    st.download_button(
        label="💾 결과 엑셀 다운로드",
        data=buffer.getvalue(),
        file_name=f"momentum_v2_{pd.Timestamp.today().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
