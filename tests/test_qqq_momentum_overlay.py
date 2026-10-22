import unittest
import numpy as np
import pandas as pd
from core.qqq_momentum_overlay import apply_mom10_overlay, limit_target_risk
from core.soxx_momentum_overlay import apply_mom10_overlay as soxx_overlay


class QqqMomentumOverlayTests(unittest.TestCase):
    def setUp(self):
        self.idx = pd.date_range('2020-01-01', periods=40)
        self.price = pd.Series(np.r_[np.arange(100., 80., -1), np.arange(100., 120.)], index=self.idx)
        self.targets = pd.DataFrame({'QQQ': .3, 'TQQQ': .1}, index=self.idx)
        self.targets.iloc[12:17] = [.5, .5]  # Turnaround must also be limited.
        self.targets.iloc[17:20] = [.1, .0]

    def test_matches_soxx_rule_and_retains_columns(self):
        result = apply_mom10_overlay(self.targets, self.price)
        expected = soxx_overlay(self.targets.rename(columns={'QQQ':'SOXX','TQQQ':'SOXL'}), self.price)
        pd.testing.assert_frame_equal(result, expected.rename(columns={'SOXX':'QQQ','SOXL':'TQQQ'}))
        self.assertEqual(list(result.columns), ['QQQ','TQQQ'])
        np.testing.assert_allclose(result.iloc[12], [.15, .15])
        np.testing.assert_allclose(result.iloc[-1], self.targets.iloc[-1])
        self.assertTrue((result.sum(axis=1) <= 1).all())

    def test_next_open_parity_and_no_lookahead(self):
        result = apply_mom10_overlay(self.targets, self.price)
        for n in range(1, len(result)):
            expected = limit_target_risk(self.targets.iloc[n], result.iloc[n-1], self.price.pct_change(10).iloc[n-1] < 0)
            np.testing.assert_allclose(result.iloc[n], expected)
        pd.testing.assert_frame_equal(apply_mom10_overlay(self.targets.iloc[:25], self.price.iloc[:25]), result.iloc[:25])

    def test_no_mutation_and_risk_reduction_allowed(self):
        original = self.targets.copy()
        result = apply_mom10_overlay(self.targets, self.price)
        pd.testing.assert_frame_equal(original, self.targets)
        np.testing.assert_allclose(result.iloc[17:20], original.iloc[17:20])


if __name__ == '__main__':
    unittest.main()
