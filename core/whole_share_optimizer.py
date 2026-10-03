"""Whole-share allocation optimizer shared by Streamlit and local execution."""
from __future__ import annotations

from itertools import product

import numpy as np


def optimize_whole_share_targets(target, prices, current_shares, cash, fee_rate=0.0):
    """Choose the feasible whole-share mix closest to the target allocation."""
    target = np.asarray(target, dtype=float)
    prices = np.asarray(prices, dtype=float)
    current = np.asarray(current_shares, dtype=float)
    if target.ndim != 1 or prices.shape != target.shape or current.shape != target.shape:
        raise ValueError("target, prices, and current_shares must be matching vectors")
    if not np.isfinite(target).all() or (target < 0).any() or target.sum() > 1 + 1e-10:
        raise ValueError("Targets must be finite long-only weights with total <= 1")
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Prices must be finite and positive")
    if not np.isfinite(current).all() or (current < 0).any():
        raise ValueError("Current shares must be finite and non-negative")
    if not np.isfinite(cash) or cash < 0:
        raise ValueError("Cash must be finite and non-negative")
    if not 0 <= fee_rate < 1:
        raise ValueError("fee_rate must be in [0, 1)")

    current = np.rint(current).astype(int)
    account_value = float(cash + np.dot(current, prices))
    ideal = account_value * target / prices
    candidate_sets = []
    for target_weight, ideal_units, held_units in zip(target, ideal, current):
        if target_weight <= 1e-12:
            candidate_sets.append([0])
            continue
        floor_units = int(np.floor(ideal_units + 1e-12))
        choices = set(range(max(0, floor_units - 3), floor_units + 4))
        choices.update({0, int(held_units), max(int(held_units) - 1, 0), int(held_units) + 1})
        candidate_sets.append(sorted(choices))

    target_with_cash = np.append(target, max(1.0 - float(target.sum()), 0.0))
    best_key = None
    best = None
    for units_tuple in product(*candidate_sets):
        units = np.asarray(units_tuple, dtype=int)
        turnover_value = float(np.dot(np.abs(units - current), prices))
        fees = fee_rate * turnover_value
        final_cash = account_value - float(np.dot(units, prices)) - fees
        if final_cash < -1e-7:
            continue
        post_cost_value = account_value - fees
        if post_cost_value <= 0:
            continue
        actual = np.append(
            units * prices / post_cost_value,
            max(final_cash, 0.0) / post_cost_value,
        )
        error = float(np.square(actual - target_with_cash).sum())
        key = (round(error, 15), turnover_value, int(np.abs(units - current).sum()), units_tuple)
        if best_key is None or key < best_key:
            best_key, best = key, units

    if best is None:
        raise RuntimeError("No feasible whole-share allocation was found")
    return best
