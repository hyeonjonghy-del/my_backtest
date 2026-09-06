from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from integrated_us_backtest import growth_sleeve_mix, make_sgov_bil_proxy, trim_and_rebase


class GrowthSleeveMixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index = pd.date_range("2020-01-31", periods=16, freq="ME")
        self.sleeve_nav = pd.DataFrame(
            {
                "SOXX/SOXL": np.linspace(100.0, 160.0, len(self.index)),
                "QQQ/TQQQ": np.linspace(100.0, 115.0, len(self.index)),
            },
            index=self.index,
        )

    def test_fixed_mode_stays_equal_weight(self) -> None:
        mix = growth_sleeve_mix(self.sleeve_nav, "fixed_50_50")
        self.assertTrue((mix == 0.50).all().all())

    def test_momentum_mode_overweights_stronger_sleeve_after_lag(self) -> None:
        mix = growth_sleeve_mix(self.sleeve_nav, "momentum_70_30")
        self.assertEqual(float(mix.iloc[12]["SOXX/SOXL"]), 0.50)
        self.assertEqual(float(mix.iloc[13]["SOXX/SOXL"]), 0.70)
        self.assertEqual(float(mix.iloc[13]["QQQ/TQQQ"]), 0.30)

    def test_exact_tie_remains_equal_weight(self) -> None:
        tied = pd.DataFrame(
            {"SOXX/SOXL": np.arange(100.0, 116.0), "QQQ/TQQQ": np.arange(100.0, 116.0)},
            index=self.index,
        )
        mix = growth_sleeve_mix(tied, "momentum_70_30")
        self.assertTrue((mix == 0.50).all().all())

    def test_momentum_mode_can_overweight_qqq_sleeve(self) -> None:
        reversed_nav = self.sleeve_nav.rename(
            columns={"SOXX/SOXL": "QQQ/TQQQ", "QQQ/TQQQ": "SOXX/SOXL"}
        )
        mix = growth_sleeve_mix(reversed_nav, "momentum_70_30")
        self.assertEqual(float(mix.iloc[13]["SOXX/SOXL"]), 0.30)
        self.assertEqual(float(mix.iloc[13]["QQQ/TQQQ"]), 0.70)

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            growth_sleeve_mix(self.sleeve_nav, "unknown")

    def test_bil_is_used_before_sgov_and_sgov_after_launch(self) -> None:
        index = pd.date_range("2020-05-25", periods=5, freq="B")
        raw = pd.DataFrame(
            {
                "QQQ": [100, 101, 102, 103, 104],
                "GLD": [100, 101, 102, 103, 104],
                "SOXX": [100, 101, 102, 103, 104],
                "SOXL": [100, 101, 102, 103, 104],
                "TQQQ": [100, 101, 102, 103, 104],
                "BIL": [100, 101, 102, 103, 104],
                "SGOV": [np.nan, np.nan, 200, 204, 210],
            },
            index=index,
        )
        result = make_sgov_bil_proxy(raw)
        self.assertAlmostEqual(result["SGOV"].iloc[1] / result["SGOV"].iloc[0] - 1, 0.01)
        self.assertAlmostEqual(result["SGOV"].iloc[2] / result["SGOV"].iloc[1] - 1, 102 / 101 - 1)
        self.assertAlmostEqual(result["SGOV"].iloc[3] / result["SGOV"].iloc[2] - 1, 0.02)

    def test_selected_start_changes_evaluation_period_and_rebases_nav(self) -> None:
        index = pd.date_range("2020-01-01", periods=5, freq="YS")
        nav = pd.DataFrame({"strategy": [1.0, 1.1, 1.3, 1.6, 2.0]}, index=index)
        early = trim_and_rebase(nav, "2021-01-01")
        late = trim_and_rebase(nav, "2023-01-01")
        self.assertEqual(early.index[0], pd.Timestamp("2021-01-01"))
        self.assertEqual(late.index[0], pd.Timestamp("2023-01-01"))
        self.assertEqual(float(early.iloc[0, 0]), 1.0)
        self.assertEqual(float(late.iloc[0, 0]), 1.0)
        self.assertNotEqual(float(early.iloc[-1, 0]), float(late.iloc[-1, 0]))


if __name__ == "__main__":
    unittest.main()
