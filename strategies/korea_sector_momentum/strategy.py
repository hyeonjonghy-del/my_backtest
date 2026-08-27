from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    monthly: pd.DataFrame
    sector_prices: pd.DataFrame


def make_sector_prices(
    monthly_prices: pd.DataFrame,
    sectors: Mapping[str, Sequence[str]],
) -> pd.DataFrame:
    """Build equal-weight sector indices from monthly stock prices."""
    sector_returns = {}
    for sector, tickers in sectors.items():
        available = [ticker for ticker in tickers if ticker in monthly_prices.columns]
        if len(available) != len(tickers):
            raise ValueError(f"Missing price columns for {sector}: {set(tickers) - set(available)}")
        sector_returns[sector] = monthly_prices[available].pct_change().mean(axis=1)

    returns = pd.DataFrame(sector_returns).dropna(how="any")
    return (1.0 + returns).cumprod() * 100.0


def _momentum_scores(prices: pd.DataFrame, at: pd.Timestamp, lookback: int) -> pd.Series:
    if at not in prices.index:
        raise KeyError(f"Signal date not found: {at}")
    position = prices.index.get_loc(at)
    if position < lookback:
        raise ValueError("Not enough history for momentum calculation")
    return prices.loc[at] / prices.iloc[position - lookback] - 1.0


def backtest(
    monthly_prices: pd.DataFrame,
    sectors: Mapping[str, Sequence[str]],
    start: str,
    end: str,
    weights: Iterable[float] = (0.45, 0.30, 0.15, 0.05, 0.05),
    selection_lookback: int = 12,
    ranking_lookback: int = 12,
    transaction_cost: float = 0.001,
) -> BacktestResult:
    """Run the final Korea-sector momentum strategy.

    Each January, select the five sectors with the best 12-month momentum.
    Each month, rank those sectors and cash (fixed return score of 0%) together.
    The top five rank slots receive the configured weights; a cash slot remains
    uninvested. No separate 6-month downside filter is used.
    """
    weights = np.asarray(list(weights), dtype=float)
    if len(weights) != 5 or not np.isclose(weights.sum(), 1.0):
        raise ValueError("weights must contain five values summing to 1")

    prices = make_sector_prices(monthly_prices, sectors)
    months = prices.index
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    previous_weights = pd.Series(0.0, index=prices.columns)
    selected: List[str] = []
    rows = []

    for index in range(max(selection_lookback, ranking_lookback) + 1, len(months)):
        month = months[index]
        if month < start_ts or month > end_ts:
            continue
        signal_month = months[index - 1]

        if not selected or month.month == 1:
            annual_scores = _momentum_scores(prices, signal_month, selection_lookback)
            selected = list(annual_scores.sort_values(ascending=False).index[:5])

        ranking_scores = _momentum_scores(prices, signal_month, ranking_lookback).loc[selected]
        candidates = {sector: float(ranking_scores.loc[sector]) for sector in selected}
        candidates["현금"] = 0.0
        ranked_candidates = sorted(candidates, key=candidates.get, reverse=True)

        target = pd.Series(0.0, index=prices.columns)
        for rank, candidate in enumerate(ranked_candidates[:5]):
            if candidate != "현금":
                target.loc[candidate] = weights[rank]

        monthly_returns = prices.loc[month] / prices.loc[signal_month] - 1.0
        turnover = (target - previous_weights).abs().sum()
        strategy_return = float((target * monthly_returns).sum() - transaction_cost * turnover)
        rows.append({
            "date": month,
            "return": strategy_return,
            "turnover": turnover,
            "selected": ",".join(selected),
            "ranked": ",".join(ranked_candidates[:5]),
            "weight_현금": float(1.0 - target.sum()),
            **{f"weight_{sector}": target.loc[sector] for sector in prices.columns},
        })
        previous_weights = target

    monthly = pd.DataFrame(rows).set_index("date")
    monthly["wealth"] = (1.0 + monthly["return"]).cumprod()
    return BacktestResult(monthly=monthly, sector_prices=prices)


def metrics(monthly: pd.DataFrame) -> Dict[str, float]:
    returns = monthly["return"].dropna()
    wealth = (1.0 + returns).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    years = len(returns) / 12.0
    return {
        "cumulative_return": float(wealth.iloc[-1] - 1.0),
        "cagr": float(wealth.iloc[-1] ** (1.0 / years) - 1.0),
        "annualized_volatility": float(returns.std(ddof=1) * np.sqrt(12.0)),
        "max_drawdown": float(drawdown.min()),
        "win_rate": float((returns > 0).mean()),
    }
