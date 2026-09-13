import unittest

import pandas as pd

from core.us_execution import whole_share_open_backtest


class WholeShareOpenBacktestTests(unittest.TestCase):
    def _frames(self, dates, prior, opens, closes):
        columns = ["QQQ", "TQQQ"]
        return (
            pd.DataFrame(prior, index=dates, columns=columns, dtype=float),
            pd.DataFrame(opens, index=dates, columns=columns, dtype=float),
            pd.DataFrame(closes, index=dates, columns=columns, dtype=float),
        )

    def test_split_changes_share_count_without_creating_return(self):
        dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
        targets = pd.DataFrame({"QQQ": [1.0, 1.0], "TQQQ": [0.0, 0.0]}, index=dates)
        prior, opens, closes = self._frames(
            dates,
            [[100, 10], [100, 10]],
            [[100, 10], [50, 10]],
            [[100, 10], [50, 10]],
        )
        splits = pd.DataFrame({"QQQ": [1.0, 2.0], "TQQQ": [1.0, 1.0]}, index=dates)
        dividends = pd.DataFrame(0.0, index=dates, columns=targets.columns)

        returns, _, _, shares, cash = whole_share_open_backtest(
            targets, prior, opens, closes, splits, dividends, 0.0, 1000.0
        )

        self.assertEqual(shares.loc[dates[0], "QQQ"], 10)
        self.assertEqual(shares.loc[dates[1], "QQQ"], 20)
        self.assertAlmostEqual(float(returns.sum()), 0.0)
        self.assertAlmostEqual(float(cash.iloc[-1]), 0.0)

    def test_order_is_sized_at_prior_close_then_capped_at_gap_open(self):
        dates = pd.to_datetime(["2024-01-02"])
        targets = pd.DataFrame({"QQQ": [1.0], "TQQQ": [0.0]}, index=dates)
        prior, opens, closes = self._frames(dates, [[100, 10]], [[120, 10]], [[120, 10]])
        neutral = pd.DataFrame({"QQQ": [1.0], "TQQQ": [1.0]}, index=dates)
        zero = pd.DataFrame(0.0, index=dates, columns=targets.columns)

        _, _, _, shares, cash = whole_share_open_backtest(
            targets, prior, opens, closes, neutral, zero, 0.0, 1000.0
        )

        self.assertEqual(shares.iloc[0]["QQQ"], 8)
        self.assertAlmostEqual(float(cash.iloc[0]), 40.0)

    def test_fee_budget_prevents_negative_cash(self):
        dates = pd.to_datetime(["2024-01-02"])
        targets = pd.DataFrame({"QQQ": [1.0], "TQQQ": [0.0]}, index=dates)
        prior, opens, closes = self._frames(dates, [[100, 10]], [[100, 10]], [[100, 10]])
        neutral = pd.DataFrame(1.0, index=dates, columns=targets.columns)
        zero = pd.DataFrame(0.0, index=dates, columns=targets.columns)

        _, _, _, shares, cash = whole_share_open_backtest(
            targets, prior, opens, closes, neutral, zero, 0.0025, 1000.0
        )

        self.assertEqual(shares.iloc[0]["QQQ"], 9)
        self.assertAlmostEqual(float(cash.iloc[0]), 97.75)

    def test_future_price_change_does_not_change_past_result(self):
        dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
        targets = pd.DataFrame({"QQQ": [0.5, 0.5], "TQQQ": [0.0, 0.0]}, index=dates)
        prior, opens, closes = self._frames(
            dates, [[100, 10], [100, 10]], [[100, 10], [100, 10]], [[100, 10], [100, 10]]
        )
        neutral = pd.DataFrame(1.0, index=dates, columns=targets.columns)
        zero = pd.DataFrame(0.0, index=dates, columns=targets.columns)
        baseline = whole_share_open_backtest(
            targets, prior, opens, closes, neutral, zero, 0.0, 1000.0
        )[0]
        opens.loc[dates[1], "QQQ"] = 999.0
        closes.loc[dates[1], "QQQ"] = 999.0
        changed = whole_share_open_backtest(
            targets, prior, opens, closes, neutral, zero, 0.0, 1000.0
        )[0]

        self.assertEqual(baseline.iloc[0], changed.iloc[0])

    def test_ex_date_dividend_belongs_to_prior_close_holder(self):
        dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
        targets = pd.DataFrame({"QQQ": [1.0, 0.0], "TQQQ": [0.0, 0.0]}, index=dates)
        prior, opens, closes = self._frames(
            dates, [[100, 10], [100, 10]], [[100, 10], [100, 10]], [[100, 10], [100, 10]]
        )
        neutral = pd.DataFrame(1.0, index=dates, columns=targets.columns)
        dividends = pd.DataFrame({"QQQ": [0.0, 1.0], "TQQQ": [0.0, 0.0]}, index=dates)

        _, _, _, shares, cash = whole_share_open_backtest(
            targets, prior, opens, closes, neutral, dividends, 0.0, 1000.0
        )

        self.assertEqual(shares.iloc[-1]["QQQ"], 0)
        self.assertAlmostEqual(float(cash.iloc[-1]), 1010.0)


if __name__ == "__main__":
    unittest.main()
