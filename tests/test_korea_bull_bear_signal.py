import unittest
from datetime import datetime

import pandas as pd

from kiwoom_account import build_holdings_trade_plan
from scripts.send_korea_bull_bear_signal import (
    KODEX_200,
    KODEX_LEVERAGE,
    format_kodex_execution,
)


class KoreaBullBearSignalTests(unittest.TestCase):
    def test_streamlit_plan_suppresses_orders_when_allocation_is_unchanged(self):
        weights = pd.Series(
            {"KODEX Leverage": 0.0, "KODEX 200": 0.0, "Cash": 1.0}
        )

        plan, summary = build_holdings_trade_plan(
            weights,
            {"KODEX Leverage": 111_680.0, "KODEX 200": 110_710.0},
            {"KODEX Leverage": 0, "KODEX 200": 200},
            55_548_686.0,
            77_684_043.0,
            0.0003,
            previous_target_weights=weights.copy(),
        )

        self.assertFalse(summary["allocation_changed"])
        self.assertEqual(summary["total_order_value"], 0.0)
        self.assertTrue((plan["Order Shares"] == 0).all())
        self.assertTrue((plan["Order"] == "Hold").all())

    def test_streamlit_plan_keeps_orders_when_allocation_changes(self):
        target = pd.Series(
            {"KODEX Leverage": 0.0, "KODEX 200": 0.0, "Cash": 1.0}
        )
        previous = pd.Series(
            {"KODEX Leverage": 0.0, "KODEX 200": 1.0, "Cash": 0.0}
        )

        plan, summary = build_holdings_trade_plan(
            target,
            {"KODEX Leverage": 111_680.0, "KODEX 200": 110_710.0},
            {"KODEX Leverage": 0, "KODEX 200": 200},
            55_548_686.0,
            77_684_043.0,
            0.0003,
            previous_target_weights=previous,
        )

        kodex_order = plan.loc[plan["Symbol"] == "KODEX 200"].iloc[0]
        self.assertTrue(summary["allocation_changed"])
        self.assertEqual(kodex_order["Order Shares"], -200)
        self.assertEqual(kodex_order["Order"], "Sell")

    def test_unchanged_allocation_shows_only_no_order_message(self):
        weights = pd.Series(
            {"KODEX Leverage": 0.0, "KODEX 200": 0.0, "Cash": 1.0}
        )

        message = format_kodex_execution(
            title="KODEX 기본 전략",
            now=datetime(2026, 10, 6, 15, 35),
            latest_date="2026-10-06",
            target_weights=weights,
            previous_target_weights=weights.copy(),
            current={KODEX_LEVERAGE: 0, KODEX_200: 200},
            cash=55_548_686.0,
            prices={KODEX_LEVERAGE: 111_680.0, KODEX_200: 110_710.0},
            profile_label="국내 KOSPI 계좌",
            signal_lines=["Leverage Signal: Wait"],
        )

        self.assertEqual(
            message,
            "\n".join(
                [
                    "[KODEX 기본 전략]",
                    "실행시각: 2026-10-06 15:35 KST",
                    "기준일: 2026-10-06",
                    "변동 없음 (주문 없음)",
                ]
            ),
        )
        self.assertNotIn("매도", message)
        self.assertNotIn("EXECUTION", message)

    def test_changed_allocation_still_shows_execution_plan(self):
        target = pd.Series(
            {"KODEX Leverage": 0.0, "KODEX 200": 0.0, "Cash": 1.0}
        )
        previous = pd.Series(
            {"KODEX Leverage": 0.0, "KODEX 200": 1.0, "Cash": 0.0}
        )

        message = format_kodex_execution(
            title="KODEX 기본 전략",
            now=datetime(2026, 10, 6, 15, 35),
            latest_date="2026-10-06",
            target_weights=target,
            previous_target_weights=previous,
            current={KODEX_LEVERAGE: 0, KODEX_200: 200},
            cash=55_548_686.0,
            prices={KODEX_LEVERAGE: 111_680.0, KODEX_200: 110_710.0},
            profile_label="국내 KOSPI 계좌",
            signal_lines=["Leverage Signal: Wait"],
        )

        self.assertIn("상태: 전략 비중 변경", message)
        self.assertIn("KODEX 200: 매도 200주", message)
        self.assertIn("EXECUTION 1", message)


if __name__ == "__main__":
    unittest.main()
