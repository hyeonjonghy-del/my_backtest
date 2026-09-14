from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.qqq_gld_sgov_momentum_v2.strategy import (
    backtest_next_open_whole_shares,
    make_monthly_targets,
)


def _frame(index: pd.DatetimeIndex, prices: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": prices,
            "close": prices,
            "adjclose": prices,
            "split_ratio": 1.0,
            "dividend": 0.0,
        },
        index=index,
    )


def test_month_end_signal_executes_at_next_open_without_capturing_overnight_gap() -> None:
    index = pd.bdate_range("2020-01-02", "2021-08-31")
    step = np.arange(len(index), dtype=float)
    signal_prices = pd.DataFrame(
        {
            "QQQ": 100.0 + step * 0.20,
            "GLD": 100.0 + step * 0.05,
            "SGOV": 100.0 + step * 0.01,
        },
        index=index,
    )
    _, signals = make_monthly_targets(signal_prices)
    first_holding_period = signals.dropna().index[0].to_period("M") + 1
    first_execution = index[index.to_period("M") == first_holding_period][0]

    executable = np.full(len(index), 100.0)
    executable[index >= first_execution] = 200.0
    frames = {
        "QQQ": _frame(index, executable),
        "GLD": _frame(index, executable),
        "BIL": _frame(index, executable),
    }
    result, metrics = backtest_next_open_whole_shares(
        signal_prices,
        frames,
        {"QQQ": "QQQ", "GLD": "GLD", "CASH_BEFORE": "BIL", "CASH_AFTER": None},
        initial_capital=100_000.0,
        cost_bps=0.0,
    )

    assert result.index[0] == first_execution
    assert np.isclose(result["Wealth"].iloc[0], 1.0)
    assert np.isclose(metrics["최종 배수"], 1.0)
    assert (result.filter(like="Shares ") % 1 == 0).all().all()


def test_unavailable_post_listing_cash_etf_does_not_break_pre_listing_backtest() -> None:
    index = pd.bdate_range("2020-01-02", "2021-08-31")
    step = np.arange(len(index), dtype=float)
    signal_prices = pd.DataFrame(
        {"QQQ": 100 + step * 0.2, "GLD": 100 + step * 0.1, "SGOV": 100 + step * 0.01},
        index=index,
    )
    flat = np.full(len(index), 100.0)
    frames = {
        "QQQ": _frame(index, flat),
        "GLD": _frame(index, flat),
        "BIL": _frame(index, flat),
        "SGOV": pd.DataFrame(columns=["open", "close", "adjclose", "split_ratio", "dividend"]),
    }
    result, _ = backtest_next_open_whole_shares(
        signal_prices,
        frames,
        {"QQQ": "QQQ", "GLD": "GLD", "CASH_BEFORE": "BIL", "CASH_AFTER": "SGOV"},
        initial_capital=100_000.0,
    )
    assert not result.empty
