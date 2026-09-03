"""
S&P 500 상대 모멘텀 전략
────────────────────────────────────────────────────────────
생존편향 제거 방법:
  - Wikipedia S&P500 편출입 이력을 자동 수집
  - 현재 구성종목 + 편출입 이력을 합산하여
    각 리밸런싱 시점의 실제 S&P500 구성종목을 복원
  - 수동 파일 다운로드 불필요

주요 기능:
  - 11-1 모멘텀 (최근 1개월 제외)
  - 듀얼 모멘텀 (하락장 현금 보유)
  - 거래비용 반영
  - 섹터 정보 표시
  - 샤프/칼마 지수
  - S&P 500 벤치마크 비교

필요 라이브러리:
  pip install yfinance pandas-datareader
────────────────────────────────────────────────────────────
"""

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import io
import time
import requests
from math import ceil
from datetime import datetime
from chart_utils import static_area_chart, static_yearly_returns_chart

# Keep inputs independent of the reporting start year.  This makes overlapping
# backtest periods reproducible when only the displayed start year changes.
HISTORICAL_UNIVERSE_START_YEAR = 2000
CANONICAL_PRICE_START = "2013-01-01"  # fixed shared history for reproducible 2015+ comparisons

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
    page_title="S&P 500 모멘텀 전략 v3",
    page_icon="🇺🇸",
    layout="wide"
)
title_col, run_col = st.columns([4, 1])
with title_col:
    st.title("🇺🇸 S&P 500 상대 모멘텀 전략 v3")
with run_col:
    st.write("")
    run_btn = st.button("Run backtest", type="primary", use_container_width=True, key="run_backtest_top")
st.markdown("""
**생존편향(Survivorship Bias)을 제거**한 백테스트입니다.  
Wikipedia S&P500 편출입 이력을 자동 수집하여 각 리밸런싱 시점의 **실제 구성종목**을 복원합니다.  
v3는 과거 유니버스 역추적 시 편출입 변경을 한 번씩만 순차 적용하도록 수정했습니다.
""")

# ─────────────────────────────────────────────────────────
# 2. Wikipedia에서 S&P500 구성종목 + 편출입 이력 수집
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600 * 24, show_spinner=False)
def build_sp500_universe() -> tuple:
    """
    Wikipedia에서 S&P500 현재 구성종목과 편출입 이력을 수집하여
    시점별 실제 구성종목 딕셔너리를 반환합니다.

    반환:
      universe_dict : {날짜: [티커 리스트]}
      sector_map    : {티커: 섹터}
    """
    WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers  = {'User-Agent': 'Mozilla/5.0'}

    status = st.empty()
    status.text("🌐 Wikipedia에서 S&P500 데이터 수집 중...")

    try:
        import io
        html_text = requests.get(WIKI_URL, headers=headers, timeout=15).text
        tables = pd.read_html(io.StringIO(html_text))
    except Exception as e:
        st.error(f"❌ Wikipedia 접속 실패: {e}")
        return {}, {}

    # ── 현재 구성종목 (첫 번째 테이블) ────────────────────
    current_df  = tables[0]
    # 컬럼명 정규화
    current_df.columns = [c.strip() for c in current_df.columns]

    ticker_col = next((c for c in current_df.columns
                       if 'ticker' in c.lower() or 'symbol' in c.lower()), current_df.columns[0])
    sector_col = next((c for c in current_df.columns
                       if 'sector' in c.lower() or 'gics' in c.lower()), None)

    current_tickers = (
        current_df[ticker_col]
        .astype(str).str.strip()
        .str.replace(r'\.', '-', regex=True)   # BRK.B → BRK-B (yfinance 형식)
        .tolist()
    )

    sector_map = {}
    if sector_col:
        sector_map = dict(zip(
            current_df[ticker_col].astype(str).str.strip().str.replace(r'\.', '-', regex=True),
            current_df[sector_col].astype(str)
        ))

    # ── 편출입 이력 (두 번째 테이블) ──────────────────────
    changes_df = tables[1] if len(tables) > 1 else pd.DataFrame()

    # 편출입 이력으로 과거 유니버스 복원
    # 현재 구성종목을 기준으로 역방향으로 재구성
    all_tickers_ever = set(current_tickers)
    change_records   = []   # [(날짜, 추가된 티커들, 제거된 티커들)]

    if not changes_df.empty:
        changes_df.columns = [str(c).strip() for c in changes_df.columns]

        # 날짜 컬럼 찾기
        date_col = next((c for c in changes_df.columns
                         if 'date' in c.lower()), changes_df.columns[0])
        # 추가/제거 컬럼 찾기
        added_col   = next((c for c in changes_df.columns
                            if 'add' in c.lower()), None)
        removed_col = next((c for c in changes_df.columns
                            if 'remov' in c.lower() or 'delet' in c.lower()), None)

        if added_col and removed_col:
            for _, row in changes_df.iterrows():
                try:
                    raw_date = str(row[date_col]).strip()
                    dt = pd.to_datetime(raw_date, errors='coerce')
                    if pd.isna(dt) or dt.year < 2000:
                        continue

                    added   = str(row[added_col]).strip().replace('.', '-')
                    removed = str(row[removed_col]).strip().replace('.', '-')

                    added_list   = [t for t in [added]   if t and t not in ('nan', '-', '')]
                    removed_list = [t for t in [removed] if t and t not in ('nan', '-', '')]

                    all_tickers_ever.update(added_list)
                    all_tickers_ever.update(removed_list)

                    change_records.append((dt, added_list, removed_list))
                except Exception:
                    continue

    # 변경 이력을 날짜 순으로 정렬
    change_records.sort(key=lambda x: x[0])

    # 리밸런싱 기준 날짜 목록 생성 (분기말 기준)
    today     = pd.Timestamp.today()
    quarters  = pd.date_range(
        start=f"{HISTORICAL_UNIVERSE_START_YEAR}-01-01",
        end=today,
        freq='QE'   # 분기말
    )

    # 현재 → 과거 방향으로 역추적하여 시점별 유니버스 복원
    universe_at_date = {}
    current_set = set(current_tickers)
    reversed_changes = list(reversed(change_records))
    change_idx = 0

    # 가장 최근 분기부터 역방향으로 복원.
    # 현재 시점에서 q_date까지 되감는 동안 발생한 편출입은 한 번씩만 역적용한다.
    for q_date in reversed(quarters):
        while change_idx < len(reversed_changes) and reversed_changes[change_idx][0] > q_date:
            _, added, removed = reversed_changes[change_idx]
            for t in added:
                current_set.discard(t)
            for t in removed:
                current_set.add(t)
            change_idx += 1

        universe_at_date[q_date] = set(current_set)

    # 딕셔너리 정렬
    universe_dict = {
        k: sorted(v)
        for k, v in sorted(universe_at_date.items())
    }

    status.empty()

    total_tickers = len(all_tickers_ever)
    st.success(
        f"✅ Wikipedia 수집 완료: "
        f"현재 {len(current_tickers)}개 종목, "
        f"전체 유니버스 {total_tickers}개 (편출입 이력 포함), "
        f"{len(universe_dict)}개 분기 시점"
    )

    return universe_dict, sector_map


def get_universe_at(date: pd.Timestamp, universe_dict: dict) -> list:
    """특정 날짜에 가장 가까운 과거 유니버스 반환"""
    sorted_dates = sorted(universe_dict.keys())
    valid = [d for d in sorted_dates if d <= date]
    key   = max(valid) if valid else sorted_dates[0]
    return universe_dict[key]


def cash_etf_for_date(cash_prices: pd.DataFrame, decision_date: pd.Timestamp) -> str:
    """Choose an actually listed ETF at the decision date, without backfilling."""
    for ticker in ('SGOV', 'BIL'):
        if ticker in cash_prices and cash_prices[ticker].loc[:decision_date].notna().any():
            return ticker
    raise ValueError(f"{decision_date.date()}: SGOV/BIL 가격이 없어 계산할 수 없습니다.")


def mix_stock_and_cash(stock_returns: pd.Series, cash_returns: pd.Series,
                       stock_weight: float, previous_stock: float,
                       previous_cash: float, transaction_cost: float,
                       cash_ticker_changed: bool = False) -> tuple:
    """Hold the two sleeves between rebalances; weights drift with performance.

    Stock sleeve holds the shares bought at the rebalance until the next rebalance.
    Fees retain its full stock-sleeve sell/buy convention and additionally
    charge cash ETF trades (including a BIL-to-SGOV switch).
    """
    cash_weight = 1.0 - stock_weight
    cash_returns = cash_returns.reindex(stock_returns.index)
    if cash_weight == 0:
        cash_returns = pd.Series(0.0, index=stock_returns.index)
    elif cash_returns.isna().any():
        raise ValueError("현금 ETF 수익률에 결측이 있습니다. 기간을 생략하지 않고 중단합니다.")
    stock_value = stock_weight * (1.0 + stock_returns).cumprod()
    cash_value = cash_weight * (1.0 + cash_returns).cumprod()
    total = stock_value + cash_value
    returns = total.pct_change(fill_method=None)
    returns.iloc[0] = total.iloc[0] - 1.0
    cash_trade = (previous_cash + cash_weight if cash_ticker_changed
                  else abs(previous_cash - cash_weight))
    returns.iloc[0] -= transaction_cost * (previous_stock + stock_weight + cash_trade)
    return returns, stock_value / total


def buy_and_hold_equal_weight_returns(prices: pd.DataFrame) -> pd.Series:
    """Buy equal-dollar positions once and hold their shares until rebalance."""
    if prices.shape[0] < 2 or prices.shape[1] == 0:
        return pd.Series(dtype=float, name="Momentum")
    entry_prices = prices.iloc[0]
    if entry_prices.isna().any() or (entry_prices <= 0).any():
        raise ValueError("리밸런싱 진입일의 종가가 없거나 0 이하인 종목이 있습니다.")
    # Split the stock sleeve equally at the entry close and keep share counts
    # fixed. Each position's weight then drifts with its price until rebalance.
    relative_values = prices.div(entry_prices, axis=1)
    nav = relative_values.mean(axis=1)
    returns = nav.pct_change(fill_method=None).iloc[1:]
    returns.name = "Momentum"
    return returns


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def download_cash_prices(start_str: str, end_str: str) -> pd.DataFrame:
    import yfinance as yf
    # auto_adjust includes cash ETF distributions in close-to-close returns.
    raw = yf.download(['SGOV', 'BIL'], start=start_str, end=end_str,
                      auto_adjust=True, progress=False)['Close']
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    raw = raw.loc[~raw.index.duplicated()].sort_index()
    if any(t not in raw or raw[t].dropna().empty for t in ('SGOV', 'BIL')):
        raise ValueError("SGOV/BIL 중 다운로드되지 않은 종목이 있습니다.")
    return raw


# ─────────────────────────────────────────────────────────
# 3. 주가 데이터 다운로드 (yfinance)
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600 * 6, show_spinner=False)
def download_price_data(tickers_tuple: tuple, start_str: str, end_str: str) -> pd.DataFrame:
    """yfinance로 종목 주가 일괄 다운로드"""
    try:
        import yfinance as yf
    except ImportError:
        st.error("❌ yfinance가 설치되지 않았습니다. `pip install yfinance` 실행 후 재시작하세요.")
        return pd.DataFrame()

    tickers = list(tickers_tuple)
    prog    = st.progress(0)
    status  = st.empty()
    all_prices = []
    failed     = []

    # 배치 다운로드 (50개씩)
    batch_size = 50
    batches    = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]

    for b_idx, batch in enumerate(batches):
        status.text(
            f"📊 주가 다운로드 중... "
            f"({b_idx*batch_size+1}~{min((b_idx+1)*batch_size, len(tickers))}/{len(tickers)})"
        )
        prog.progress((b_idx + 1) / len(batches))

        try:
            raw = yf.download(
                batch,
                start=start_str,
                end=end_str,
                auto_adjust=True,
                progress=False,
                threads=True,
            )['Close']

            if isinstance(raw, pd.Series):
                raw = raw.to_frame(name=batch[0])

            # 컬럼명 정리
            raw.columns = [str(c).replace('.', '-') for c in raw.columns]
            raw.index   = pd.to_datetime(raw.index)
            raw         = raw[~raw.index.duplicated(keep='first')]

            all_prices.append(raw)
        except Exception as e:
            failed.extend(batch)

        time.sleep(0.3)

    status.empty()
    prog.empty()

    if failed:
        st.warning(f"⚠️ 다운로드 실패 {len(failed)}건")

    if not all_prices:
        return pd.DataFrame()

    price_df = pd.concat(all_prices, axis=1)
    price_df = price_df.loc[:, ~price_df.columns.duplicated()]
    # Never back-fill: it would create prices before a ticker actually traded.\n    price_df = price_df.sort_index().ffill()
    return price_df


# ─────────────────────────────────────────────────────────
# 4. 사이드바 UI
# ─────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 전략 설정")

    today_date = pd.Timestamp.today().date()
    start_year = st.number_input(
        "백테스트 시작 연도", value=2020, min_value=2000, max_value=today_date.year
    )
    end_date = st.date_input(
        "백테스트 종료일",
        value=today_date,
        min_value=pd.Timestamp("2000-01-01").date(),
        max_value=today_date,
    )
    top_n = st.number_input(
        "보유 종목 수 (Top N)", value=10, min_value=1, max_value=100
    )

    rebalance_map = {
        "1개월 (월간)": 1, "3개월 (분기)": 3,
        "6개월 (반기)": 6, "12개월 (연간)": 12,
    }
    rebal_label    = st.selectbox("리밸런싱 주기", list(rebalance_map.keys()), index=1)
    rebalance_step = rebalance_map[rebal_label]

    momentum_window = st.number_input(
        "모멘텀 기간 (개월)", value=6, min_value=2, max_value=24
    )

    st.markdown("---")
    st.subheader("🔧 고급 설정")

    skip_recent = st.checkbox(
        "최근 1개월 제외 (11-1 모멘텀)",
        value=False,
        help="단기 반전 현상 방지"
    )
    use_dual_momentum = st.checkbox(
        "듀얼 모멘텀 (하락장 현금 보유)",
        value=False,
        help="S&P500 지수가 12개월 전보다 낮으면 전액 현금 보유"
    )
    sgov_target_pct = st.slider(
        "SGOV 목표 비중 (%)", min_value=0, max_value=100,
        value=30, step=5,
        help="종목 리밸런싱일에 SGOV 목표 비중도 함께 맞춥니다. SGOV 상장 전에는 BIL을 사용합니다."
    )
    sgov_target_weight = sgov_target_pct / 100.0
    if sgov_target_pct > 0:
        st.caption(
            f"리밸런싱일 목표: 주식 {100-sgov_target_pct}% / SGOV {sgov_target_pct}% · "
            "다음 리밸런싱까지 실제 비중은 가격에 따라 변합니다. SGOV 상장 전에는 BIL을 사용합니다."
        )
    transaction_cost = st.slider(
        "거래비용 (1회, %)",
        min_value=0.0, max_value=1.0, value=0.25, step=0.05,
        help="매수 또는 매도 1회 기준 비용입니다. 왕복 거래는 2회 비용으로 반영됩니다."
    ) / 100

    st.markdown("---")


# ─────────────────────────────────────────────────────────
# 5. 메인 백테스트 로직
# ─────────────────────────────────────────────────────────
if run_btn:

    requested_end_dt = pd.to_datetime(end_date)
    if requested_end_dt < pd.to_datetime(f"{start_year}-01-01"):
        st.error("❌ 종료일은 백테스트 시작일 이후로 설정해주세요.")
        st.stop()

    # ── 5-1. S&P500 유니버스 수집 ───────────────────────
    with st.spinner("Wikipedia에서 S&P500 구성종목 수집 중..."):
        universe_dict, sector_map = build_sp500_universe()

    if not universe_dict:
        st.error("❌ 유니버스 수집 실패")
        st.stop()

    # ── 5-2. 전체 종목 수집 ─────────────────────────────
    all_tickers_set = set()
    for tickers in universe_dict.values():
        all_tickers_set.update(tickers)
    all_tickers = tuple(sorted(all_tickers_set))

    st.info(f"📦 전체 유니버스: {len(all_tickers)}개 종목 (중복 제거 후)")

    # ── 5-3. 주가 다운로드 ──────────────────────────────
    # Use a shared download window so 2015 and 2020 runs use identical
    # prices and candidates in their overlapping period.
    fetch_start = CANONICAL_PRICE_START
    fetch_end = (requested_end_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    st.info(f"📥 주가 데이터 다운로드 기간: {fetch_start} ~ {requested_end_dt.date()}")

    with st.spinner("주가 데이터 다운로드 중... (첫 실행 시 시간이 걸립니다)"):
        df_price = download_price_data(all_tickers, fetch_start, fetch_end)

    if df_price.empty:
        st.error("❌ 주가 데이터를 가져오지 못했습니다.")
        st.stop()

    df_price = df_price[df_price.index <= requested_end_dt]
    if df_price.empty:
        st.error("❌ 종료일 이전의 유효한 주가 데이터가 없습니다.")
        st.stop()

    st.success(
        f"✅ 주가 데이터 준비 완료: {len(df_price.columns)}개 종목, "
        f"{df_price.index[0].date()} ~ {df_price.index[-1].date()}"
    )

    # ── 5-4. S&P500 지수 (절대 모멘텀용) ────────────────
    sp500_index = None
    try:
        import yfinance as yf
        sp500_raw = yf.download('^GSPC', start=fetch_start, end=fetch_end, auto_adjust=True, progress=False)['Close']
        if len(sp500_raw) > 100:
            sp500_index = sp500_raw[sp500_raw.index <= requested_end_dt]
            st.success("✅ S&P500 지수 다운로드 완료")
    except Exception:
        st.warning("⚠️ S&P500 지수 다운로드 실패. 듀얼 모멘텀 없이 진행합니다.")

    # ── 5-5. 백테스트 날짜 설정 ─────────────────────────
    start_dt         = pd.to_datetime(f'{start_year}-01-01')
    data_avail_start = df_price.index[0] + pd.DateOffset(months=momentum_window)
    if start_dt < data_avail_start:
        st.warning(f"⚠️ 시작일을 {data_avail_start.strftime('%Y-%m')}로 자동 조정합니다.")
        start_dt = data_avail_start

    end_dt   = min(df_price.index[-1], requested_end_dt)
    all_days = df_price.index

    use_sgov_allocation = sgov_target_weight > 0
    cash_prices = None
    if use_sgov_allocation:
        try:
            cash_prices = download_cash_prices(fetch_start, fetch_end).loc[:end_dt]
            st.info(
                f"고정 배분 ON · 주식 {100-sgov_target_pct}% / SGOV {sgov_target_pct}% · "
                "SGOV 상장 전 BIL 대체 · SGOV 0% 결과도 동일 기간에 비교합니다."
            )
        except Exception as exc:
            st.error(f"SGOV/BIL 데이터 오류: {exc}")
            st.stop()

    # ── 5-6. 리밸런싱 날짜 생성 ─────────────────────────
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
        st.error("❌ 리밸런싱 날짜가 2개 미만입니다.")
        st.stop()

    period_dates = list(rebalance_dates)
    if end_dt > period_dates[-1]:
        period_dates.append(end_dt)
    period_dates = sorted(set(period_dates))

    # ── 5-7. 백테스트 루프 ──────────────────────────────
    portfolio_returns_list = []
    portfolio_weight_list = []
    history_records        = []
    cash_periods           = 0
    previous_position      = "cash"
    unfiltered_returns_list = []
    allocation_records = []
    previous_stock_weight = 0.0
    previous_cash_weight = 0.0
    previous_cash_ticker = None

    prog2   = st.progress(0)
    status2 = st.empty()
    total   = len(period_dates) - 1

    for i in range(total):
        curr_date = period_dates[i]
        next_date = period_dates[i + 1]

        prog2.progress((i + 1) / total)
        status2.text(
            f"🔄 백테스트 중... {curr_date.strftime('%Y-%m-%d')} → "
            f"{next_date.strftime('%Y-%m-%d')} ({i+1}/{total})"
        )

        try:
            if use_sgov_allocation:
                cash_ticker = cash_etf_for_date(cash_prices, curr_date)
            # ── 듀얼 모멘텀: 절대 모멘텀 필터 ────────────
            in_market = True
            if use_dual_momentum and sp500_index is not None:
                try:
                    mkt_past_loc = sp500_index.index.get_indexer(
                        [curr_date - pd.DateOffset(months=momentum_window)],
                        method='nearest'
                    )[0]
                    mkt_curr_loc = sp500_index.index.get_indexer(
                        [curr_date], method='nearest'
                    )[0]
                    mkt_curr  = float(sp500_index.iloc[mkt_curr_loc])
                    mkt_past  = float(sp500_index.iloc[mkt_past_loc])
                    in_market = mkt_curr > mkt_past
                except Exception:
                    in_market = True

            if not in_market:
                cash_periods += 1
                history_records.append({
                    '리밸런싱일': curr_date.strftime('%Y-%m-%d'),
                    '티커': '💵 CASH',
                    '종목명': '현금 보유 (절대 모멘텀 필터)',
                    '섹터': '-',
                    '모멘텀': '-',
                })
                curr_loc = all_days.get_loc(curr_date)
                if curr_loc + 1 < len(all_days):
                    entry_date = all_days[curr_loc + 1]
                    cash_ret = pd.Series(0.0, index=df_price.loc[entry_date:next_date].index)
                    if previous_position == "market" and transaction_cost > 0 and not cash_ret.empty:
                        cash_ret.iloc[0] -= transaction_cost
                    if use_sgov_allocation and not cash_ret.empty:
                        unfiltered_returns_list.append(cash_ret.copy())
                        cash_period_returns = cash_prices[cash_ticker].pct_change(
                            fill_method=None).reindex(cash_ret.index)
                        # entry_date is the execution close, so that day's prior-close
                        # return was not held. Returns begin on the following close.
                        cash_period_returns.iloc[0] = 0.0
                        cash_ret, stock_weights = mix_stock_and_cash(
                            pd.Series(0.0, index=cash_ret.index),
                            cash_period_returns, 0.0,
                            previous_stock_weight, previous_cash_weight, transaction_cost,
                            previous_cash_ticker is not None and previous_cash_ticker != cash_ticker,
                        )
                        previous_stock_weight, previous_cash_weight = 0.0, 1.0
                        previous_cash_ticker = cash_ticker
                        allocation_records.append({
                            '판단일': curr_date, '적용 시작일': entry_date,
                            '적용 종료일': next_date,
                            '주식 목표비중': 0.0, '현금 목표비중': 1.0,
                            '현금 ETF': cash_ticker, '사유': '듀얼 모멘텀 우선',
                        })
                        history_records[-1].update({
                            '티커': cash_ticker, '종목명': '현금 ETF (듀얼 모멘텀)',
                            '목표비중': 1.0,
                        })
                    portfolio_returns_list.append(cash_ret)
                    portfolio_weight_list.append(pd.Series(0.0, index=cash_ret.index))
                previous_position = "cash"
                continue

            # ── 당시 S&P500 구성종목 ──────────────────────
            universe_at = get_universe_at(curr_date, universe_dict)
            valid_cols  = [t for t in universe_at if t in df_price.columns]

            if len(valid_cols) < top_n:
                continue

            # ── 모멘텀 점수 계산 (11-1) ───────────────────
            past_target = curr_date - pd.DateOffset(months=momentum_window)
            past_loc    = df_price.index.get_indexer([past_target], method='nearest')[0]
            past_date   = df_price.index[past_loc]

            if abs((curr_date - past_date).days - momentum_window * 30) > 45:
                continue

            # 11-1: 최근 1개월 제외
            if skip_recent:
                ref_loc  = df_price.index.get_indexer(
                    [curr_date - pd.DateOffset(months=1)], method='nearest'
                )[0]
                price_ref = df_price[valid_cols].iloc[ref_loc]
            else:
                price_ref = df_price[valid_cols].loc[curr_date]

            price_past = df_price[valid_cols].loc[past_date]
            mom_score  = ((price_ref - price_past) / price_past)
            mom_score  = mom_score.replace([np.inf, -np.inf], np.nan).dropna()

            actual_n   = min(top_n, len(mom_score))
            if actual_n == 0:
                continue

            # Stable secondary order makes exact momentum ties reproducible.
            top_series = mom_score.sort_index().nlargest(actual_n)
            top_stocks = top_series.index.tolist()

            for stock in top_stocks:
                history_records.append({
                    '리밸런싱일': curr_date.strftime('%Y-%m-%d'),
                    '티커':       stock,
                    '종목명':     stock,
                    '섹터':       sector_map.get(stock, '-'),
                    '모멘텀':     f"{top_series[stock]*100:.2f}%",
                })

            # ── Look-ahead Bias 제거 ──────────────────────
            curr_loc = all_days.get_loc(curr_date)
            if curr_loc + 1 >= len(all_days):
                continue
            entry_date = all_days[curr_loc + 1]
            if entry_date >= next_date:
                continue

            price_period = df_price[top_stocks].loc[entry_date:next_date]
            if price_period.shape[0] < 2:
                continue

            daily_ret = price_period.pct_change(fill_method=None).dropna(how='all')
            if daily_ret.empty:
                continue

            # ── 데이터 정합성 검증 ────────────────────────
            # 하루 수익률이 ±100% 초과인 종목은 주식분할 조정 오류나
            # 티커 재사용으로 인한 데이터 오류 가능성이 매우 높으므로 제외
            bad_data_mask = (daily_ret.abs() > 1.0).any()
            valid_stocks  = daily_ret.columns[~bad_data_mask].tolist()

            if not valid_stocks:
                continue
            price_period = price_period[valid_stocks]
            daily_ret = daily_ret[valid_stocks]
            port_ret = buy_and_hold_equal_weight_returns(price_period)
            if port_ret.empty:
                continue

            # 거래비용 반영
            unfiltered_port_ret = port_ret.copy()
            if transaction_cost > 0:
                transaction_events = 1 if previous_position == "cash" else 2
                unfiltered_port_ret.iloc[0] -= transaction_cost * transaction_events
            if use_sgov_allocation:
                unfiltered_returns_list.append(unfiltered_port_ret)
                target_stock = 1.0 - sgov_target_weight
                cash_period_returns = cash_prices[cash_ticker].pct_change(
                    fill_method=None).reindex(port_ret.index)
                port_ret, stock_weights = mix_stock_and_cash(
                    port_ret, cash_period_returns, target_stock,
                    previous_stock_weight, previous_cash_weight, transaction_cost,
                    previous_cash_ticker is not None and previous_cash_ticker != cash_ticker,
                )
                previous_stock_weight = float(stock_weights.iloc[-1])
                previous_cash_weight = 1.0 - previous_stock_weight
                previous_cash_ticker = cash_ticker
                allocation_records.append({
                    '판단일': curr_date, '적용 시작일': entry_date,
                    '적용 종료일': next_date,
                    '주식 목표비중': target_stock,
                    '현금 목표비중': sgov_target_weight,
                    '현금 ETF': cash_ticker, '사유': '고정 SGOV 배분',
                })
                # Show only the stocks that actually contributed to this period.
                history_records = [r for r in history_records
                                   if r['리밸런싱일'] != curr_date.strftime('%Y-%m-%d')]
                for stock in valid_stocks:
                    history_records.append({
                        '리밸런싱일': curr_date.strftime('%Y-%m-%d'), '티커': stock,
                        '종목명': stock, '섹터': sector_map.get(stock, '-'),
                        '모멘텀': f"{top_series[stock]*100:.2f}%",
                        '목표비중': target_stock / len(valid_stocks),
                    })
                if target_stock < 1:
                    history_records.append({
                        '리밸런싱일': curr_date.strftime('%Y-%m-%d'), '티커': cash_ticker,
                        '종목명': '현금 ETF', '섹터': '미국 초단기 국채',
                        '모멘텀': '-', '목표비중': 1.0 - target_stock,
                    })
            else:
                port_ret = unfiltered_port_ret
            previous_position = "market"

            portfolio_returns_list.append(port_ret)
            portfolio_weight_list.append(stock_weights if use_sgov_allocation
                                         else pd.Series(1.0, index=port_ret.index))

        except Exception as exc:
            if use_sgov_allocation:
                st.error(f"{curr_date.date()} 리밸런싱 계산 오류: {exc}")
                st.stop()
            continue

    prog2.empty()
    status2.empty()

    if use_sgov_allocation and len(allocation_records) != total:
        covered = {r['판단일'] for r in allocation_records}
        missing = [str(d.date()) for d in period_dates[:-1] if d not in covered]
        st.error("계산에서 누락된 리밸런싱 기간이 있어 결과를 표시하지 않습니다: " + ', '.join(missing))
        st.stop()

    if not portfolio_returns_list:
        st.error("❌ 유효한 수익률 데이터가 없습니다.")
        st.stop()

    # ── 5-8. 성과 지표 계산 ─────────────────────────────
    full_returns = (
        pd.concat(portfolio_returns_list)
        .sort_index()
        .pipe(lambda s: s[~s.index.duplicated(keep='first')])
    )
    equity_weight = (
        pd.concat(portfolio_weight_list)
        .sort_index()
        .pipe(lambda s: s[~s.index.duplicated(keep='first')])
        .reindex(full_returns.index)
        .ffill()
        .fillna(0.0)
        .clip(0.0, 1.0)
    )

    cum_returns = (1 + full_returns).cumprod()
    sgov_zero_nav = None
    if use_sgov_allocation:
        sgov_zero_returns = pd.concat(unfiltered_returns_list).sort_index()
        sgov_zero_returns = sgov_zero_returns.loc[~sgov_zero_returns.index.duplicated()]
        sgov_zero_nav = (1.0 + sgov_zero_returns.reindex(full_returns.index)).cumprod()
    running_max = cum_returns.cummax()
    drawdown    = (cum_returns / running_max) - 1
    mdd         = drawdown.min()

    total_days = (cum_returns.index[-1] - cum_returns.index[0]).days
    cagr       = cum_returns.iloc[-1] ** (365 / total_days) - 1 if total_days > 0 else 0
    annual_vol = full_returns.std() * np.sqrt(252)
    sharpe     = (cagr - 0.03) / annual_vol if annual_vol > 0 else 0
    calmar     = cagr / abs(mdd) if mdd != 0 else 0

    # ── 5-9. 벤치마크 (S&P500 Buy & Hold) ──────────────
    bm_cum = None
    if sp500_index is not None:
        try:
            bm_ret = sp500_index.pct_change().reindex(full_returns.index).fillna(0)
            bm_cum = (1 + bm_ret).cumprod()
            # Series → DataFrame 변환
            if isinstance(bm_cum, pd.Series):
                bm_cum = bm_cum.to_frame('SP500')
        except Exception:
            pass

    # ─────────────────────────────────────────────────────
    # 6. 결과 표시
    # ─────────────────────────────────────────────────────
    st.markdown("## 📊 Backtest Results — ✅ Survivorship Bias Removed")

    cost_label = f"Transaction cost {transaction_cost*100:.2f}% per trade"
    mom_label  = f"{momentum_window}M (excl. last 1M)" if skip_recent else f"{momentum_window}M"
    dual_label = "Dual Momentum ON" if use_dual_momentum else "Dual Momentum OFF"
    st.caption(
        f"Momentum: {mom_label} | Rebalancing: {rebal_label} | "
        f"{cost_label} | Top {top_n} stocks | {dual_label} | "
        f"Cash periods: {cash_periods}"
    )
    if use_sgov_allocation:
        st.caption(
            f"고정 배분 · 주식 {100-sgov_target_pct}% / SGOV {sgov_target_pct}% · "
            "종목 리밸런싱일에 목표비중 복원 · SGOV 상장 전 BIL"
        )
        comparison = []
        for label, nav in [
            (f'SGOV {sgov_target_pct}%', cum_returns),
            ('SGOV 0% (동일 기간)', sgov_zero_nav),
        ]:
            years = (nav.index[-1] - nav.index[0]).days / 365.0
            comparison.append({'전략': label, '누적수익률': nav.iloc[-1] - 1,
                               'CAGR': nav.iloc[-1] ** (1 / years) - 1 if years > 0 else 0,
                               'MDD': (nav / nav.cummax() - 1).min()})
        st.dataframe(pd.DataFrame(comparison).set_index('전략').style.format('{:.2%}'),
                     use_container_width=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Return",   f"{(cum_returns.iloc[-1]-1)*100:.2f}%")
    c2.metric("CAGR",           f"{cagr*100:.2f}%")
    c3.metric("Max Drawdown",   f"{mdd*100:.2f}%", delta_color="inverse")
    c4.metric("Sharpe Ratio",   f"{sharpe:.2f}")
    c5.metric("Calmar Ratio",   f"{calmar:.2f}")

    # ── 3번 질문 답변: 다음 리밸런싱 날짜 안내 ──────────
    next_reb = rebalance_dates[-1] + pd.DateOffset(months=rebalance_step)
    st.info(
        f"📅 **Next Rebalancing:** around **{next_reb.strftime('%Y-%m')}** end "
        f"(3-month cycle: Jan / Apr / Jul / Oct end)"
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 Return Chart", "🏆 Current Picks", "📅 Monthly Returns", "📝 Trade History"
    ])

    # ── Tab 1: 수익률 차트 (영어, 현금보유 표시, MDD 비교) ──
    with tab1:
        # 현금 보유 구간 계산
        cash_periods_list = []
        if use_dual_momentum:
            history_df_tmp = pd.DataFrame(history_records)
            if not history_df_tmp.empty and '티커' in history_df_tmp.columns:
                cash_rows = history_df_tmp[history_df_tmp['티커'] == '💵 CASH']['리밸런싱일'].tolist()
                for cr in cash_rows:
                    cr_dt = pd.to_datetime(cr)
                    cr_idx = period_dates.index(cr_dt) if cr_dt in period_dates else -1
                    if cr_idx >= 0 and cr_idx + 1 < len(period_dates):
                        cash_periods_list.append((cr_dt, period_dates[cr_idx + 1]))

        # S&P500 드로다운 계산
        bm_drawdown = None
        if bm_cum is not None:
            bm_vals    = bm_cum.iloc[:, 0] if isinstance(bm_cum, pd.DataFrame) else bm_cum
            bm_running = bm_vals.cummax()
            bm_drawdown = (bm_vals / bm_running) - 1

        fig, axes = plt.subplots(
            2, 1, figsize=(12, 9), gridspec_kw={'height_ratios': [3, 1.5]}
        )

        # ── 상단: 누적 수익률 ──────────────────────────────
        axes[0].plot(
            cum_returns.index, cum_returns.values,
            label='Momentum Strategy', color='crimson', linewidth=1.5, zorder=3
        )
        if sgov_zero_nav is not None:
            axes[0].plot(sgov_zero_nav.index, sgov_zero_nav.values,
                         label='SGOV 0%', color='grey', linestyle='--', alpha=0.8)
        if bm_cum is not None:
            bm_vals = bm_cum.iloc[:, 0] if isinstance(bm_cum, pd.DataFrame) else bm_cum
            axes[0].plot(
                bm_vals.index, bm_vals.values,
                label='S&P 500 Buy & Hold', color='steelblue',
                linestyle='--', alpha=0.8, linewidth=1.2, zorder=2
            )

        # 현금 보유 구간 음영
        first_cash = True
        for start, end in cash_periods_list:
            axes[0].axvspan(
                start, end,
                color='gold', alpha=0.25,
                label='Cash (Dual Momentum)' if first_cash else None,
                zorder=1
            )
            first_cash = False

        axes[0].set_title("Cumulative Return Comparison (1 = Initial Capital)", fontsize=13)
        axes[0].set_ylabel("Multiple")
        axes[0].legend(fontsize=10)
        axes[0].grid(alpha=0.3)

        # ── 하단: MDD 비교 ─────────────────────────────────
        axes[1].fill_between(
            drawdown.index, drawdown.values * 100, 0,
            color='crimson', alpha=0.35, label='Momentum Strategy'
        )
        if bm_drawdown is not None:
            axes[1].fill_between(
                bm_drawdown.index, bm_drawdown.values * 100, 0,
                color='steelblue', alpha=0.2, label='S&P 500'
            )
        # 현금 보유 구간 MDD 차트에도 표시
        for start, end in cash_periods_list:
            axes[1].axvspan(start, end, color='gold', alpha=0.25)

        axes[1].set_title("Drawdown Comparison (%)", fontsize=11)
        axes[1].set_ylabel("Drawdown (%)")
        axes[1].legend(fontsize=9)
        axes[1].grid(alpha=0.3)

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

        yearly_nav = {"Strategy": cum_returns}
        if bm_cum is not None:
            bm_vals = bm_cum.iloc[:, 0] if isinstance(bm_cum, pd.DataFrame) else bm_cum
            yearly_nav["S&P 500"] = bm_vals
        st.pyplot(
            static_yearly_returns_chart(yearly_nav, "Yearly Returns", height=330),
            clear_figure=True,
        )

        weight_df = pd.DataFrame({
            "Equity": equity_weight,
            "SGOV / BIL" if use_sgov_allocation else "Cash": 1 - equity_weight,
        })
        st.pyplot(static_area_chart(weight_df, "Portfolio Weights", height=300), clear_figure=True)

        if cash_periods_list:
            st.caption(
                f"🟡 Yellow shading = Cash holding periods ({len(cash_periods_list)} times) "
                f"— Dual Momentum defensive mode"
            )

    # ── Tab 2: 현재 추천 종목 ───────────────────────────
    with tab2:
        if use_sgov_allocation:
            last_allocation = allocation_records[-1]
            st.subheader("최근 리밸런싱 실행 종목·목표비중")
            last_date = last_allocation['판단일'].strftime('%Y-%m-%d')
            last_holdings = pd.DataFrame([r for r in history_records if r['리밸런싱일'] == last_date])
            st.dataframe(last_holdings.style.format({'목표비중': '{:.1%}'}), use_container_width=True)
            st.caption(f"판단일 {last_date} · 목표비중이며 이후 가격 변화로 실제 비중은 달라집니다.")
            st.subheader("최신 모멘텀 순위 (다음 리밸런싱 후보)")
        else:
            st.subheader("📌 Current Picks (as of latest date)")
        latest_date  = df_price.index[-1]
        latest_univ  = get_universe_at(latest_date, universe_dict)
        valid_latest = [t for t in latest_univ if t in df_price.columns]

        past_now_loc = df_price.index.get_indexer(
            [latest_date - pd.DateOffset(months=momentum_window)], method='nearest'
        )[0]
        ref_loc = df_price.index.get_indexer(
            [latest_date - pd.DateOffset(months=1)], method='nearest'
        )[0] if skip_recent else df_price.index.get_indexer([latest_date], method='nearest')[0]

        p_ref  = df_price[valid_latest].iloc[ref_loc]
        p_past = df_price[valid_latest].iloc[past_now_loc]
        curr_mom = ((p_ref - p_past) / p_past).replace([np.inf, -np.inf], np.nan).dropna()
        curr_top = curr_mom.nlargest(min(top_n, len(curr_mom)))

        picks_df = pd.DataFrame([{
            'Rank':   rank + 1,
            'Ticker': t,
            'Sector': sector_map.get(t, '-'),
            'Momentum Return': f"{s*100:.2f}%",
            'Price (USD)': f"${df_price[t].loc[latest_date]:,.2f}",
        } for rank, (t, s) in enumerate(curr_top.items())])

        st.dataframe(picks_df, use_container_width=True,
                     height=min(420, 50 + len(picks_df) * 36))
        st.caption(f"As of: {latest_date.strftime('%Y-%m-%d')}")

    # ── Tab 3: 월별 수익률 (연간 수익률 추가) ─────────────
    with tab3:
        monthly_ret = full_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
        m_df = pd.DataFrame({
            'Year':  monthly_ret.index.year,
            'Month': monthly_ret.index.month,
            'Ret':   monthly_ret.values,
        })
        m_pivot = m_df.pivot(index='Year', columns='Month', values='Ret')
        m_pivot.columns = [f'M{c:02d}' for c in m_pivot.columns]

        # 연간 수익률 계산해서 맨 오른쪽 컬럼 추가
        annual_ret = monthly_ret.groupby(monthly_ret.index.year).apply(
            lambda x: (1 + x).prod() - 1
        )
        m_pivot['Annual'] = annual_ret

        st.dataframe(
            m_pivot.style
            .background_gradient(cmap='RdYlGn', axis=None, subset=m_pivot.columns[:-1])
            .background_gradient(cmap='RdYlGn', axis=None, subset=['Annual'])
            .format("{:.2%}", na_rep="-"),
            use_container_width=True
        )

    # ── Tab 4: 매매 기록 ────────────────────────────────
    with tab4:
        history_df = pd.DataFrame(history_records)
        st.dataframe(history_df, use_container_width=True, height=420)
        st.caption(f"Total {len(history_df)} records | Cash periods: {cash_periods}")
        if use_sgov_allocation:
            st.subheader("SGOV 목표비중 배분 이력")
            st.dataframe(pd.DataFrame(allocation_records), use_container_width=True)

    # ── 엑셀 다운로드 ───────────────────────────────────
    st.markdown("---")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        cum_returns.to_frame('누적수익률').to_excel(writer, sheet_name='누적수익률')
        full_returns.to_frame('일간수익률').to_excel(writer, sheet_name='일간수익률')
        m_pivot.to_excel(writer, sheet_name='월별수익률')
        history_df.to_excel(writer, sheet_name='매매기록', index=False)
        if use_sgov_allocation:
            pd.DataFrame(allocation_records).to_excel(writer, sheet_name='SGOV_배분이력', index=False)
            weight_df.to_excel(writer, sheet_name='주식_현금비중')
            sgov_zero_nav.to_frame('SGOV_0pct').to_excel(writer, sheet_name='SGOV0_비교')
        if bm_cum is not None:
            if isinstance(bm_cum, pd.Series):
                bm_cum.to_frame('SP500').to_excel(writer, sheet_name='벤치마크')
            else:
                bm_cum.to_excel(writer, sheet_name='벤치마크')
    buf.seek(0)

    st.download_button(
        label="💾 결과 엑셀 다운로드",
        data=buf.getvalue(),
        file_name=f"sp500_momentum_{pd.Timestamp.today().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

