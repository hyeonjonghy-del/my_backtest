"""Execution-safe helpers for US close-signal, next-open strategies."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def validated_common_dates(frames, start_date, end_date):
    start, end = pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize()
    indexes = {}
    for symbol, frame in frames.items():
        required = {"open", "close", "adjclose"}
        if not required.issubset(frame.columns):
            raise ValueError(f"{symbol}: open/close/adjclose columns are required")
        values = frame.loc[(frame.index >= start) & (frame.index <= end), list(required)].apply(
            pd.to_numeric, errors="coerce"
        )
        invalid = values.isna().any(axis=1) | (values <= 0).any(axis=1)
        if invalid.any():
            sample = ", ".join(str(date.date()) for date in values.index[invalid][:5])
            raise ValueError(f"{symbol}: missing/invalid executable prices on {sample}")
        indexes[symbol] = pd.DatetimeIndex(values.index)
    union = pd.DatetimeIndex([])
    common = None
    for index in indexes.values():
        union = union.union(index)
        common = index if common is None else common.intersection(index)
    for symbol, index in indexes.items():
        missing = union.difference(index)
        if len(missing):
            sample = ", ".join(str(date.date()) for date in missing[:5])
            raise ValueError(f"{symbol}: missing trading dates {sample}")
    return common if common is not None else pd.DatetimeIndex([])


def adjusted_open(frame):
    factor = (frame["adjclose"] / frame["close"]).replace([np.inf, -np.inf], np.nan)
    result = frame["open"] * factor
    return result.where((result > 0) & np.isfinite(result))


def fixed_units_open_backtest(
    targets, prior_closes, opens, closes, fee_rate, rebalance_every_session=False
):
    """Size after a close signal, then execute those fixed units next open."""
    if not 0 <= fee_rate < 1:
        raise ValueError("fee_rate must be in [0, 1)")
    dates = pd.DatetimeIndex(targets.index)
    assets = list(targets.columns)
    for frame, label in [(prior_closes, "prior close"), (opens, "open"), (closes, "close")]:
        values = frame.reindex(index=dates, columns=assets).astype(float)
        if not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any():
            raise ValueError(f"Missing or invalid {label} prices")
    units = pd.Series(0.0, index=assets)
    cash = 1.0
    previous_target = None
    prior_nav = 1.0
    nav_rows, turnover_rows = [], []

    for number, date in enumerate(dates):
        target = targets.loc[date].astype(float).clip(0, 1)
        if not np.isfinite(target).all() or target.sum() > 1 + 1e-10:
            raise ValueError("Targets must be finite long-only weights with total <= 1")
        changed = (
            rebalance_every_session
            or previous_target is None
            or not np.allclose(target, previous_target, atol=1e-12, rtol=0)
        )
        traded = 0.0
        if changed:
            sizing_px = prior_closes.loc[date]
            signal_nav = cash + float((units * sizing_px).sum())
            current_values = units * sizing_px
            low, high = 0.0, signal_nav
            for _ in range(80):
                net = (low + high) / 2
                required = net + fee_rate * float((net * target - current_values).abs().sum())
                if required > signal_nav:
                    high = net
                else:
                    low = net
            desired_units = low * target / sizing_px

            open_px = opens.loc[date]
            delta = desired_units - units
            sells = delta.clip(upper=0)
            available = cash + float((-sells * open_px).sum()) * (1 - fee_rate)
            buys = delta.clip(lower=0)
            buy_value = float((buys * open_px).sum())
            if buy_value * (1 + fee_rate) > available and buy_value > 0:
                buys *= max(available, 0.0) / (buy_value * (1 + fee_rate))
            executed = sells + buys
            traded = float((executed.abs() * open_px).sum())
            cash += -float((executed * open_px).sum()) - traded * fee_rate
            cash = max(cash, 0.0)
            units += executed
            previous_target = target.copy()

        close_nav = cash + float((units * closes.loc[date]).sum())
        nav_rows.append(close_nav)
        open_nav = cash + float((units * opens.loc[date]).sum())
        turnover_rows.append(traded / open_nav if open_nav > 0 else 0.0)
        prior_nav = close_nav

    nav = pd.Series(nav_rows, index=dates, name="Close NAV")
    daily = nav.pct_change(fill_method=None)
    daily.iloc[0] = nav.iloc[0] - 1.0
    return daily.fillna(0.0), pd.Series(turnover_rows, index=dates, name="Turnover"), nav


def next_nyse_session(date):
    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar("NYSE")
    start = pd.Timestamp(date) + pd.Timedelta(days=1)
    valid = calendar.valid_days(start, start + pd.Timedelta(days=10), tz=None)
    if len(valid) == 0:
        raise RuntimeError("Could not determine the next NYSE session")
    return pd.Timestamp(valid[0]).tz_localize(None).normalize()


def rebalance_due_after_close(signal_date, frequency):
    if frequency == "Daily":
        return True
    next_date = next_nyse_session(signal_date)
    signal_date = pd.Timestamp(signal_date)
    if frequency == "Weekly":
        return tuple(next_date.isocalendar()[:2]) != tuple(signal_date.isocalendar()[:2])
    if frequency == "Monthly":
        return (next_date.year, next_date.month) != (signal_date.year, signal_date.month)
    raise ValueError(f"Unknown rebalance frequency: {frequency}")


def latest_completed_nyse_session(as_of: datetime):
    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(
        start_date=(as_of.date() - timedelta(days=10)), end_date=as_of.date(), tz="America/New_York"
    )
    completed = schedule.loc[schedule["market_close"] <= pd.Timestamp(as_of)]
    if completed.empty:
        raise RuntimeError("Could not determine the latest completed NYSE session")
    return pd.Timestamp(completed.index[-1]).tz_localize(None).normalize()
