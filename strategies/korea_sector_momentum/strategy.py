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
        available = [t for t in tickers if t in monthly_prices.columns]
        if len(available) != len(tickers):
            raise ValueError(f"Missing price columns for {sector}: {set(tickers) - set(available)}")
        sector_returns[sector] = monthly_prices[available].pct_change().mean(axis=1)

    returns = pd.DataFrame(sector_returns).dropna(how="any")
    return (1.0 + returns).cumprod() * 100.0


def _ranked_sectors(prices: pd.DataFrame, at: pd.Timestamp, lookback: int) -> List[str]:
    if at not in prices.index:
        raise KeyError(f"Signal date not found: {at}")
    pos = prices.index.get_loc(at)
    if pos < lookback:
        raise ValueError("Not enough history for momentum calculation")
    base = prices.iloc[pos - lookback]
    scores = prices.loc[at] / base - 1.0
    return list(scores.sort_values(ascending=False).index)


def backtest(
    monthly_prices: pd.DataFrame,
    sectors: Mapping[str, Sequence[str]],
    start: str,
    end: str,
    weights: Iterable[float] = (0.45, 0.30, 0.15, 0.05, 0.05),
    selection_lookback: int = 12,
    ranking_lookback: int = 12,
    downside_lookback: int = 6,
    transaction_cost: float = 0.001,
) -> BacktestResult:
    weights = np.asarray(list(weights), dtype=float)
    if len(weights) != 5 or not np.isclose(weights.sum(), 1.0):
        raise ValueError("weights must contain five values summing to 1")

    prices = make_sector_prices(monthly_prices, sectors)
    months = prices.index
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    prev_weights = pd.Series(0.0, index=prices.columns)
    selected: List[str] = []
    rows = []

    for i in range(max(selection_lookback, ranking_lookback, downside_lookback) + 1, len(months)):
        month = months[i]
        if month < start_ts or month > end_ts:
            continue
        signal_month = months[i - 1]

        # Selection is made at the previous December and applied for the year.
        if month.month == 1:
            annual_rank = _ranked_sectors(prices, signal_month, selection_lookback)
            selected = annual_rank[:5]

        ranked = _ranked_sectors(prices, signal_month, ranking_lookback)
        ranked = [s for s in ranked if s in selected]
        target = pd.Series(0.0, index=prices.columns)
        for rank, sector in enumerate(ranked):
            target.loc[sector] = weights[rank]

        downside_pos = prices.index.get_loc(signal_month)
        downside_base = prices.iloc[downside_pos - downside_lookback]
        downside_return = prices.loc[signal_month] / downside_base - 1.0
        for sector in selected:
            if downside_return.loc[sector] <= 0.0:
                target.loc[sector] = 0.0

        monthly_returns = prices.loc[month] / prices.loc[signal_month] - 1.0
        turnover = (target - prev_weights).abs().sum()
        strategy_return = float((target * monthly_returns).sum() - transaction_cost * turnover)
        rows.append({
            "date": month,
            "return": strategy_return,
            "turnover": turnover,
            "selected": ",".join(selected),
            "ranked": ",".join(ranked),
            **{f"weight_{s}": target.loc[s] for s in prices.columns},
        })
        prev_weights = target

    monthly = pd.DataFrame(rows).set_index("date")
    monthly["wealth"] = (1.0 + monthly["return"]).cumprod()
    return BacktestResult(monthly=monthly, sector_prices=prices)


def simulate_cash_rules(
    monthly_prices: pd.DataFrame,
    sectors: Mapping[str, Sequence[str]],
    start: str,
    end: str,
    weights: Iterable[float] = (0.45, 0.30, 0.15, 0.05, 0.05),
    selection_lookback: int = 12,
    ranking_lookback: int = 12,
    transaction_cost: float = 0.001,
) -> Dict[str, pd.DataFrame]:
    """Simulation-only comparison of two absolute-momentum cash rules.

    The production backtest() above is unchanged. These variants replace its
    six-month downside filter only for comparison:
    - negative_cash: each selected sector with negative 12-month momentum keeps
      its rank weight in cash.
    - cash_as_asset: cash has a 0% score and competes for the five rank slots.
    """
    weights = np.asarray(list(weights), dtype=float)
    if len(weights) != 5 or not np.isclose(weights.sum(), 1.0):
        raise ValueError("weights must contain five values summing to 1")

    prices = make_sector_prices(monthly_prices, sectors)
    months = prices.index
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    def run_variant(mode: str) -> pd.DataFrame:
        previous = pd.Series(0.0, index=prices.columns)
        selected: List[str] = []
        rows = []

        for i in range(max(selection_lookback, ranking_lookback) + 1, len(months)):
            month = months[i]
            if month < start_ts or month > end_ts:
                continue
            signal_month = months[i - 1]

            if month.month == 1:
                selected = _ranked_sectors(prices, signal_month, selection_lookback)[:5]

            signal_pos = prices.index.get_loc(signal_month)
            base = prices.iloc[signal_pos - ranking_lookback]
            scores = prices.loc[signal_month] / base - 1.0
            selected_scores = scores.loc[selected].sort_values(ascending=False)

            target = pd.Series(0.0, index=prices.columns)
            if mode == "negative_cash":
                for rank, sector in enumerate(selected_scores.index):
                    if selected_scores.loc[sector] > 0.0:
                        target.loc[sector] = weights[rank]
            else:
                candidates = {sector: float(selected_scores.loc[sector]) for sector in selected}
                candidates["현금"] = 0.0
                ranked_candidates = sorted(candidates, key=candidates.get, reverse=True)
                for rank, candidate in enumerate(ranked_candidates[:5]):
                    if candidate != "현금":
                        target.loc[candidate] = weights[rank]

            monthly_returns = prices.loc[month] / prices.loc[signal_month] - 1.0
            turnover = (target - previous).abs().sum()
            strategy_return = float((target * monthly_returns).sum() - transaction_cost * turnover)
            rows.append({
                "date": month,
                "return": strategy_return,
                "turnover": turnover,
                **{f"weight_{sector}": target.loc[sector] for sector in prices.columns},
            })
            previous = target

        result = pd.DataFrame(rows).set_index("date")
        result["wealth"] = (1.0 + result["return"]).cumprod()
        return result

    return {
        "12m negative -> cash": run_variant("negative_cash"),
        "cash ranked at 0%": run_variant("cash_as_asset"),
    }


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
