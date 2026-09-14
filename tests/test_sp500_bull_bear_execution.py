from __future__ import annotations

import numpy as np
import pandas as pd

from core.us_execution import whole_share_open_backtest


def test_constant_weekly_or_monthly_target_is_not_rebalanced_for_price_drift() -> None:
    dates = pd.bdate_range("2024-01-02", periods=4)
    targets = pd.DataFrame({"SPY": 0.60, "UPRO": 0.40}, index=dates)
    prior = pd.DataFrame({"SPY": [100, 100, 110, 120], "UPRO": [50, 50, 45, 40]}, index=dates)
    opens = pd.DataFrame({"SPY": [100, 110, 120, 130], "UPRO": [50, 45, 40, 35]}, index=dates)
    closes = opens.copy()
    splits = pd.DataFrame(1.0, index=dates, columns=["SPY", "UPRO"])
    dividends = pd.DataFrame(0.0, index=dates, columns=["SPY", "UPRO"])

    _, _, turnover, shares, _ = whole_share_open_backtest(
        targets, prior, opens, closes, splits, dividends, 0.0025, 100_000.0
    )

    assert turnover.iloc[0] > 0
    assert np.isclose(turnover.iloc[1:].sum(), 0.0)
    assert (shares.iloc[1:].to_numpy() == shares.iloc[0].to_numpy()).all()
