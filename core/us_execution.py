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
    target_values = targets.reindex(index=dates, columns=assets).to_numpy(dtype=float)
    prior_values = prior_closes.reindex(index=dates, columns=assets).to_numpy(dtype=float)
    open_values = opens.reindex(index=dates, columns=assets).to_numpy(dtype=float)
    close_values = closes.reindex(index=dates, columns=assets).to_numpy(dtype=float)
    units = np.zeros(len(assets), dtype=float)
    cash = 1.0
    previous_target = None
    nav_rows, turnover_rows = [], []

    for number in range(len(dates)):
        target = np.clip(target_values[number], 0.0, 1.0)
        if not np.isfinite(target).all() or target.sum() > 1 + 1e-10:
            raise ValueError("Targets must be finite long-only weights with total <= 1")
        changed = (
            rebalance_every_session
            or previous_target is None
            or not np.allclose(target, previous_target, atol=1e-12, rtol=0.0)
        )
        traded = 0.0
        open_px = open_values[number]
        nav_before_trade = cash + float(np.dot(units, open_px))
        if changed:
            sizing_px = prior_values[number]
            signal_nav = cash + float(np.dot(units, sizing_px))
            current_values = units * sizing_px
            low, high = 0.0, signal_nav
            for _ in range(48):
                net = (low + high) / 2
                required = net + fee_rate * float(np.abs(net * target - current_values).sum())
                if required > signal_nav:
                    high = net
                else:
                    low = net
            desired_units = low * target / sizing_px

            delta = desired_units - units
            sells = np.minimum(delta, 0.0)
            available = cash + float(np.dot(-sells, open_px)) * (1 - fee_rate)
            buys = np.maximum(delta, 0.0)
            buy_value = float(np.dot(buys, open_px))
            if buy_value * (1 + fee_rate) > available and buy_value > 0:
                buys *= max(available, 0.0) / (buy_value * (1 + fee_rate))
            executed = sells + buys
            traded = float(np.dot(np.abs(executed), open_px))
            cash += -float(np.dot(executed, open_px)) - traded * fee_rate
            cash = max(cash, 0.0)
            units += executed
            previous_target = target.copy()

        close_nav = cash + float(np.dot(units, close_values[number]))
        nav_rows.append(close_nav)
        turnover_rows.append(traded / nav_before_trade if nav_before_trade > 0 else 0.0)

    nav = pd.Series(nav_rows, index=dates, name="Close NAV")
    daily = nav.pct_change(fill_method=None)
    daily.iloc[0] = nav.iloc[0] - 1.0
    return daily.fillna(0.0), pd.Series(turnover_rows, index=dates, name="Turnover"), nav


def whole_share_open_backtest(
    targets,
    prior_closes,
    opens,
    closes,
    splits,
    dividends,
    fee_rate,
    initial_capital,
):
    """Backtest executable whole-share orders with raw prices and corporate actions.

    Targets are known after the prior close. Desired share counts are therefore
    fixed from the prior raw close, then sells and affordable buys execute at the
    next raw open. Split adjustments occur before that open; dividends are added
    to cash on the ex-date as an economic-value approximation.
    """
    if not 0 <= fee_rate < 1:
        raise ValueError("fee_rate must be in [0, 1)")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    dates = pd.DatetimeIndex(targets.index)
    assets = list(targets.columns)
    price_frames = []
    for frame, label in [(prior_closes, "prior close"), (opens, "open"), (closes, "close")]:
        values = frame.reindex(index=dates, columns=assets).astype(float)
        if not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any():
            raise ValueError(f"Missing or invalid {label} prices")
        price_frames.append(values)
    prior_values, open_values, close_values = [frame.to_numpy(dtype=float) for frame in price_frames]

    split_values = splits.reindex(index=dates, columns=assets, fill_value=1.0).astype(float).to_numpy()
    dividend_values = dividends.reindex(index=dates, columns=assets, fill_value=0.0).astype(float).to_numpy()
    if not np.isfinite(split_values).all() or (split_values <= 0).any():
        raise ValueError("Split ratios must be finite and positive")
    if not np.isfinite(dividend_values).all() or (dividend_values < 0).any():
        raise ValueError("Dividends must be finite and non-negative")

    shares = np.zeros(len(assets), dtype=float)
    cash = float(initial_capital)
    previous_nav = float(initial_capital)
    previous_target = None
    returns, turnover_rows, cash_rows = [], [], []
    weight_rows, share_rows = [], []

    for number, _date in enumerate(dates):
        target = np.clip(targets.iloc[number].to_numpy(dtype=float), 0.0, 1.0)
        if not np.isfinite(target).all() or target.sum() > 1 + 1e-10:
            raise ValueError("Targets must be finite long-only weights with total <= 1")
        changed = previous_target is None or not np.allclose(
            target, previous_target, atol=1e-12, rtol=0.0
        )

        split = split_values[number]
        pre_split_shares = shares.copy()
        shares *= split
        dividend_receivable = float(np.dot(shares, dividend_values[number]))
        open_px = open_values[number]
        nav_before = cash + float(np.dot(shares, open_px))
        traded = 0.0

        if changed:
            sizing_px = prior_values[number]
            signal_nav = cash + float(np.dot(pre_split_shares, sizing_px))
            current_values = pre_split_shares * sizing_px
            low, high = 0.0, signal_nav
            for _ in range(48):
                net = (low + high) / 2
                required = net + fee_rate * float(np.abs(net * target - current_values).sum())
                if required > signal_nav:
                    high = net
                else:
                    low = net
            desired_pre_split = np.floor(low * target / sizing_px + 1e-10)
            desired = np.floor(desired_pre_split * split + 1e-12)
            delta = desired - shares

            sells = np.minimum(delta, 0.0)
            sell_value = float(np.dot(-sells, open_px))
            cash += sell_value * (1 - fee_rate)
            shares += sells
            traded += sell_value

            desired_buys = np.maximum(desired - shares, 0.0)
            buy_cost = float(np.dot(desired_buys, open_px)) * (1 + fee_rate)
            while buy_cost > cash + 1e-9 and desired_buys.sum() > 0:
                candidates = []
                for asset_number in np.flatnonzero(desired_buys > 0):
                    candidate = desired_buys.copy()
                    candidate[asset_number] -= 1
                    resulting = shares + candidate
                    weights_at_open = resulting * open_px / max(nav_before, 1e-12)
                    error = float(np.square(weights_at_open - target).sum())
                    candidates.append((error, asset_number, candidate))
                _, _, desired_buys = min(candidates, key=lambda item: (item[0], item[1]))
                buy_cost = float(np.dot(desired_buys, open_px)) * (1 + fee_rate)

            buy_value = float(np.dot(desired_buys, open_px))
            cash -= buy_value * (1 + fee_rate)
            shares += desired_buys
            traded += buy_value
            cash = max(cash, 0.0)
            previous_target = target.copy()

        # Ex-date entitlement belongs to the shares held before today's open,
        # including positions sold at that open and excluding new purchases.
        cash += dividend_receivable
        close_nav = cash + float(np.dot(shares, close_values[number]))
        returns.append(close_nav / previous_nav - 1 if previous_nav > 0 else 0.0)
        previous_nav = close_nav
        values = shares * close_values[number]
        weight_rows.append(values / close_nav if close_nav > 0 else np.zeros(len(assets)))
        share_rows.append(shares.copy())
        cash_rows.append(cash)
        turnover_rows.append(traded / nav_before if nav_before > 0 else 0.0)

    return (
        pd.Series(returns, index=dates, name="Return").replace([np.inf, -np.inf], np.nan).fillna(0.0),
        pd.DataFrame(weight_rows, index=dates, columns=assets),
        pd.Series(turnover_rows, index=dates, name="Turnover"),
        pd.DataFrame(share_rows, index=dates, columns=assets),
        pd.Series(cash_rows, index=dates, name="Cash"),
    )


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
