import numpy as np
import pandas as pd
import pytest

from strategies.comp.fscore import financial_score, parse_report, rank_with_fscore, required_periods, filter_by_fscore


def annual_records():
    return pd.DataFrame([
        dict(year=2022, q=4, filed='2023-03-01', basis='CFS', sales=np.nan, gross=np.nan, sga=np.nan,
             assets=80., cfo=np.nan, op=np.nan),
        dict(year=2023, q=4, filed='2024-03-01', basis='CFS', sales=100., gross=20., sga=10.,
             assets=90., cfo=10., op=12.),
        dict(year=2024, q=4, filed='2025-03-01', basis='CFS', sales=150., gross=45., sga=12.,
             assets=200., cfo=30., op=25.),
    ])


def test_annual_score_and_materials_normalization():
    r = annual_records()
    score = financial_score(r, '2025-04-01', 2024, 4)
    assert score['fscore'] == score['fscore_raw'] == 4
    assert financial_score(r, '2025-04-01', 2024, 4, True)['fscore_raw'] == 5
    assert financial_score(r, '2025-04-01', 2024, 4, True)['fscore'] == 4
    r.loc[2, 'cfo'] = 20.
    assert financial_score(r, '2025-04-01', 2024, 4, True)['fscore'] == pytest.approx(3.2)
    r.loc[2, 'gross'] = 30.
    assert financial_score(r, '2025-04-01', 2024, 4)['gpm_point'] == 0


def test_missing_accounts_publication_and_basis_fail_closed():
    r = annual_records()
    assert np.isnan(financial_score(r, '2025-03-01', 2024, 4)['fscore'])
    r.loc[2, 'gross'] = np.nan
    assert 'gross' in financial_score(r, '2025-04-01', 2024, 4)['missing_reason']
    r = annual_records()
    r.loc[1, 'basis'] = 'OFS'
    assert financial_score(r, '2025-04-01', 2024, 4)['missing_reason'] == 'mixed_statement_basis'
    r = annual_records()
    r.loc[2, 'cfo'] = np.nan
    assert financial_score(r, '2025-04-01', 2024, 4)['fscore'] == 4
    assert np.isnan(financial_score(r, '2025-04-01', 2024, 4, True)['fscore'])


def test_interim_ttm_and_beginning_assets_not_latest_assets():
    r = annual_records()
    r = pd.concat([r, pd.DataFrame([
        dict(year=2023, q=1, filed='2023-05-01', basis='CFS', sales=20., gross=4., sga=2.,
             assets=80., cfo=4., op=3.),
        dict(year=2024, q=1, filed='2024-05-01', basis='CFS', sales=30., gross=6., sga=3.,
             assets=100., cfo=5., op=4.),
        dict(year=2025, q=1, filed='2025-05-01', basis='CFS', sales=60., gross=24., sga=3.,
             assets=10000., cfo=15., op=10.),
    ])], ignore_index=True)
    score = financial_score(r, '2025-05-02', 2025, 1)
    assert score['sales_ttm'] == 180
    assert score['previous_sales_ttm'] == 110
    assert score['assets_begin'] == 100
    assert score['previous_assets_begin'] == 80
    assert score['fscore_year'] == 2025 and score['fscore_quarter'] == 1
    assert score['turnover_point'] == 1  # 180/100 > 110/80; not 180/10000.
    assert score['fscore'] == 4
    assert required_periods(2025, 1) == {(2025, 1), (2024, 4), (2024, 1), (2023, 4), (2023, 1)}


def payload(amount='10', cumulative='30', division='IS', account='ifrs-full_Revenue'):
    return {'list': [dict(sj_div=division, account_id=account, account_nm='x', thstrm_amount=amount,
                         thstrm_add_amount=cumulative, currency='KRW', rcept_no='20250814000001')]}


def test_parser_cumulative_income_cf_balance_and_missing_no_imputation():
    assert parse_report(payload(), '005930', 2025, 2, 'CFS')['sales'] == 30
    assert parse_report(payload(), '005930', 2025, 4, 'CFS')['sales'] == 10
    assert np.isnan(parse_report(payload(cumulative=''), '005930', 2025, 2, 'CFS')['sales'])
    assert parse_report(payload(cumulative=''), '005930', 2025, 1, 'CFS')['sales'] == 10
    assert parse_report(payload(division='CF', account='ifrs-full_CashFlowsFromUsedInOperatingActivities'),
                        '005930', 2025, 2, 'CFS')['cfo'] == 10
    assert parse_report(payload(division='BS', account='ifrs-full_Assets'),
                        '005930', 2025, 2, 'CFS')['assets'] == 10
    p = payload()
    p['list'][0]['currency'] = 'USD'
    assert np.isnan(parse_report(p, '005930', 2025, 2, 'CFS')['sales'])


def test_integer_comp_tiebreak_and_complete_case_control():
    ranked = pd.DataFrame({'comp': [81.1, 80.9, 80.1, 79.9, 82.1]}, index=['a', 'b', 'c', 'd', 'e'])
    scores = pd.DataFrame({'fscore': [1., 1., 4., 4., np.nan]}, index=ranked.index)
    assert rank_with_fscore(ranked, scores).index.tolist() == ['a', 'c', 'b', 'd']
    assert rank_with_fscore(ranked, scores, reorder=False).index.tolist() == ['a', 'b', 'c', 'd']
    assert rank_with_fscore(ranked, scores).loc['c', 'comp'] == 80.1


def test_no_rounding_materials_score_and_deterministic_secondary_ties():
    ranked = pd.DataFrame({'comp': [80.8, 80.1, 80.4, 80.4]}, index=['000001', '000002', '000004', '000003'])
    scores = pd.DataFrame({'fscore': [3., 3.2, 4., 4.]}, index=ranked.index)
    assert rank_with_fscore(ranked, scores).index.tolist() == ['000003', '000004', '000002', '000001']


def test_zero_assets_and_zero_sales_not_zero_scores():
    records = annual_records()
    records.loc[0, 'assets'] = 0
    result = financial_score(records, '2025-04-01', 2024, 4)
    assert np.isnan(result['fscore']) and result['missing_reason'] == 'positive_beginning_assets'
    records = annual_records()
    records.loc[2, 'sales'] = 0
    result = financial_score(records, '2025-04-01', 2024, 4)
    assert np.isnan(result['fscore']) and result['missing_reason'] == 'nonpositive_ttm_sales'


def test_parser_conflicting_duplicate_accounts_excluded():
    p = payload()
    p['list'].append({**p['list'][0], 'thstrm_add_amount': '99'})
    assert np.isnan(parse_report(p, '005930', 2025, 2, 'CFS')['sales'])


def test_sga_standard_ifrs_and_explicit_components():
    p = payload(account='ifrs-full_SellingGeneralAndAdministrativeExpense')
    assert parse_report(p, '005930', 2025, 2, 'CFS')['sga'] == 30
    p = payload(account='ifrs-full_SellingExpense')
    assert np.isnan(parse_report(p, '005930', 2025, 2, 'CFS')['sga'])
    p['list'].append({**p['list'][0], 'account_id': 'ifrs-full_GeneralAndAdministrativeExpense', 'thstrm_add_amount': '20'})
    assert parse_report(p, '005930', 2025, 2, 'CFS')['sga'] == 50


def test_account_name_section_number_not_subcomponent():
    p = payload(account='ifrs-full_OperatingExpense')
    p['list'][0]['account_nm'] = 'Ⅳ. 판매비와관리비'
    assert parse_report(p, '005930', 2025, 2, 'CFS')['sga'] == 30
    p['list'][0]['account_nm'] = '기타판매비와관리비'
    assert np.isnan(parse_report(p, '005930', 2025, 2, 'CFS')['sga'])


def test_complete_case_control_preserves_original_order_even_exact_ties():
    ranked = pd.DataFrame({'comp': [80., 80., 79.]}, index=['c', 'b', 'a'])
    scores = pd.DataFrame({'fscore': [1., 4., 4.]}, index=ranked.index)
    assert rank_with_fscore(ranked, scores, reorder=False).index.tolist() == ['c', 'b', 'a']


def test_threshold_excludes_equal_and_missing_without_reordering_comp():
    ranked = pd.DataFrame({'comp': [85., 84.9, 84.1, 83., 82., 81.]}, index=list('abcdef'))
    scores = pd.DataFrame({'fscore': [np.nan, 2., 4., 2.4, 3.2, 0.]}, index=ranked.index)
    assert filter_by_fscore(ranked, scores, 0).index.tolist() == list('bcde')
    assert filter_by_fscore(ranked, scores, 2).index.tolist() == list('cde')
    assert filter_by_fscore(ranked, scores, 3).index.tolist() == list('ce')
    for value in (-1, 4, np.inf, np.nan):
        with pytest.raises(ValueError):
            filter_by_fscore(ranked, scores, value)
