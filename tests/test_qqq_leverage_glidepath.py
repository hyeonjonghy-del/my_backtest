from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.qqq_leverage_glidepath.strategy import (
    ASSETS,
    GlidepathConfig,
    REGIME_WEIGHTS,
    backtest_next_open,
    build_signals,
    effective_nasdaq_exposure,
    target_for_regime,
)


def _frame(index: pd.DatetimeIndex, close: np.ndarray, open_: np.ndarray | None = None):
    open_values = close if open_ is None else open_
    return pd.DataFrame(
        {"open": open_values, "close": close, "adjclose": close}, index=index
    )


def test_regime_targets_are_fully_invested_and_have_expected_exposure() -> None:
    expected = {
        "bear": 0.30,
        "caution": 0.65,
        "turn1": 0.65,
        "turn2": 1.15,
        "bull": 2.00,
    }
    for regime, target in REGIME_WEIGHTS.items():
        assert np.isclose(target.sum(), 1.0)
        assert (target >= 0).all()
        assert np.isclose(effective_nasdaq_exposure(target), expected[regime])


def test_backtest_uses_prior_close_regime_at_next_open() -> None:
    index = pd.bdate_range("2024-01-02", periods=14)
    qqq = np.array([100, 102, 104, 106, 108, 110, 92, 90, 93, 96, 99, 103, 107, 111], dtype=float)
    flat = np.full(len(index), 100.0)
    unavailable = np.full(len(index), np.nan)
    frames = {
        "QQQ": _frame(index, qqq),
        "QLD": _frame(index, flat),
        "TQQQ": _frame(index, flat),
        "BIL": _frame(index, flat),
        "SGOV": _frame(index, unavailable),
    }
    config = GlidepathConfig(
        fast_days=2,
        slow_days=3,
        recovery_slope_days=1,
        peak_lookback_days=2,
        caution_drawdown=0.075,
        rebalance_monthly=False,
    )
    signals = build_signals(pd.Series(qqq, index=index), config)
    result, _ = backtest_next_open(frames, index[4], 100_000.0, config)

    for day in result.index[1:]:
        prior_position = index.get_loc(day) - 1
        expected = signals.loc[index[prior_position], "Regime"]
        assert result.loc[day, "Regime"] == expected


def test_overnight_gap_is_borne_by_old_allocation_before_rebalance() -> None:
    index = pd.bdate_range("2024-01-02", periods=12)
    qqq = np.array([100, 102, 104, 106, 108, 110, 90, 90, 91, 92, 93, 94], dtype=float)
    qld = np.full(len(index), 100.0)
    tqqq_close = np.full(len(index), 100.0)
    tqqq_open = tqqq_close.copy()
    # The plunge signal is known only after index[6] closes. The old bull
    # allocation must therefore bear this next-open gap before switching.
    tqqq_open[7] = 70.0
    tqqq_close[7] = 70.0
    flat = np.full(len(index), 100.0)
    frames = {
        "QQQ": _frame(index, qqq),
        "QLD": _frame(index, qld),
        "TQQQ": _frame(index, tqqq_close, tqqq_open),
        "BIL": _frame(index, flat),
        "SGOV": _frame(index, np.full(len(index), np.nan)),
    }
    config = GlidepathConfig(
        fast_days=2,
        slow_days=3,
        recovery_slope_days=1,
        peak_lookback_days=2,
        caution_drawdown=0.075,
        rebalance_monthly=False,
    )
    result, _ = backtest_next_open(frames, index[4], 100_000.0, config)

    # If the new defensive target had been applied before the overnight gap,
    # TQQQ would already be zero and this loss would disappear.
    assert result.loc[index[7], "Wealth"] < result.loc[index[6], "Wealth"] * 0.95
    assert result.loc[index[7], "Weight_TQQQ"] == 0.0


def test_sgov_replaces_bil_when_history_becomes_available() -> None:
    index = pd.bdate_range("2024-01-02", periods=14)
    qqq = np.array([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87], dtype=float)
    flat = np.full(len(index), 100.0)
    sgov = np.full(len(index), np.nan)
    sgov[9:] = 100.0
    frames = {asset: _frame(index, flat) for asset in ASSETS}
    frames["QQQ"] = _frame(index, qqq)
    frames["SGOV"] = _frame(index, sgov)
    config = GlidepathConfig(
        fast_days=2,
        slow_days=3,
        recovery_slope_days=1,
        peak_lookback_days=2,
        caution_drawdown=0.075,
        rebalance_monthly=False,
    )
    result, _ = backtest_next_open(frames, index[4], 100_000.0, config)

    first_sgov_day = index[9]
    assert result.loc[first_sgov_day, "Weight_SGOV"] > 0
    assert np.isclose(result.loc[first_sgov_day, "Weight_BIL"], 0.0)
    assert np.isclose(target_for_regime("bear")["SGOV"], 0.70)
