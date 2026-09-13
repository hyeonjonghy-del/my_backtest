import unittest

import pandas as pd

from pykrx.stock import _normalize_ohlcv


class PykrxShimTests(unittest.TestCase):
    def test_raw_close_is_preferred_when_adjusted_close_is_also_present(self):
        raw = pd.DataFrame(
            {"Open": [100.0], "High": [112.0], "Low": [98.0], "Close": [110.0],
             "Adj Close": [107.0], "Volume": [1234]},
            index=pd.to_datetime(["2025-01-02"]),
        )
        result = _normalize_ohlcv(raw)
        self.assertEqual(float(result.loc[pd.Timestamp("2025-01-02"), "종가"]), 110.0)
        self.assertFalse(result.columns.duplicated().any())

    def test_adjusted_close_is_fallback_when_raw_close_is_absent(self):
        raw = pd.DataFrame({"Open": [100.0], "Adj Close": [107.0]},
                           index=pd.to_datetime(["2025-01-02"]))
        result = _normalize_ohlcv(raw)
        self.assertEqual(float(result.iloc[0]["종가"]), 107.0)


if __name__ == "__main__":
    unittest.main()
