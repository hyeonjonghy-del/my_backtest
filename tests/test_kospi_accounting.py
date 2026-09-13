import ast
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from core.kospi_accounting import backtest_ledger


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range("2024-01-01", periods=4)
        self.targets = pd.DataFrame(
            {"KODEX Leverage": 0.5, "KODEX 200": 0.0, "Cash": 0.5}, index=self.dates
        )
        self.zero = self.targets * 0

    def test_holdings_drift_and_overnight_loss(self):
        co, oc = self.zero.copy(), self.zero.copy()
        oc.iloc[1, 0] = 0.1
        co.iloc[2, 0] = -0.1
        nav, weights, log = backtest_ledger(self.dates, self.targets, co, oc, 0)
        self.assertAlmostEqual(nav.iloc[-1], 0.995)
        self.assertAlmostEqual(weights.iloc[1, 0], 55 / 105)
        self.assertEqual(len(log), 1)

    def test_partial_fill_carries_fixed_remaining_units_across_gap(self):
        co = self.zero.copy()
        co.iloc[1, 0] = 0.1
        nav, weights, log = backtest_ledger(
            self.dates, self.targets, co, self.zero, 0, "after_close", 0.7
        )
        self.assertAlmostEqual(nav.iloc[1], 1.035)
        self.assertAlmostEqual(weights.iloc[1, 0], 0.55 / 1.035)
        self.assertEqual(len(log), 2)
        self.assertAlmostEqual(log.iloc[1]["Turnover"], 0.165 / 1.035)

    def test_zero_fill_matches_next_open(self):
        co = self.zero.copy()
        co.iloc[1, 0] = 0.05
        first = backtest_ledger(self.dates, self.targets, co, self.zero, 0.001)
        second = backtest_ledger(
            self.dates, self.targets, co, self.zero, 0.001, "after_close", 0
        )
        pd.testing.assert_series_equal(first[0], second[0])
        pd.testing.assert_frame_equal(first[1], second[1])

    def test_full_fill_no_next_day_hidden_trade(self):
        co = self.zero.copy()
        co.iloc[1, 0] = 0.2
        nav, _, log = backtest_ledger(
            self.dates, self.targets, co, self.zero, 0, "after_close", 1
        )
        self.assertAlmostEqual(nav.iloc[-1], 1.1)
        self.assertEqual(len(log), 1)

    def test_fees_charged_on_actual_buy_and_sell(self):
        targets = self.targets.copy()
        targets.iloc[1:] = [0.0, 0.0, 1.0]
        nav, _, log = backtest_ledger(
            self.dates, targets, self.zero, self.zero, 0.01, "same_close"
        )
        buy = 0.5 / 1.005
        self.assertAlmostEqual(log.iloc[0]["Fee Cost"], buy * 0.01)
        self.assertAlmostEqual(log.iloc[1]["Fee Cost"], buy * 0.01)
        self.assertAlmostEqual(nav.iloc[-1], 1 - 2 * buy * 0.01)

    def test_gap_cash_cap_no_borrowing(self):
        targets = self.targets.copy()
        targets[:] = [1.0, 0.0, 0.0]
        co = self.zero.copy()
        co.iloc[1, 0] = 0.2
        nav, weights, log = backtest_ledger(self.dates, targets, co, self.zero, 0.01)
        self.assertTrue((weights["Cash"] >= 0).all())
        self.assertGreater(log.iloc[0]["Unfilled Value"], 0)
        self.assertAlmostEqual(nav.iloc[-1], 1 / 1.01)

    def test_future_returns_do_not_change_past(self):
        baseline = backtest_ledger(
            self.dates, self.targets, self.zero, self.zero, 0.001, "after_close", 0.7
        )
        oc = self.zero.copy()
        oc.iloc[-1, 0] = 2
        changed = backtest_ledger(
            self.dates, self.targets, self.zero, oc, 0.001, "after_close", 0.7
        )
        pd.testing.assert_series_equal(baseline[0].iloc[:-1], changed[0].iloc[:-1])

    def test_page_adapters_and_log_format(self):
        root = Path(__file__).resolve().parents[1]
        specs = [
            ("pages/3_KOSPI200_Bull_Bear.py", "backtest_portfolio_next_open"),
            ("pages/3_KOSPI200_Bull_Bear_v1(aggressive).py", "backtest_next_open"),
        ]
        results = []
        for file, name in specs:
            tree = ast.parse((root / file).read_text(encoding="utf-8-sig"))
            func = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            namespace = {"backtest_ledger": backtest_ledger}
            exec(compile(ast.Module(body=[func], type_ignores=[]), file, "exec"), namespace)
            results.append(namespace[name](self.dates, self.targets, self.zero, self.zero, 0.001))
        np.testing.assert_allclose(results[0][0], results[1][0])
        self.assertIsInstance(results[0][2].iloc[0]["Old Weight"], float)
        self.assertIn("Before Allocation", results[0][2])


if __name__ == "__main__":
    unittest.main()
