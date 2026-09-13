"""KOSPI fractional-unit ledger; signals at close, fills at close/next open.

Return series reconstruct normalized prices, not exchange-share denominations.
Units stay fixed between orders. Cash earns zero. No unrecorded rebalancing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def validated_common_dates(left, right, start_date, end_date):
    """Return executable shared KRX dates, failing on silent quote gaps.

    Both ETFs trade on the same exchange. If only one has a row, or either
    open/close is missing or non-positive, treating that day as a zero return
    would manufacture an executable history that did not exist.
    """
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()

    def valid_dates(frame, label):
        if not {"open", "close"}.issubset(frame.columns):
            raise ValueError(f"{label}: open/close columns are required")
        subset = frame.loc[(frame.index >= start) & (frame.index <= end), ["open", "close"]]
        numeric = subset.apply(pd.to_numeric, errors="coerce")
        invalid = numeric.isna().any(axis=1) | (numeric <= 0).any(axis=1)
        if invalid.any():
            sample = ", ".join(str(d.date()) for d in numeric.index[invalid][:5])
            raise ValueError(f"{label}: missing/invalid executable prices on {sample}")
        return pd.DatetimeIndex(numeric.index)

    left_dates = valid_dates(left, "KODEX 200")
    right_dates = valid_dates(right, "KODEX Leverage")
    missing_left = right_dates.difference(left_dates)
    missing_right = left_dates.difference(right_dates)
    if len(missing_left) or len(missing_right):
        details = []
        if len(missing_left):
            details.append("KODEX 200 missing " + ", ".join(str(d.date()) for d in missing_left[:5]))
        if len(missing_right):
            details.append("KODEX Leverage missing " + ", ".join(str(d.date()) for d in missing_right[:5]))
        raise ValueError("KRX trading-date mismatch: " + "; ".join(details))
    return left_dates


def backtest_ledger(dates, targets, overnight, intraday, fee_rate,
                    mode="next_open", fill_rate=0.0):
    if mode not in {"next_open", "after_close", "same_close"}:
        raise ValueError("Unknown execution mode")
    if not 0 <= fee_rate < 1 or not 0 <= fill_rate <= 1:
        raise ValueError("Invalid fee/fill rate")
    dates = pd.DatetimeIndex(dates)
    assets = [c for c in targets.columns if c != "Cash"]
    signals = targets.reindex(dates)[assets].astype(float)
    if signals.isna().any().any() or (signals < 0).any().any() or (signals.sum(axis=1) > 1 + 1e-10).any():
        raise ValueError("Targets must be finite long-only weights with total <= 1")
    co = overnight.reindex(index=dates, columns=assets).astype(float)
    oc = intraday.reindex(index=dates, columns=assets).astype(float)
    for returns in (co, oc):
        if not np.isfinite(returns.to_numpy()).all() or (returns <= -1).any().any():
            raise ValueError("Missing or invalid executable asset returns")

    units = pd.Series(0.0, index=assets)
    price = pd.Series(1.0, index=assets)
    cash = 1.0
    last_signal = pd.Series(0.0, index=assets)
    pending_units = None
    nav_rows, weight_rows, trades = [], [], []

    def weights():
        values = units * price
        total = cash + float(values.sum())
        result = values / total
        result["Cash"] = cash / total
        return result

    def target_units(target):
        # Solve post-fee NAV so a 100% allocation cannot overspend its cash.
        values = units * price
        nav = cash + float(values.sum())
        low, high = 0.0, nav
        for _ in range(80):
            net = (low + high) / 2
            required = net + fee_rate * float((net * target - values).abs().sum())
            if required > nav:
                high = net
            else:
                low = net
        return low * target / price

    def execute(desired_units, date, label):
        nonlocal units, cash
        old_weights = weights()
        before = cash + float((units * price).sum())
        delta = desired_units - units
        # Sell first; a gap may make the pending buys unaffordable. Cap buys
        # to actual available cash including their fees, never borrow silently.
        sell = delta.clip(upper=0)
        proceeds = float((-sell * price).sum())
        available = cash + proceeds * (1 - fee_rate)
        buy = delta.clip(lower=0)
        buy_value = float((buy * price).sum())
        if buy_value * (1 + fee_rate) > available and buy_value > 0:
            buy *= max(available, 0.0) / (buy_value * (1 + fee_rate))
        executed = sell + buy
        traded = float((executed.abs() * price).sum())
        cost = traded * fee_rate
        cash += -float((executed * price).sum()) - cost
        if cash < -1e-10:
            raise ArithmeticError("Negative cash after fill")
        cash = max(cash, 0.0)
        units += executed
        if traded > 1e-12:
            new_weights = weights()
            format_weights = lambda row: ", ".join(f"{a} {row[a]:.2%}" for a in row.index)
            trades.append({"Date": date.date(), "Execution": label,
                           "Before Allocation": format_weights(old_weights),
                           "After Allocation": format_weights(new_weights),
                           "Old Weight": float(old_weights[assets].sum()),
                           "New Weight": float(new_weights[assets].sum()),
                           "Turnover": traded / before, "Fee Cost": cost,
                           "NAV": cash + float((units * price).sum()),
                           "Cash": cash,
                           "Unfilled Value": float(((desired_units - units).clip(lower=0) * price).sum()),
                           **{f"Old {a}": float(old_weights[a]) for a in old_weights.index},
                           **{f"New {a}": float(new_weights[a]) for a in new_weights.index}})

    for date in dates:
        price *= 1 + co.loc[date]
        if pending_units is not None:
            execute(pending_units, date, "Next open" if mode == "next_open" else "Next open residual")
            pending_units = None
        price *= 1 + oc.loc[date]
        target = signals.loc[date]
        if not np.allclose(target, last_signal, atol=1e-12, rtol=0):
            desired = target_units(target)
            if mode == "same_close":
                execute(desired, date, "Ideal same close")
            elif mode == "after_close":
                execute(units + fill_rate * (desired - units), date, "After-close fixed close")
                if fill_rate < 1:
                    pending_units = desired
            else:
                pending_units = desired
            last_signal = target.copy()
        nav_rows.append(cash + float((units * price).sum()))
        weight_rows.append(weights())

    columns = ["Date", "Execution", "Old Weight", "New Weight", "Turnover", "Fee Cost", "NAV", "Cash", "Unfilled Value"]
    return (pd.Series(nav_rows, index=dates, name="Strategy"),
            pd.DataFrame(weight_rows, index=dates), pd.DataFrame(trades) if trades else pd.DataFrame(columns=columns))
