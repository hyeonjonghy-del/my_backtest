import unittest

import pandas as pd

from core.execution_alerts import whole_share_plan
from core.us_execution import (
    fixed_units_open_backtest,
    repair_latest_yahoo_close,
    rebalance_due_after_close,
    validated_common_dates,
)


class UsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.to_datetime(["2026-09-10", "2026-09-11"])

    def test_full_switch_charges_sell_and_buy_notional(self):
        targets = pd.DataFrame([[1.0, 0.0], [0.0, 1.0]], index=self.dates, columns=["SOXX", "BIL"])
        prices = pd.DataFrame(100.0, index=self.dates, columns=targets.columns)
        daily, turnover, nav = fixed_units_open_backtest(
            targets, prices, prices, prices, 0.001, rebalance_every_session=True
        )
        self.assertGreater(turnover.iloc[1], 1.99)
        self.assertAlmostEqual(nav.iloc[1] / nav.iloc[0] - 1, -0.002, places=5)

    def test_fixed_prior_close_units_are_not_resized_using_gap_open(self):
        targets = pd.DataFrame([[0.5], [0.5]], index=self.dates, columns=["SOXX"])
        prior = pd.DataFrame([[100.0], [120.0]], index=self.dates, columns=["SOXX"])
        opened = pd.DataFrame([[120.0], [120.0]], index=self.dates, columns=["SOXX"])
        closed = pd.DataFrame([[120.0], [144.0]], index=self.dates, columns=["SOXX"])
        _, _, nav = fixed_units_open_backtest(targets, prior, opened, closed, 0.0)
        self.assertAlmostEqual(nav.iloc[-1], 1.12)

    def test_missing_symbol_date_is_rejected(self):
        left = pd.DataFrame(
            {"open": [1, 1], "close": [1, 1], "adjclose": [1, 1]}, index=self.dates
        )
        right = left.iloc[:1]
        with self.assertRaisesRegex(ValueError, "missing trading dates"):
            validated_common_dates({"SOXX": left, "SOXL": right}, self.dates[0], self.dates[-1])

    def test_nyse_calendar_drives_weekly_and_monthly_rebalance(self):
        self.assertTrue(rebalance_due_after_close("2026-09-11", "Weekly"))
        self.assertFalse(rebalance_due_after_close("2026-09-10", "Weekly"))
        self.assertTrue(rebalance_due_after_close("2026-09-30", "Monthly"))

    def test_whole_share_plan_auto_invests_new_cash_without_weight_change(self):
        plan = whole_share_plan(
            {"QQQ": 0.733, "TQQQ": 0.267},
            {"QQQ": 721.45, "TQQQ": 72.64},
            {"QQQ": 6, "TQQQ": 24},
            2_537.77,
            previous_weights={"QQQ": 0.733, "TQQQ": 0.267},
            fee_rate=0.0025,
        )
        self.assertFalse(plan["target_changed"])
        self.assertTrue(plan["cash_deposit_detected"])
        self.assertTrue(plan["execution_required"])
        self.assertGreater(plan["orders"]["QQQ"]["order"], 0)
        self.assertGreater(plan["orders"]["TQQQ"]["order"], 0)

    def test_whole_share_plan_does_not_rebalance_price_drift_that_needs_sale(self):
        plan = whole_share_plan(
            {"QQQ": 0.5, "TQQQ": 0.5},
            {"QQQ": 100.0, "TQQQ": 100.0},
            {"QQQ": 6, "TQQQ": 4},
            0.0,
            previous_weights={"QQQ": 0.5, "TQQQ": 0.5},
        )
        self.assertFalse(plan["cash_deposit_detected"])
        self.assertFalse(plan["execution_required"])
        self.assertEqual(plan["orders"]["QQQ"]["order"], 0)
        self.assertEqual(plan["orders"]["TQQQ"]["order"], 0)

    def test_recovers_only_completed_missing_close_with_matching_final_metadata(self):
        dates = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"])
        frame = pd.DataFrame(
            {
                "open": [727.89, 741.00, 748.00],
                "high": [743.22, 748.35, 749.00],
                "low": [727.81, 741.00, 747.00],
                "close": [741.47, None, None],
                "adjclose": [741.47, None, None],
                "volume": [46_730_000, 39_451_977, 10_000],
                "split_ratio": [1.0, 1.0, 1.0],
                "dividend": [0.0, 0.0, 0.0],
                "close_recovered": [False, False, False],
            },
            index=dates,
        )
        metadata = {
            "regularMarketPrice": 747.46,
            "regularMarketTime": int(pd.Timestamp("2026-09-22 20:00:00Z").timestamp()),
        }
        now = pd.Timestamp("2026-09-23 10:00:00Z")
        result = repair_latest_yahoo_close(frame, metadata, dates[1], now)
        self.assertEqual(result.loc[dates[1], "close"], 747.46)
        self.assertEqual(result.loc[dates[1], "adjclose"], 747.46)
        self.assertTrue(result.loc[dates[1], "close_recovered"])
        self.assertTrue(pd.isna(result.loc[dates[2], "close"]))

        stale = dict(metadata, regularMarketTime=int(pd.Timestamp("2026-09-21 20:00:00Z").timestamp()))
        self.assertTrue(pd.isna(repair_latest_yahoo_close(frame, stale, dates[1], now).loc[dates[1], "close"]))
        intraday = dict(metadata, regularMarketTime=int(pd.Timestamp("2026-09-22 19:00:00Z").timestamp()))
        self.assertTrue(pd.isna(repair_latest_yahoo_close(frame, intraday, dates[1], now).loc[dates[1], "close"]))
        split_day = frame.copy()
        split_day.loc[dates[1], "split_ratio"] = 2.0
        self.assertTrue(pd.isna(repair_latest_yahoo_close(split_day, metadata, dates[1], now).loc[dates[1], "close"]))

    def test_recovers_soxl_dividend_ex_date_without_losing_adjustment(self):
        dates = pd.to_datetime(["2026-09-21", "2026-09-22"])
        frame = pd.DataFrame(
            {
                "open": [132.07, 137.67],
                "high": [144.00, 152.6394],
                "low": [129.62, 137.46],
                "close": [141.93, None],
                "adjclose": [141.83699, None],
                "volume": [62_514_300, 65_009_024],
                "split_ratio": [1.0, 1.0],
                "dividend": [0.0, 0.093],
                "close_recovered": [False, False],
            },
            index=dates,
        )
        metadata = {
            "regularMarketPrice": 151.95,
            "regularMarketTime": int(pd.Timestamp("2026-09-22 20:00:00Z").timestamp()),
        }
        result = repair_latest_yahoo_close(frame, metadata, dates[1], pd.Timestamp("2026-09-23 10:00:00Z"))
        self.assertEqual(result.loc[dates[1], "close"], 151.95)
        self.assertEqual(result.loc[dates[1], "adjclose"], 151.95)
        self.assertEqual(result.loc[dates[1], "dividend"], 0.093)
        self.assertTrue(result.loc[dates[1], "close_recovered"])

        invalid_dividend = frame.copy()
        invalid_dividend.loc[dates[1], "dividend"] = 200.0
        rejected = repair_latest_yahoo_close(
            invalid_dividend, metadata, dates[1], pd.Timestamp("2026-09-23 10:00:00Z")
        )
        self.assertTrue(pd.isna(rejected.loc[dates[1], "close"]))


if __name__ == "__main__":
    unittest.main()
