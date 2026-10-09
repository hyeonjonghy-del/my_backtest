from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from strategies.comp import CompConfig, backtest, load_bundle
from strategies.comp.reporting import monthly_performance


st.set_page_config(page_title='COMP Strategy', layout='wide')
st.title('COMP')

with st.sidebar:
    upload = st.file_uploader('COMP 데이터', type=['zip'])
    # Server-owned path only; never accept arbitrary filesystem paths from visitors.
    data_dir = Path(__file__).resolve().parents[1] / 'data/comp'
    default_bundle = data_dir / 'latest.zip' if (data_dir / 'latest.zip').is_file() else data_dir / 'research.zip'
    local = Path(os.environ.get('COMP_DATA_BUNDLE', str(default_bundle)))
    if upload is not None:
        content = upload.getvalue()
    elif local.is_file():
        content = local.read_bytes()
    else:
        st.warning('데이터 미등록')
        st.stop()

fingerprint = hashlib.sha256(content).hexdigest()
if st.session_state.get('comp_bundle_id') != fingerprint:
    try:
        st.session_state.comp_dataset = load_bundle(content)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        st.error(str(error))
        st.stop()
    st.session_state.comp_bundle_id = fingerprint
    st.session_state.pop('comp_result', None)
    st.session_state.pop('comp_sector_id', None)
dataset = st.session_state.comp_dataset

with st.sidebar:
    sector_upload = st.file_uploader('과거 섹터 분류', type=['csv'])
    sector_content = sector_upload.getvalue() if sector_upload else None
    sector_id = hashlib.sha256(sector_content).hexdigest() if sector_content else None
    if st.session_state.get('comp_sector_id') != sector_id:
        try:
            dataset = load_bundle(content)
            if sector_content:
                dataset.sectors = pd.read_csv(io.BytesIO(sector_content), dtype={'ticker': str})
                dataset.validate()
            st.session_state.comp_dataset = dataset
            st.session_state.comp_sector_id = sector_id
            st.session_state.pop('comp_result', None)
        except (ValueError, KeyError) as error:
            st.error(str(error))
            st.stop()
    have_sectors = dataset.sectors is not None and not dataset.sectors.empty
    if not have_sectors:
        st.caption('과거 섹터 분류 미확보')
        st.session_state.comp_selected_only = False
        st.session_state.comp_limit_on = False
    available_start = pd.Timestamp(dataset.metadata['start']).date()
    available_end = pd.Timestamp(dataset.metadata['end']).date()
    st.caption(f'검증 가능한 기간 · {available_start} ~ {available_end}')
    start_date = st.date_input('시작일', value=available_start, min_value=available_start, max_value=available_end)
    end_date = st.date_input('종료일', value=available_end, min_value=available_start, max_value=available_end)
    interval = st.radio('리밸런싱', [4, 6], index=1, horizontal=True, format_func=lambda n: f'{n}주')
    top_n = st.number_input('목표 보유 종목 수', min_value=1, value=10, step=1)
    per_mode = st.radio('밸류에이션', ['trailing', 'none'], horizontal=True,
                        format_func=lambda value: '실적 PER' if value == 'trailing' else 'PER 제외')
    growth = st.selectbox('성장률 처리', ['positive_base', 'absolute_base'],
                          format_func=lambda value: '전기 흑자만' if value == 'positive_base' else '전기 손실 허용')
    selected_only = st.checkbox('선택 섹터만 편입', disabled=not have_sectors, key='comp_selected_only')
    options = sorted(dataset.sectors.sector.unique().tolist()) if have_sectors else []
    sectors = st.multiselect('편입 섹터', options, disabled=not (have_sectors and selected_only))
    limit_on = st.checkbox('섹터별 보유 수 제한', disabled=not have_sectors, key='comp_limit_on')
    limit = st.number_input('섹터별 최대 종목 수', min_value=1, max_value=top_n, value=min(3, top_n),
                            disabled=not (have_sectors and limit_on))
    capital = st.number_input('초기 자금 (원)', min_value=1_000_000., value=100_000_000., step=10_000_000.)
    cost = st.number_input('편도 비용 (bp)', min_value=0., max_value=100., value=10., step=1.)
    run = st.button('검증 실행', type='primary', use_container_width=True)

if run or 'comp_result' not in st.session_state or st.session_state.get('comp_result_version') != 4:
    try:
        config = CompConfig(rebalance_weeks=interval, top_n=top_n, per_mode=per_mode, growth_policy=growth,
                            sector_mode='selected' if selected_only else 'all', allowed_sectors=tuple(sectors),
                            max_per_sector=int(limit) if limit_on else None,
                            cost_bps=float(cost), initial_capital=float(capital),
                            start_date=start_date.isoformat(), end_date=end_date.isoformat())
        with st.spinner('계산 중'):
            st.session_state.comp_result = backtest(dataset, config)
            st.session_state.comp_result_version = 4
    except (ValueError, KeyError) as error:
        st.error(str(error))
        st.stop()

result = st.session_state.comp_result
metrics, curve = result['metrics'], result['equity']
st.caption(f"실제 검증 · {metrics['start']} ~ {metrics['end']} · 연구용 수정 COMP")
if metrics['end_limited_by_data']:
    st.warning(f"요청 종료일 {metrics['requested_end']} · 확보 데이터 종료일 {metrics['available_end']}. 오늘까지 검증된 결과가 아닙니다.")
active = metrics['config']
st.caption(f"적용 설정 · {active['rebalance_weeks']}주 · {active['top_n']}종목 · {active['per_mode']} · {active['growth_policy']}")
unique_buys = result['trades'].loc[result['trades'].side.eq('buy'), 'ticker'].nunique()
st.caption(f"실제 최대 동시 보유 · {int(curve.positions.max())}종목 · 기간 내 매수한 고유 종목 · {unique_buys}종목")
st.caption(f"섹터 · {active['sector_mode']} · 섹터별 상한 {active['max_per_sector'] if active['max_per_sector'] else '없음'}")
st.warning('섹터 옵션은 미검증 확장입니다. 선행 PER 대체 및 자료 누락의 한계가 있으며 자동 주문은 실행하지 않습니다.')
if (pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None).normalize() - result['signal_date']).days > 14:
    st.info('과거 연구 데이터입니다. 오늘의 매매 신호가 아닙니다.')
columns = st.columns(4)
for column, label, value in zip(columns, ['수익률', '최대 낙폭', '코스피', '코스닥'],
                                [metrics['return'], metrics['mdd'], metrics['kospi_return'], metrics['kosdaq_return']]):
    column.metric(label, f'{value:+.2%}')
overview, ranking, executions, methodology = st.tabs(['성과', '종목', '거래내역', '계산 방법'])
with overview:
    st.line_chart(curve[['nav', 'KOSPI', 'KOSDAQ']].rename(columns={'nav': 'COMP'}), height=360)
    st.line_chart(result['drawdown'] * 100, y_label='낙폭 (%)', height=240)
    for column, symbol, key in zip(st.columns(3), ['COMP', 'KOSPI', 'KOSDAQ'], ['mdd', 'kospi_mdd', 'kosdaq_mdd']):
        column.metric(f'{symbol} MDD', f'{metrics[key]:.2%}')
    if metrics['stale_position_days'] or metrics['failed_buys']:
        st.warning(f"가격 누락 보유일 {metrics['stale_position_days']} · 미체결 매수 {metrics['failed_buys']}")
    st.subheader('월별·연간 수익률')
    st.caption('첫 달은 검증 시작일부터, 마지막 달은 종료일까지 계산합니다. 연간은 해당 연도의 검증 구간 수익률이며, -는 검증 기간 밖입니다.')
    monthly = monthly_performance(curve).rename(columns={**{n: f'{n}월' for n in range(1, 13)}, 'annual': '연간'})
    monthly.index.names = ['연도', '자산']
    display_monthly = monthly.apply(lambda column: column.map(lambda value: '-' if pd.isna(value) else f'{value:+.2%}'))
    st.dataframe(display_monthly, use_container_width=True)
    st.download_button('월별·연간 수익률 CSV', monthly.to_csv().encode('utf-8-sig'), 'comp_monthly_returns.csv', 'text/csv')
    st.download_button('자산 추이 CSV', curve.to_csv().encode('utf-8-sig'), 'comp_equity.csv', 'text/csv')
with ranking:
    st.subheader(f"확정 신호 · {result['signal_date'].date()}")
    st.caption('리밸런싱 신호' if result['latest_scheduled'] else '주간 점검 신호 · 정기 교체일 아님')
    latest = result['latest']
    display = ['name', 'comp', 'rank_momentum', 'smart_money', 'price_momentum', 'valuation',
               'sector', 'selected', 'target_weight']
    st.dataframe(latest[[column for column in display if column in latest]].reset_index(),
                 hide_index=True, use_container_width=True)
with executions:
    st.dataframe(result['trades'], hide_index=True, use_container_width=True)
    st.download_button('거래 CSV', result['trades'].to_csv(index=False).encode('utf-8-sig'), 'comp_trades.csv', 'text/csv')
    st.json(metrics)
    st.download_button('설정 JSON', json.dumps(asdict(CompConfig(**metrics['config'])), ensure_ascii=False, indent=2),
                       'comp_config.json', 'application/json')
with methodology:
    st.caption(f"적용 기간 · {metrics['start']} ~ {metrics['end']} · 초기 자금 {active['initial_capital']:,.0f}원 · 편도 비용 {active['cost_bps']:g}bp")
    document = Path(__file__).resolve().parents[1] / 'strategies/comp/methodology.md'
    st.markdown(document.read_text(encoding='utf-8'))
