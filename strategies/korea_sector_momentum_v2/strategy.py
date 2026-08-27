from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    monthly: pd.DataFrame
    sector_prices: pd.DataFrame
    sector_sources: pd.DataFrame


def make_hybrid_sector_prices(
    monthly_prices: pd.DataFrame,
    sector_specs: Mapping[str, Mapping[str, object]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create continuous sector indices from stock proxies then live ETF returns.

    A sector uses the equal-weight return of its two representative stocks until
    the first full calendar month after its ETF listing. From that month onward
    it uses the ETF closing-price return. Indices are linked by returns, never
    by raw price levels, so an ETF's different price unit cannot create a jump.
    """
    sector_returns: Dict[str, pd.Series] = {}
    sector_sources: Dict[str, pd.Series] = {}

    for sector, spec in sector_specs.items():
        proxy_tickers = list(spec["proxy_tickers"])
        etf_ticker = str(spec["etf_ticker"])
        first_etf_month = pd.Timestamp(str(spec["first_etf_return_month"]))

        missing = [ticker for ticker in [*proxy_tickers, etf_ticker] if ticker not in monthly_prices]
        if missing:
            raise ValueError(f"Missing price columns for {sector}: {missing}")

        proxy_return = monthly_prices[proxy_tickers].pct_change().mean(axis=1)
        etf_return = monthly_prices[etf_ticker].pct_change()
        use_etf = (monthly_prices.index >= first_etf_month) & etf_return.notna()

        linked_return = proxy_return.where(~use_etf, etf_return)
        sector_returns[sector] = linked_return
        sector_sources[sector] = pd.Series(
            np.where(use_etf, str(spec["etf_name"]), "대표종목 프록시"),
            index=monthly_prices.index,
        )

    returns = pd.DataFrame(sector_returns).dropna(how="any")
    sources = pd.DataFrame(sector_sources).reindex(returns.index)
    return (1.0 + returns).cumprod() * 100.0, sources


def _momentum_scores(prices: pd.DataFrame, at: pd.Timestamp, lookback: int) -> pd.Series:
    position = prices.index.get_loc(at)
    if position < lookback:
        raise ValueError("Not enough history for momentum calculation")
    return prices.loc[at] / prices.iloc[position - lookback] - 1.0


def backtest(
    monthly_prices: pd.DataFrame,
    sector_specs: Mapping[str, Mapping[str, object]],
    start: str,
    end: str,
    weights: Iterable[float] = (0.45, 0.30, 0.15, 0.05, 0.05),
    selection_lookback: int = 12,
    ranking_lookback: int = 12,
    transaction_cost: float = 0.001,
) -> BacktestResult:
    weights = np.asarray(list(weights), dtype=float)
    if len(weights) != 5 or not np.isclose(weights.sum(), 1.0):
        raise ValueError("weights must contain five values summing to 1")

    prices, sources = make_hybrid_sector_prices(monthly_prices, sector_specs)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    previous_weights = pd.Series(0.0, index=prices.columns)
    selected: list[str] = []
    rows = []

    for index in range(max(selection_lookback, ranking_lookback) + 1, len(prices.index)):
        month = prices.index[index]
        if month < start_ts or month > end_ts:
            continue

        signal_month = prices.index[index - 1]
        if not selected or month.month == 1:
            selected = list(_momentum_scores(prices, signal_month, selection_lookback).nlargest(5).index)

        scores = _momentum_scores(prices, signal_month, ranking_lookback).loc[selected]
        candidates = {sector: float(scores[sector]) for sector in selected}
        candidates["현금"] = 0.0
        ranked = sorted(candidates, key=candidates.get, reverse=True)[:5]

        target = pd.Series(0.0, index=prices.columns)
        for rank, asset in enumerate(ranked):
            if asset != "현금":
                target.loc[asset] = weights[rank]

        realized = prices.loc[month] / prices.loc[signal_month] - 1.0
        turnover = float((target - previous_weights).abs().sum())
        strategy_return = float((target * realized).sum() - transaction_cost * turnover)
        rows.append({
            "date": month,
            "return": strategy_return,
            "turnover": turnover,
            "selected": ",".join(selected),
            "ranked": ",".join(ranked),
            "weight_현금": float(1.0 - target.sum()),
            **{f"weight_{sector}": target.loc[sector] for sector in prices.columns},
        })
        previous_weights = target

    monthly = pd.DataFrame(rows).set_index("date")
    monthly["wealth"] = (1.0 + monthly["return"]).cumprod()
    return BacktestResult(monthly=monthly, sector_prices=prices, sector_sources=sources)


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
