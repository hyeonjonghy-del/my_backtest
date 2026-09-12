"""Focused regression tests for the S&P 500 momentum page helpers."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd


PAGE = Path(__file__).parents[1] / "pages" / "2_S&P500_Momentun.py"


def _load_helper(name: str):
    """Load one page helper without executing the Streamlit application."""
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"pd": pd, "np": np}
    exec(compile(module, str(PAGE), "exec"), namespace)
    return namespace[name]


def test_buy_and_hold_keeps_missing_holding_at_last_close() -> None:
    calculate = _load_helper("buy_and_hold_equal_weight_returns")
    dates = pd.date_range("2026-09-09", periods=3, freq="D")
    prices = pd.DataFrame(
        {"AAA": [100.0, 110.0, 121.0], "BBB": [100.0, np.nan, np.nan]},
        index=dates,
    )

    returns = calculate(prices)

    expected_nav = pd.Series([1.05, 1.105], index=dates[1:])
    actual_nav = (1.0 + returns).cumprod()
    pd.testing.assert_series_equal(actual_nav, expected_nav, check_names=False)
    assert returns.notna().all()


def test_stock_only_mix_ignores_missing_cash_quotes() -> None:
    mix = _load_helper("mix_stock_and_cash")
    dates = pd.date_range("2026-09-10", periods=2, freq="D")
    stock_returns = pd.Series([0.01, 0.02], index=dates)
    missing_cash = pd.Series([np.nan, np.nan], index=dates)

    returns, stock_weights = mix(
        stock_returns, missing_cash, 1.0, 0.0, 0.0, 0.0
    )

    pd.testing.assert_series_equal(returns, stock_returns)
    assert (stock_weights == 1.0).all()
