import unittest

from core.account_cash_flow import detect_external_cash_flow


class KiwoomAccountCashFlowTests(unittest.TestCase):
    symbols = ("QQQ", "TQQQ")

    def snapshot(self, cash, qqq=6, tqqq=24):
        return {"cash": cash, "shares": {"QQQ": qqq, "TQQQ": tqqq}}

    def test_detects_deposit_when_holdings_are_unchanged(self):
        cash_flow = detect_external_cash_flow(
            self.snapshot(537.77), self.snapshot(2537.77), self.symbols
        )
        self.assertAlmostEqual(cash_flow, 2000.0)

    def test_detects_withdrawal_when_holdings_are_unchanged(self):
        cash_flow = detect_external_cash_flow(
            self.snapshot(2537.77), self.snapshot(537.77), self.symbols
        )
        self.assertAlmostEqual(cash_flow, -2000.0)

    def test_share_change_is_not_misclassified_as_external_cash_flow(self):
        cash_flow = detect_external_cash_flow(
            self.snapshot(2537.77), self.snapshot(1089.87, qqq=8), self.symbols
        )
        self.assertEqual(cash_flow, 0.0)

    def test_first_snapshot_cannot_claim_a_cash_flow(self):
        self.assertEqual(
            detect_external_cash_flow(None, self.snapshot(2537.77), self.symbols),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
