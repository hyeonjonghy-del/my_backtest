import ast
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

from core.us_cash_flow import confirmation_key
from core.whole_share_optimizer import optimize_whole_share_targets
from core.execution_alerts import whole_share_plan

ROOT = Path(__file__).resolve().parents[1]


def load_plan(page):
    nodes = [n for n in ast.parse((ROOT/'pages'/page).read_text(encoding='utf-8')).body
             if isinstance(n, ast.FunctionDef) and n.name == 'build_execution_plan']
    scope = {'np':np, 'pd':pd, 'optimize_whole_share_targets':optimize_whole_share_targets}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), page, 'exec'), scope)
    return scope['build_execution_plan']


class CashFlowTests(unittest.TestCase):
    def test_confirmed_deposit_allows_mixed_orders_in_both_pages_and_modes(self):
        for page, symbols in [('5_SOXX_SOXL_vol.py',['SOXX','SOXL']),
                              ('6_QQQ_TQQQ_holdings.py',['QQQ','TQQQ'])]:
            plan = load_plan(page)
            for target in ([.2376702664220905,.3168936885627874], [.28,.37]):
                with self.subTest(page=page, target=target):
                    weights = pd.Series(target, index=symbols)
                    prices = pd.Series([510.36,103.24], index=symbols)
                    held = pd.Series([11,67], index=symbols)
                    nav = 9622.37 + (held*prices).sum()
                    off, _ = plan(weights, prices, nav, held, 9622.37,
                                  previous_target_weights=weights, cost_rate=.0025)
                    self.assertTrue((off['Order Shares']==0).all())
                    on, cash = plan(weights, prices, nav, held, 9622.37,
                                    previous_target_weights=weights, cost_rate=.0025,
                                    cash_flow_confirmed=True)
                    expected = optimize_whole_share_targets(weights, prices, held, 9622.37, .0025)
                    np.testing.assert_array_equal(on['Target Shares'], expected)
                    fees = on['Estimated Order Value'].sum()*.0025
                    self.assertAlmostEqual(cash+(on['Target Shares'].to_numpy()*prices.to_numpy()).sum()+fees, nav)
                    if target[0] < .24:
                        np.testing.assert_array_equal(on['Order Shares'], [-1,2])
                    alert = whole_share_plan(weights.to_dict(), prices.to_dict(), held.to_dict(),
                                             9622.37, previous_weights=weights.to_dict(),
                                             fee_rate=.0025, cash_flow_confirmed=True)
                    np.testing.assert_array_equal([alert['orders'][s]['order'] for s in symbols], on['Order Shares'])

    def test_buy_only_drift_does_not_prove_a_deposit(self):
        for symbols in (['SOXX','SOXL'], ['QQQ','TQQQ']):
            weights = dict(zip(symbols,[.3,.3]))
            prices = dict(zip(symbols,[100.,100.]))
            shares = dict(zip(symbols,[1,1]))
            plan = whole_share_plan(weights, prices, shares, 1000., previous_weights=weights)
            self.assertFalse(plan['cash_deposit_detected'])
            self.assertFalse(plan['execution_required'])
            self.assertTrue(all(v['order']==0 for v in plan['orders'].values()))

    def test_target_changes_still_execute_without_cash_confirmation(self):
        plan = whole_share_plan({'QQQ':.4,'TQQQ':.3}, {'QQQ':100.,'TQQQ':100.},
                               {'QQQ':1,'TQQQ':1}, 1000., previous_weights={'QQQ':.3,'TQQQ':.3})
        self.assertTrue(plan['target_changed'])
        self.assertTrue(plan['execution_required'])

    def test_confirmation_expires_for_new_snapshot_or_account_or_holdings(self):
        account = {'source':'Kiwoom','profile_name':'one','snapshot':{'fetched_at':'A'},
                   'shares':{'SOXX':11,'SOXL':67},'cash':9622.37}
        key = confirmation_key(account, 'soxx_soxl')
        self.assertEqual(key, confirmation_key(account.copy(), 'soxx_soxl'))
        for update in ({'snapshot':{'fetched_at':'B'}}, {'profile_name':'two'},
                       {'shares':{'SOXX':10,'SOXL':69}}, {'cash':9000.}):
            self.assertNotEqual(key, confirmation_key(dict(account,**update), 'soxx_soxl'))


if __name__ == '__main__':
    unittest.main()
