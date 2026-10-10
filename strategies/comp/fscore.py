"""Quarterly TTM adaptation of the book's four/five-factor financial score."""
from __future__ import annotations

import math
import re
import unicodedata

import numpy as np
import pandas as pd


ACCOUNT_IDS = {
    'sales': ('ifrs-full_Revenue', 'ifrs_Revenue'),
    'gross': ('ifrs-full_GrossProfit', 'ifrs_GrossProfit'),
    'sga': ('dart_TotalSellingGeneralAdministrativeExpenses', 'ifrs-full_SellingGeneralAndAdministrativeExpense',
            'ifrs_SellingGeneralAndAdministrativeExpense'),
    'assets': ('ifrs-full_Assets', 'ifrs_Assets'),
    'cfo': ('ifrs-full_CashFlowsFromUsedInOperatingActivities', 'ifrs_CashFlowsFromUsedInOperatingActivities'),
    'op': ('dart_OperatingIncomeLoss',),
}
ACCOUNT_NAMES = {
    'sales': ('매출액', '매출', '수익(매출액)', '영업수익'),
    'gross': ('매출총이익', '매출총이익(손실)'),
    'sga': ('판매비와관리비', '판매비및관리비', '판매관리비', '판매및일반관리비'),
    'assets': ('자산총계', '총자산'),
    'cfo': ('영업활동현금흐름', '영업활동으로인한현금흐름', '영업활동으로부터의현금흐름'),
    'op': ('영업이익', '영업이익(손실)', '영업손익'),
}


def numeric(value):
    try:
        result = float(str(value).replace(',', '').replace('(', '-').replace(')', ''))
        return result if math.isfinite(result) else np.nan
    except (ValueError, TypeError):
        return np.nan


def account_name(value):
    name = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))
    return re.sub(r'^(?:[IVXLCDM]+|\d+)[.)]', '', name)


def parse_report(payload: dict, ticker: str, year: int, quarter: int, basis: str):
    """Use current-period YTD only, never backdate comparative disclosures."""
    rows = payload.get('list', [])
    record = {'ticker': ticker, 'year': year, 'q': quarter, 'basis': basis,
              'filed': pd.NaT, 'rcept_no': '', 'currency_valid': False}
    record.update({key: np.nan for key in ACCOUNT_IDS})
    if not rows:
        return record
    receipts = sorted({str(row.get('rcept_no', '')) for row in rows})
    if len(receipts) != 1 or not re.fullmatch(r'\d{14}', receipts[0]):
        return record
    record['rcept_no'] = receipts[0]
    record['filed'] = pd.Timestamp(receipts[0][:8])
    currencies = {row.get('currency') for row in rows if row.get('currency')}
    record['currency_valid'] = currencies == {'KRW'}
    if not record['currency_valid']:
        return record
    for key, ids in ACCOUNT_IDS.items():
        divisions = ('BS',) if key == 'assets' else ('CF',) if key == 'cfo' else ('IS', 'CIS')
        candidates = [row for row in rows if row.get('sj_div') in divisions and row.get('account_id') in ids]
        if not candidates:
            candidates = [row for row in rows if row.get('sj_div') in divisions and
                          account_name(row.get('account_nm', '')) in ACCOUNT_NAMES[key]]
        values = []
        for row in candidates:
            # Interim income statement amounts are three-month values; CF is YTD.
            field = 'thstrm_add_amount' if quarter != 4 and key not in ('assets', 'cfo') else 'thstrm_amount'
            value = numeric(row.get(field))
            if quarter == 1 and not math.isfinite(value):
                value = numeric(row.get('thstrm_amount'))
            if math.isfinite(value):
                values.append(value)
        if values and len(set(values)) == 1:
            record[key] = values[0]
    if not math.isfinite(record['sga']):
        components = []
        for account in ('ifrs-full_SellingExpense', 'ifrs-full_GeneralAndAdministrativeExpense'):
            candidates = [row for row in rows if row.get('sj_div') in ('IS', 'CIS') and row.get('account_id') == account]
            field = 'thstrm_add_amount' if quarter != 4 else 'thstrm_amount'
            values = [numeric(row.get(field)) for row in candidates]
            if quarter == 1:
                values = [numeric(row.get(field)) if math.isfinite(numeric(row.get(field))) else
                          numeric(row.get('thstrm_amount')) for row in candidates]
            finite = {value for value in values if math.isfinite(value)}
            if len(finite) == 1:
                components.append(finite.pop())
        if len(components) == 2:
            record['sga'] = sum(components)
    return record


def required_periods(year: int, quarter: int):
    if quarter == 4:
        return {(year, 4), (year - 1, 4), (year - 2, 4)}
    return {(year, quarter), (year - 1, 4), (year - 1, quarter),
            (year - 2, 4), (year - 2, quarter)}


def financial_score(records: pd.DataFrame, date, year: int, quarter: int, materials=False):
    """Return a missing reason instead of imputing missing accounts as zero."""
    result = {'fscore_raw': np.nan, 'fscore': np.nan, 'missing_reason': '', 'fscore_filed': pd.NaT,
              'fscore_year': year, 'fscore_quarter': quarter}
    visible = records[pd.to_datetime(records.filed) < pd.Timestamp(date)]
    lookup = {(int(row.year), int(row.q)): row for row in
              visible.sort_values('filed').drop_duplicates(['year', 'q'], keep='last').itertuples()}
    periods = required_periods(year, quarter)
    if not periods.issubset(lookup):
        result['missing_reason'] = 'missing_or_unpublished_period'
        return result
    used = [lookup[period] for period in periods]
    if len({row.basis for row in used}) != 1:
        result['missing_reason'] = 'mixed_statement_basis'
        return result
    keys = ['sales', 'gross', 'sga']
    flow_periods = periods - {(year - 2, quarter)} if quarter == 4 else periods
    missing = sorted({key for period in flow_periods for key in keys
                      if not math.isfinite(getattr(lookup[period], key))})
    if materials:
        current_flow = {(year, 4)} if quarter == 4 else {(year, quarter), (year - 1, 4), (year - 1, quarter)}
        missing.extend(key for period in current_flow for key in ('cfo', 'op')
                       if not math.isfinite(getattr(lookup[period], key)))
    for period in [(year - 1, quarter), (year - 2, quarter)]:
        if not math.isfinite(lookup[period].assets) or lookup[period].assets <= 0:
            missing.append('positive_beginning_assets')
    if missing:
        result['missing_reason'] = ','.join(sorted(set(missing)))
        return result
    def ttm(key, y):
        current = getattr(lookup[(y, quarter)], key)
        if quarter == 4:
            return current
        return current + getattr(lookup[(y - 1, 4)], key) - getattr(lookup[(y - 1, quarter)], key)
    sales, previous_sales = ttm('sales', year), ttm('sales', year - 1)
    if sales <= 0 or previous_sales <= 0:
        result['missing_reason'] = 'nonpositive_ttm_sales'
        return result
    flags = {
        'sales_point': int(sales > previous_sales),
        'gpm_point': int(ttm('gross', year) / sales > ttm('gross', year - 1) / previous_sales),
        'sga_point': int(ttm('sga', year) / sales < ttm('sga', year - 1) / previous_sales),
        'turnover_point': int(sales / lookup[(year - 1, quarter)].assets >
                              previous_sales / lookup[(year - 2, quarter)].assets),
    }
    raw = sum(flags.values())
    extra = int(ttm('cfo', year) > ttm('op', year)) if materials else 0
    result.update(flags, accrual_point=extra, fscore_common=raw, fscore_raw=raw + extra,
                  fscore=(raw + extra) * .8 if materials else float(raw),
                  fscore_filed=max(row.filed for row in used), sales_ttm=sales,
                  previous_sales_ttm=previous_sales,
                  gpm=ttm('gross', year) / sales,
                  previous_gpm=ttm('gross', year - 1) / previous_sales,
                  sga_ratio=ttm('sga', year) / sales,
                  previous_sga_ratio=ttm('sga', year - 1) / previous_sales,
                  assets_begin=lookup[(year - 1, quarter)].assets,
                  previous_assets_begin=lookup[(year - 2, quarter)].assets,
                  turnover=sales / lookup[(year - 1, quarter)].assets,
                  previous_turnover=previous_sales / lookup[(year - 2, quarter)].assets,
                  fscore_basis=used[0].basis)
    if materials:
        result.update(cfo_ttm=ttm('cfo', year), op_ttm=ttm('op', year))
    return result


def rank_with_fscore(ranked: pd.DataFrame, scores: pd.DataFrame, reorder=True):
    """Keep original COMP percentiles; exclude missing F-score only after scoring."""
    sample = ranked.join(scores, how='left')
    sample = sample[np.isfinite(sample.fscore)].copy()
    sample['comp_integer'] = np.floor(sample.comp).astype(int)
    if not reorder:
        return sample
    sample['ticker_tiebreak'] = sample.index
    columns = ['comp_integer', 'fscore', 'comp', 'ticker_tiebreak']
    ascending = [False, False, False, True]
    return sample.sort_values(columns, ascending=ascending, kind='stable').drop(columns='ticker_tiebreak')


def filter_by_fscore(ranked: pd.DataFrame, scores: pd.DataFrame, exclude_at_or_below: float):
    """Filter only; preserve full-precision COMP ranks and their original order."""
    if not math.isfinite(exclude_at_or_below) or not 0 <= exclude_at_or_below < 4:
        raise ValueError('Exclusion threshold must be finite and in [0, 4).')
    sample = rank_with_fscore(ranked, scores, reorder=False)
    return sample[sample.fscore > exclude_at_or_below].copy()
