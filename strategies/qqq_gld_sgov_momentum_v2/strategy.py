from __future__ import annotations

import numpy as np
import pandas as pd


ASSETS = ("QQQ", "GLD", "SGOV")


def _sgov_rank(scores: pd.Series) -> int:
    tie_order = ("SGOV", "GLD", "QQQ")
    ranked = sorted(tie_order, key=lambda asset: -float(scores[asset]))
    return ranked.index("SGOV") + 1


def make_monthly_targets(
    prices: pd.DataFrame,
    momentum_months: int = 12,
    strong_asset_weight: float = 0.80,
    cash_rank2_weight: float = 0.20,
    cash_rank1_weight: float = 0.40,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return month-end momentum and targets for the next holding month.

    QQQ and GLD always split the non-SGOV allocation in an 80:20 ratio.
    SGOV receives 0%, 20%, or 40% when it ranks third, second, or first.
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
    if not 0.0 <= cash_rank2_weight <= cash_rank1_weight < 1.0:
        raise ValueError("cash weights must satisfy 0 <= rank2 <= rank1 < 1")

    month_end = prices.loc[:, list(ASSETS)].resample("ME").last()
    momentum = month_end.pct_change(momentum_months, fill_method=None)
    targets = pd.DataFrame(np.nan, index=month_end.index, columns=ASSETS)

    for signal_date, scores in momentum.dropna(how="any").iterrows():
        cash_rank = _sgov_rank(scores)
        cash_weight = {1: cash_rank1_weight, 2: cash_rank2_weight, 3: 0.0}[cash_rank]

        risky_ranked = sorted(("GLD", "QQQ"), key=lambda asset: -float(scores[asset]))
        risky_weight = 1.0 - cash_weight
        row = pd.Series(0.0, index=ASSETS)
        row.loc[risky_ranked[0]] = risky_weight * strong_asset_weight
        row.loc[risky_ranked[1]] = risky_weight * (1.0 - strong_asset_weight)
        row.loc["SGOV"] = cash_weight
        targets.loc[signal_date] = row

    return momentum, targets


def backtest(
    prices: pd.DataFrame,
    rebalance_months: int = 1,
    momentum_months: int = 12,
    cost_bps: float = 10.0,
) -> tuple[pd.DataFrame, dict[str, float | str]]:
    if rebalance_months not in (1, 3, 6, 12):
        raise ValueError("rebalance_months must be one of 1, 3, 6, or 12")
    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")

    clean_prices = prices.loc[:, list(ASSETS)].sort_index().dropna(how="any")
    if clean_prices.empty:
        raise ValueError("No common QQQ/GLD/SGOV price history is available")

    momentum, signal_targets = make_monthly_targets(clean_prices, momentum_months)
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
