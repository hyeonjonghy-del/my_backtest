from __future__ import annotations

import numpy as np
import pandas as pd


ASSETS = ("QQQ", "GLD", "SGOV")


def _sgov_rank(scores: pd.Series) -> int:
    tie_order = ("SGOV", "GLD", "QQQ")
    ranked = sorted(tie_order, key=lambda asset: -float(scores[asset]))
    return ranked.index("SGOV") + 1


def make_sgov_bil_proxy(raw_prices: pd.DataFrame) -> pd.DataFrame:
    """Create a continuous cash index using BIL before SGOV history exists.

    The series is linked with daily adjusted-price returns rather than raw price
    levels, preventing an artificial jump when the source changes to SGOV.
    """
    required = ("QQQ", "GLD", "BIL", "SGOV")
    missing = [asset for asset in required if asset not in raw_prices.columns]
    if missing:
        raise ValueError(f"Missing price columns: {missing}")

    base = raw_prices.loc[:, ["QQQ", "GLD", "BIL"]].sort_index().dropna(how="any")
    if base.empty:
        raise ValueError("No common QQQ/GLD/BIL price history is available")
    sgov = raw_prices["SGOV"].reindex(base.index)
    bil_return = base["BIL"].pct_change(fill_method=None)
    sgov_return = sgov.pct_change(fill_method=None)
    cash_return = sgov_return.combine_first(bil_return).fillna(0.0)

    result = base.loc[:, ["QQQ", "GLD"]].copy()
    result["SGOV"] = (1.0 + cash_return).cumprod() * 100.0
    return result.loc[:, list(ASSETS)]


def make_monthly_targets(
    prices: pd.DataFrame,
    momentum_months: int = 12,
    strong_asset_weight: float = 0.80,
    qqq_boost_weight: float = 0.90,
    qqq_lead_threshold: float = 0.25,
    gold_max_weight: float = 0.60,
    cash_rank2_weight: float = 0.20,
    cash_rank1_weight: float = 0.40,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return month-end momentum and targets for the next holding month.

    When QQQ leads GLD, the non-SGOV allocation normally splits 80:20.
    When QQQ's 12-month momentum leads GLD by at least 25 percentage points,
    the non-SGOV allocation is tilted further to QQQ in a 90:10 ratio.
    When GLD leads QQQ, GLD is capped at 60% of the non-SGOV allocation and
    QQQ receives the remaining 40%.
    SGOV receives 0%, 20%, or the configurable rank-1 weight (40% by
    default) when it ranks third, second, or first.
    Exact momentum ties are resolved conservatively in SGOV's favor, then GLD,
    then QQQ.
    """
    missing = [asset for asset in ASSETS if asset not in prices.columns]
    if missing:
        raise ValueError(f"Missing price columns: {missing}")
    if momentum_months < 1:
        raise ValueError("momentum_months must be at least 1")
    if not 0.5 <= strong_asset_weight <= 1.0:
        raise ValueError("strong_asset_weight must be between 0.5 and 1.0")
    if not strong_asset_weight <= qqq_boost_weight <= 1.0:
        raise ValueError("qqq_boost_weight must be between strong_asset_weight and 1.0")
    if qqq_lead_threshold < 0.0:
        raise ValueError("qqq_lead_threshold cannot be negative")
    if not 0.5 <= gold_max_weight <= strong_asset_weight:
        raise ValueError("gold_max_weight must be between 0.5 and strong_asset_weight")
    if not 0.0 <= cash_rank2_weight <= cash_rank1_weight < 1.0:
        raise ValueError("cash weights must satisfy 0 <= rank2 <= rank1 < 1")

    month_end = prices.loc[:, list(ASSETS)].resample("ME").last()
    momentum = month_end.pct_change(momentum_months, fill_method=None)
    targets = pd.DataFrame(np.nan, index=month_end.index, columns=ASSETS)

    for signal_date, scores in momentum.dropna(how="any").iterrows():
        cash_rank = _sgov_rank(scores)
        cash_weight = {1: cash_rank1_weight, 2: cash_rank2_weight, 3: 0.0}[cash_rank]

        qqq_lead = float(scores["QQQ"] - scores["GLD"])
        if qqq_lead >= qqq_lead_threshold:
            qqq_share = qqq_boost_weight
        elif qqq_lead >= 0.0:
            qqq_share = strong_asset_weight
        else:
            qqq_share = 1.0 - gold_max_weight
        risky_weight = 1.0 - cash_weight
        row = pd.Series(0.0, index=ASSETS)
        row.loc["QQQ"] = risky_weight * qqq_share
        row.loc["GLD"] = risky_weight * (1.0 - qqq_share)
        row.loc["SGOV"] = cash_weight
        targets.loc[signal_date] = row

    return momentum, targets


def backtest(
    prices: pd.DataFrame,
    rebalance_months: int = 1,
    momentum_months: int = 12,
    cost_bps: float = 10.0,
    cash_rank1_weight: float = 0.40,
    cash_rank2_weight: float = 0.20,
) -> tuple[pd.DataFrame, dict[str, float | str]]:
    if rebalance_months not in (1, 3, 6, 12):
        raise ValueError("rebalance_months must be one of 1, 3, 6, or 12")
    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")

    clean_prices = prices.loc[:, list(ASSETS)].sort_index().dropna(how="any")
    if clean_prices.empty:
        raise ValueError("No common QQQ/GLD/SGOV price history is available")

    momentum, signal_targets = make_monthly_targets(
        clean_prices,
        momentum_months,
        cash_rank1_weight=cash_rank1_weight,
        cash_rank2_weight=cash_rank2_weight,
    )
    # A completed month-end signal is first tradable in the following month.
    applied_targets = signal_targets.shift(1)
    valid_signal_ranks = momentum.apply(
        lambda row: _sgov_rank(row) if row.notna().all() else pd.NA,
        axis=1,
    ).astype("Int64")
    applied_cash_rank = valid_signal_ranks.shift(1)

    period_index = clean_prices.index.to_period("M")
    monthly_targets = applied_targets.copy()
    monthly_targets.index = monthly_targets.index.to_period("M")
    daily_target = monthly_targets.reindex(period_index).ffill()
    daily_target.index = clean_prices.index
    rank_by_period = applied_cash_rank.copy()
    rank_by_period.index = rank_by_period.index.to_period("M")
    daily_cash_rank = rank_by_period.reindex(period_index).ffill()
    daily_cash_rank.index = clean_prices.index

    daily_returns = clean_prices.pct_change(fill_method=None).fillna(0.0)
    rebalance_periods = {
        period for period in monthly_targets.index
        if (period.month - 1) % rebalance_months == 0
    }

    wealth = 1.0
    actual: pd.Series | None = None
    previous_period = None
    rows: list[dict[str, object]] = []
    for day, daily_return in daily_returns.iterrows():
        desired = daily_target.loc[day]
        if desired.isna().any():
            continue
        period = day.to_period("M")
        if actual is None:
            actual = desired.copy()
        is_rebalance = period != previous_period and period in rebalance_periods
        turnover = float((desired - actual).abs().sum()) if is_rebalance else 0.0
        if is_rebalance:
            wealth *= 1.0 - turnover * cost_bps / 10000.0
            actual = desired.copy()

        wealth *= float((actual * (1.0 + daily_return)).sum())
        actual = actual * (1.0 + daily_return)
        actual = actual / actual.sum()
        rows.append({
            "Date": day,
            "Wealth": wealth,
            **{f"Target {asset}": float(desired[asset]) for asset in ASSETS},
            **{f"Actual {asset}": float(actual[asset]) for asset in ASSETS},
            "SGOV rank": int(daily_cash_rank.loc[day]),
            "Rebalance": is_rebalance,
            "Turnover": turnover,
        })
        previous_period = period

    if not rows:
        raise ValueError("At least 12 completed months of common price history are required")

    result = pd.DataFrame(rows).set_index("Date")
    returns = result["Wealth"].pct_change(fill_method=None).fillna(0.0)
    years = (result.index[-1] - result.index[0]).days / 365.2425
    drawdown = result["Wealth"] / result["Wealth"].cummax() - 1.0
    volatility = float(returns.std(ddof=1) * np.sqrt(252.0))
    metrics: dict[str, float | str] = {
        "시작일": str(result.index[0].date()),
        "종료일": str(result.index[-1].date()),
        "최종 배수": float(result["Wealth"].iloc[-1]),
        "CAGR": float(result["Wealth"].iloc[-1] ** (1.0 / years) - 1.0),
        "MDD": float(drawdown.min()),
        "연 변동성": volatility,
        "Sharpe": float(returns.mean() * 252.0 / volatility) if volatility else np.nan,
        "평균 SGOV 비중": float(result["Actual SGOV"].mean()),
        "리밸런싱 횟수": float(result["Rebalance"].sum()),
    }
    return result, metrics


def backtest_next_open_whole_shares(
    prices: pd.DataFrame,
    execution_frames: dict[str, pd.DataFrame],
    execution_assets: dict[str, str | None],
    initial_capital: float,
    rebalance_months: int = 1,
    momentum_months: int = 12,
    cost_bps: float = 10.0,
    cash_rank1_weight: float = 0.40,
    cash_rank2_weight: float = 0.20,
) -> tuple[pd.DataFrame, dict[str, float | str]]:
    """Backtest month-end signals with executable whole-share next-open orders.

    ``execution_assets`` maps ``QQQ`` and ``GLD`` to their tradeable tickers.
    ``CASH_BEFORE`` is the cash ETF used before ``CASH_AFTER`` becomes
    available (BIL before SGOV for the US market); it may be ``None`` to leave
    that allocation as uninvested cash. Corporate actions and raw executable
    prices come from ``execution_frames``.
    """
    from core.us_execution import split_unadjusted_price, whole_share_open_backtest

    if rebalance_months not in (1, 3, 6, 12):
        raise ValueError("rebalance_months must be one of 1, 3, 6, or 12")
    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    clean_prices = prices.loc[:, list(ASSETS)].sort_index().dropna(how="any")
    if clean_prices.empty:
        raise ValueError("No common QQQ/GLD/cash-proxy price history is available")

    momentum, signal_targets = make_monthly_targets(
        clean_prices,
        momentum_months,
        cash_rank1_weight=cash_rank1_weight,
        cash_rank2_weight=cash_rank2_weight,
    )
    applied_targets = signal_targets.shift(1)
    applied_ranks = momentum.apply(
        lambda row: _sgov_rank(row) if row.notna().all() else pd.NA,
        axis=1,
    ).astype("Int64").shift(1)

    periods = clean_prices.index.to_period("M")
    monthly_targets = applied_targets.copy()
    monthly_targets.index = monthly_targets.index.to_period("M")
    desired_daily = monthly_targets.reindex(periods).ffill()
    desired_daily.index = clean_prices.index
    monthly_ranks = applied_ranks.copy()
    monthly_ranks.index = monthly_ranks.index.to_period("M")
    daily_rank = monthly_ranks.reindex(periods).ffill()
    daily_rank.index = clean_prices.index

    valid = desired_daily.notna().all(axis=1) & daily_rank.notna()
    dates = clean_prices.index[valid]
    if dates.empty:
        raise ValueError("At least 12 completed months of common price history are required")

    qqq_ticker = execution_assets["QQQ"]
    gld_ticker = execution_assets["GLD"]
    cash_before = execution_assets.get("CASH_BEFORE")
    cash_after = execution_assets.get("CASH_AFTER")
    requested_tickers = [
        ticker for ticker in (qqq_ticker, gld_ticker, cash_before, cash_after) if ticker
    ]
    missing_frames = [ticker for ticker in requested_tickers if ticker not in execution_frames]
    if missing_frames:
        raise ValueError(f"Missing execution frames: {missing_frames}")

    cash_after_first = None
    if cash_after:
        after_frame = execution_frames[cash_after]
        available = after_frame.dropna(subset=["open", "close", "adjclose"])
        if not available.empty:
            cash_after_first = available.index.min()
        else:
            cash_after = None
    trade_tickers = list(dict.fromkeys(
        ticker for ticker in (qqq_ticker, gld_ticker, cash_before, cash_after) if ticker
    ))

    scheduled_months = {
        period for period in monthly_targets.index
        if (period.month - 1) % rebalance_months == 0
    }
    trade_targets = pd.DataFrame(0.0, index=dates, columns=trade_tickers)
    aggregate_targets = pd.DataFrame(index=dates, columns=ASSETS, dtype=float)
    rebalance = pd.Series(False, index=dates)
    held_trade_target: pd.Series | None = None
    held_aggregate_target: pd.Series | None = None
    previous_period = None

    for day in dates:
        period = day.to_period("M")
        scheduled = held_trade_target is None or (
            period != previous_period and period in scheduled_months
        )
        if scheduled:
            desired = desired_daily.loc[day].astype(float)
            target = pd.Series(0.0, index=trade_tickers)
            target.loc[qqq_ticker] += desired["QQQ"]
            target.loc[gld_ticker] += desired["GLD"]
            active_cash = cash_after if cash_after_first is not None and day >= cash_after_first else cash_before
            if active_cash:
                target.loc[active_cash] += desired["SGOV"]
            held_trade_target = target
            held_aggregate_target = desired
            rebalance.loc[day] = True
        trade_targets.loc[day] = held_trade_target
        aggregate_targets.loc[day] = held_aggregate_target
        previous_period = period

    raw_open = pd.DataFrame(index=dates, columns=trade_tickers, dtype=float)
    raw_close = pd.DataFrame(index=dates, columns=trade_tickers, dtype=float)
    prior_close = pd.DataFrame(index=dates, columns=trade_tickers, dtype=float)
    splits = pd.DataFrame(1.0, index=dates, columns=trade_tickers)
    dividends = pd.DataFrame(0.0, index=dates, columns=trade_tickers)
    for ticker in trade_tickers:
        frame = execution_frames[ticker].sort_index()
        reconstructed_open = split_unadjusted_price(frame, "open")
        reconstructed_close = split_unadjusted_price(frame, "close")
        # Before-listing placeholder prices are harmless because the target and
        # holdings for that ticker are zero. They only keep the common engine's
        # input matrix finite until the first actual quote appears.
        raw_open[ticker] = reconstructed_open.reindex(dates).ffill().bfill()
        raw_close[ticker] = reconstructed_close.reindex(dates).ffill().bfill()
        prior_close[ticker] = reconstructed_close.shift(1).reindex(dates).ffill().bfill()
        splits[ticker] = frame.get("split_ratio", pd.Series(1.0, index=frame.index)).reindex(dates).fillna(1.0)
        dividends[ticker] = frame.get("dividend", pd.Series(0.0, index=frame.index)).reindex(dates).fillna(0.0)

    daily_return, actual_weights, turnover, shares, cash = whole_share_open_backtest(
        trade_targets,
        prior_close,
        raw_open,
        raw_close,
        splits,
        dividends,
        cost_bps / 10000.0,
        initial_capital,
    )
    wealth = (1.0 + daily_return).cumprod()
    result = pd.DataFrame(index=dates)
    result.index.name = "Date"
    result["Wealth"] = wealth
    for asset in ASSETS:
        result[f"Target {asset}"] = aggregate_targets[asset]
    result["Actual QQQ"] = actual_weights[qqq_ticker]
    result["Actual GLD"] = actual_weights[gld_ticker]
    cash_columns = [ticker for ticker in (cash_before, cash_after) if ticker]
    nav_value = wealth * initial_capital
    result["Residual Cash"] = cash
    result["Residual Cash Weight"] = cash / nav_value
    cash_asset_weight = actual_weights[cash_columns].sum(axis=1) if cash_columns else 0.0
    # Treat uninvested cash as part of the defensive sleeve. This covers both
    # whole-share rounding and periods before the selected cash ETF existed.
    result["Actual SGOV"] = cash_asset_weight + result["Residual Cash Weight"]
    result["SGOV rank"] = daily_rank.reindex(dates).astype(int)
    result["Rebalance"] = rebalance
    result["Turnover"] = turnover
    for ticker in trade_tickers:
        result[f"Shares {ticker}"] = shares[ticker]

    years = max(((dates[-1] - dates[0]).days + 1) / 365.2425, 1 / 365.2425)
    baseline = pd.concat([
        pd.Series([1.0], index=[dates[0] - pd.Timedelta(nanoseconds=1)]),
        wealth,
    ])
    drawdown = baseline / baseline.cummax() - 1.0
    volatility = float(daily_return.std(ddof=1) * np.sqrt(252.0))
    metrics: dict[str, float | str] = {
        "시작일": str(dates[0].date()),
        "종료일": str(dates[-1].date()),
        "최종 배수": float(wealth.iloc[-1]),
        "CAGR": float(wealth.iloc[-1] ** (1.0 / years) - 1.0),
        "MDD": float(drawdown.min()),
        "연 변동성": volatility,
        "Sharpe": float(daily_return.mean() * 252.0 / volatility) if volatility else np.nan,
        "평균 SGOV 비중": float(result["Actual SGOV"].mean()),
        "리밸런싱 횟수": float(rebalance.sum()),
        "초기자본": float(initial_capital),
        "최종잔여현금": float(cash.iloc[-1]),
    }
    return result, metrics
