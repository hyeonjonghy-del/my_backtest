import numpy as np
import pandas as pd

from strategies.qqq_gld_sgov_momentum_v2.strategy import backtest, make_monthly_targets


def steadily_rising_prices() -> pd.DataFrame:
    index = pd.date_range("2020-01-31", periods=15, freq="ME")
    step = np.arange(len(index), dtype=float)
    return pd.DataFrame(
        {
            "QQQ": 100.0 + step,
            "GLD": 100.0 + step * 0.5,
            "SGOV": 100.0 + step * 2.0,
        },
        index=index,
    )


def test_rank1_sgov_weight_is_configurable() -> None:
    _, targets = make_monthly_targets(steadily_rising_prices(), cash_rank1_weight=0.70)

    first_target = targets.dropna().iloc[0]
    assert first_target["SGOV"] == 0.70
    assert np.isclose(first_target.sum(), 1.0)


def test_backtest_passes_rank1_sgov_weight_to_targets() -> None:
    result, _ = backtest(steadily_rising_prices(), cash_rank1_weight=0.70)

    assert (result["SGOV rank"] == 1).all()
    assert (result["Target SGOV"] == 0.70).all()
