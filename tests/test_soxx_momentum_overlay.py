import numpy as np
import pandas as pd
import unittest
from core.soxx_momentum_overlay import apply_mom10_overlay, limit_target_risk


def test_negative_return_caps_risk_and_scales_both_assets():
    idx = pd.date_range('2020-01-01', periods=25)
    price = pd.Series(np.arange(100., 75., -1), index=idx)
    target = pd.DataFrame({'SOXX': .2, 'SOXL': .1}, index=idx)
    target.iloc[12:] = [.4, .2]
    result = apply_mom10_overlay(target, price)
    np.testing.assert_allclose(result.iloc[12:], np.tile([.2, .1], (13, 1)))
    assert (result.sum(axis=1) <= 1).all()


def test_lag_recovery_reduction_and_prefix_stability():
    idx = pd.date_range('2020-01-01', periods=40)
    price = pd.Series(np.r_[np.arange(100., 80., -1), np.arange(100., 120.)], index=idx)
    target = pd.DataFrame({'SOXX': .2, 'SOXL': .1}, index=idx)
    target.iloc[10:] = [.4, .2]
    target.iloc[14:17] = [.1, .05]
    result = apply_mom10_overlay(target, price)
    # The first negative ten-session return is known only after session 10 closes.
    np.testing.assert_allclose(result.iloc[10], target.iloc[10])
    np.testing.assert_allclose(result.iloc[14:17], target.iloc[14:17])
    np.testing.assert_allclose(result.iloc[17], [.1, .05])
    np.testing.assert_allclose(result.iloc[-1], target.iloc[-1])
    pd.testing.assert_frame_equal(apply_mom10_overlay(target.iloc[:25], price.iloc[:25]), result.iloc[:25])


def test_next_open_matches_history_and_turnaround_is_capped():
    idx = pd.date_range('2020-01-01', periods=30)
    price = pd.Series(np.arange(100., 70., -1), index=idx)
    target = pd.DataFrame({'SOXX': .3, 'SOXL': .1}, index=idx)
    target.iloc[15:] = [.5, .5]
    result = apply_mom10_overlay(target, price)
    for n in range(1, len(idx)):
        down = bool(price.pct_change(10).iloc[n-1] < 0)
        expected = limit_target_risk(target.iloc[n], result.iloc[n-1], down)
        np.testing.assert_allclose(result.iloc[n], expected)


class MomentumOverlayTests(unittest.TestCase):
    test_scaling = staticmethod(test_negative_return_caps_risk_and_scales_both_assets)
    test_lag_and_recovery = staticmethod(test_lag_recovery_reduction_and_prefix_stability)
    test_live_history_parity = staticmethod(test_next_open_matches_history_and_turnaround_is_capped)


if __name__ == '__main__':
    unittest.main()
