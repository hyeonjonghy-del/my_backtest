from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies.samsung_electronics_trend_vol.strategy import StrategyConfig, build_signals, run_backtest


class SamsungStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = StrategyConfig(
            long_ma_window=6,
            fast_ma_window=3,
            fast_ma_slope_window=2,
            momentum_window=3,
            recent_range_window=3,
            strong_momentum_threshold=0.02,
            strong_volatility_cap=2.0,
            leverage_weight=0.25,
            early_reentry_weight=0.65,
            crash_drawdown_threshold=0.40,
            crash_volatility_threshold=2.5,
            fee_rate=0.001,
        )

    def test_strong_bull_uses_25_percent_leverage_sleeve(self) -> None:
        index = pd.date_range("2025-01-01", periods=15, freq="B")
        close = pd.Series(np.linspace(100, 130, len(index)), index=index)
        signals = build_signals(close, self.config)

        self.assertTrue(bool(signals["strong_bull"].iloc[-1]))
        self.assertAlmostEqual(float(signals["target_samsung_weight"].iloc[-1]), 0.75)
        self.assertAlmostEqual(float(signals["target_leverage_weight"].iloc[-1]), 0.25)
        self.assertAlmostEqual(float(signals["target_effective_exposure"].iloc[-1]), 1.25)

    def test_falling_market_moves_to_cash(self) -> None:
        index = pd.date_range("2025-01-01", periods=15, freq="B")
        close = pd.Series(np.linspace(130, 100, len(index)), index=index)
        signals = build_signals(close, self.config)

        self.assertEqual(float(signals["target_samsung_weight"].iloc[-1]), 0.0)
        self.assertEqual(float(signals["target_leverage_weight"].iloc[-1]), 0.0)

    def test_crash_protection_overrides_bull_regime(self) -> None:
        index = pd.date_range("2025-01-01", periods=15, freq="B")
        close = pd.Series([100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124, 126, 75], index=index)
        signals = build_signals(close, self.config)

        self.assertTrue(bool(signals["crash"].iloc[-1]))
        self.assertEqual(float(signals["target_effective_exposure"].iloc[-1]), 0.0)

    def test_signals_are_executed_at_next_open(self) -> None:
        index = pd.date_range("2025-01-01", periods=15, freq="B")
        close = pd.Series(np.linspace(100, 130, len(index)), index=index)
        ohlcv = pd.DataFrame({"open": close * 0.995, "close": close}, index=index)
        result = run_backtest(ohlcv, self.config)

        pd.testing.assert_series_equal(
            result["executed_samsung_weight"],
            result["target_samsung_weight"].shift(1).fillna(0.0),
            check_names=False,
        )
        pd.testing.assert_series_equal(
            result["executed_leverage_weight"],
            result["target_leverage_weight"].shift(1).fillna(0.0),
            check_names=False,
        )

    def test_full_cash_days_have_zero_strategy_return(self) -> None:
        index = pd.date_range("2025-01-01", periods=15, freq="B")
        close = pd.Series(np.linspace(130, 100, len(index)), index=index)
        ohlcv = pd.DataFrame({"open": close * 1.005, "close": close}, index=index)
        result = run_backtest(ohlcv, self.config)

        cash_days = result.loc[result["cash_all_day"]]
        self.assertTrue((cash_days["strategy_return"].abs() < 1e-12).all())

    def test_actual_leverage_returns_are_identified(self) -> None:
        index = pd.date_range("2025-01-01", periods=15, freq="B")
        close = pd.Series(np.linspace(100, 130, len(index)), index=index)
        samsung = pd.DataFrame({"open": close * 0.995, "close": close}, index=index)
        leverage = pd.DataFrame({"open": close * 1.01, "close": close * 1.02}, index=index)
        result = run_backtest(samsung, self.config, leverage)

        self.assertEqual(result["leverage_return_source"].iloc[0], "Synthetic 2x")
        self.assertTrue(result["leverage_return_source"].iloc[1:].eq("Actual ETF").all())


if __name__ == "__main__":
    unittest.main()
