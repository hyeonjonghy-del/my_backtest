from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies.samsung_electronics_trend_vol.strategy import StrategyConfig, build_signals, run_backtest


class SamsungStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = StrategyConfig(
            long_ma_window=5,
            momentum_window=3,
            volatility_window=3,
            volatility_cap=1.5,
            target_volatility=0.3,
            min_invested_weight=0.25,
            max_invested_weight=1.0,
            fee_rate=0.001,
        )

    def test_rising_market_turns_risk_on_with_bounded_weight(self) -> None:
        index = pd.date_range("2025-01-01", periods=12, freq="B")
        close = pd.Series(np.linspace(100, 125, len(index)), index=index)
        signals = build_signals(close, self.config)

        self.assertTrue(bool(signals["risk_on"].iloc[-1]))
        self.assertGreaterEqual(signals["target_weight"].iloc[-1], 0.25)
        self.assertLessEqual(signals["target_weight"].iloc[-1], 1.0)

    def test_falling_market_moves_to_cash(self) -> None:
        index = pd.date_range("2025-01-01", periods=12, freq="B")
        close = pd.Series(np.linspace(125, 100, len(index)), index=index)
        signals = build_signals(close, self.config)

        self.assertFalse(bool(signals["risk_on"].iloc[-1]))
        self.assertEqual(float(signals["target_weight"].iloc[-1]), 0.0)

    def test_high_volatility_bull_uses_reduced_fallback_weight(self) -> None:
        index = pd.date_range("2025-01-01", periods=10, freq="B")
        close = pd.Series([100, 110, 105, 120, 115, 130, 125, 140, 135, 150], index=index)
        config = StrategyConfig(
            long_ma_window=5,
            momentum_window=3,
            volatility_window=3,
            volatility_cap=0.05,
            target_volatility=0.03,
            high_volatility_weight=0.35,
        )
        signals = build_signals(close, config)

        self.assertTrue(bool(signals["trend_ok"].iloc[-1]))
        self.assertFalse(bool(signals["volatility_ok"].iloc[-1]))
        self.assertAlmostEqual(float(signals["target_weight"].iloc[-1]), 0.35)

    def test_signal_is_executed_at_next_open(self) -> None:
        index = pd.date_range("2025-01-01", periods=12, freq="B")
        close = pd.Series(np.linspace(100, 125, len(index)), index=index)
        ohlcv = pd.DataFrame({"open": close * 0.995, "close": close}, index=index)
        result = run_backtest(ohlcv, self.config)

        expected = result["target_weight"].shift(1).fillna(0.0)
        pd.testing.assert_series_equal(result["executed_weight"], expected, check_names=False)
        self.assertTrue((result["strategy_nav"] > 0).all())

    def test_full_cash_days_have_zero_strategy_return(self) -> None:
        index = pd.date_range("2025-01-01", periods=12, freq="B")
        close = pd.Series(np.linspace(125, 100, len(index)), index=index)
        ohlcv = pd.DataFrame({"open": close * 1.005, "close": close}, index=index)
        result = run_backtest(ohlcv, self.config)

        cash_days = result.loc[result["cash_all_day"]]
        self.assertTrue((cash_days["strategy_return"].abs() < 1e-12).all())


if __name__ == "__main__":
    unittest.main()
