import unittest

import pandas as pd

from core.kospi_accounting import validated_common_dates


def prices(dates, opens=None, closes=None):
    count = len(dates)
    return pd.DataFrame({"open": opens if opens is not None else [100.0] * count,
                         "close": closes if closes is not None else [101.0] * count},
                        index=pd.to_datetime(dates))


class KospiDataValidationTests(unittest.TestCase):
    def test_accepts_matching_positive_execution_prices(self):
        frame = prices(["2025-01-02", "2025-01-03"])
        dates = validated_common_dates(frame, frame.copy(), "2025-01-02", "2025-01-03")
        self.assertEqual(list(dates), list(frame.index))

    def test_rejects_one_sided_trading_date(self):
        left = prices(["2025-01-02", "2025-01-03"])
        right = prices(["2025-01-02"])
        with self.assertRaisesRegex(ValueError, "KODEX Leverage missing 2025-01-03"):
            validated_common_dates(left, right, "2025-01-02", "2025-01-03")

    def test_rejects_missing_or_nonpositive_open_close(self):
        left = prices(["2025-01-02"], opens=[0.0])
        right = prices(["2025-01-02"])
        with self.assertRaisesRegex(ValueError, "missing/invalid executable prices"):
            validated_common_dates(left, right, "2025-01-02", "2025-01-02")


if __name__ == "__main__":
    unittest.main()
