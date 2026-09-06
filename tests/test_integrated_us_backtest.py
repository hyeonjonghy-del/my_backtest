from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from integrated_us_backtest import growth_sleeve_mix


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


if __name__ == "__main__":
    unittest.main()
