import unittest

import pandas as pd

from kiwoom_account import build_holdings_trade_plan


class KospiExecutionPlanTests(unittest.TestCase):
    def test_full_allocation_reserves_transaction_fee(self):
        plan, summary = build_holdings_trade_plan(
            pd.Series({"KODEX Leverage": 1.0, "Cash": 0.0}),
            {"KODEX Leverage": 10_000.0}, {"KODEX Leverage": 0.0},
            current_cash=1_000_000.0, account_value=1_000_000.0, fee_rate=0.001,
        )
        shares = int(plan.iloc[0]["Target Shares"])
        self.assertLessEqual(shares * 10_000.0 * 1.001, 1_000_000.0)
        self.assertEqual(shares, 99)
        self.assertGreaterEqual(summary["target_cash"], 0.0)

    def test_default_zero_fee_preserves_prior_sizing(self):
        plan, _ = build_holdings_trade_plan(
            {"Asset": 1.0}, {"Asset": 10.0}, {"Asset": 0.0}, 100.0, 100.0
        )
        self.assertEqual(int(plan.iloc[0]["Target Shares"]), 10)


if __name__ == "__main__":
    unittest.main()
