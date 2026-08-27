from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from pykrx import stock

try:
    from .strategy import backtest, metrics
except ImportError:  # Direct CLI execution from this directory.
    from strategy import backtest, metrics


def _query_start(start: str) -> str:
    return (pd.Timestamp(start) - pd.DateOffset(months=14)).strftime("%Y%m%d")


def load_hybrid_monthly_prices(sector_specs, start: str, end: str) -> pd.DataFrame:
    """Load proxy stocks and all ETF prices with enough history for 12m signals."""
    tickers = set()
    for spec in sector_specs.values():
        tickers.update(spec["proxy_tickers"])
        tickers.add(spec["etf_ticker"])

    frames = []
    for ticker in sorted(tickers):
        daily = stock.get_market_ohlcv_by_date(_query_start(start), end.replace("-", ""), ticker)
        if daily.empty:
            raise RuntimeError(f"No KRX data returned for {ticker}")
        frames.append(daily["종가"].rename(ticker))
    # Do not forward-fill before an ETF is listed: its missing pre-listing values
    # must remain missing so the proxy return is used.
    return pd.concat(frames, axis=1).sort_index().resample("ME").last()


def load_kospi200_monthly_prices(start: str, end: str) -> pd.Series:
    start_text = _query_start(start)
    end_text = end.replace("-", "")
    try:
        daily = stock.get_index_ohlcv_by_date(start_text, end_text, "1028")
        if not daily.empty:
            return daily["종가"].resample("ME").last().rename("KOSPI200")
    except Exception:
        pass
    daily = stock.get_market_ohlcv_by_date(start_text, end_text, "069500")
    if daily.empty:
        raise RuntimeError("No KOSPI200/KODEX 200 benchmark data returned")
    return daily["종가"].resample("ME").last().rename("KOSPI200")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output", default="results")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    prices = load_hybrid_monthly_prices(config["sector_specs"], args.start, args.end)
    result = backtest(
        prices, config["sector_specs"], args.start, args.end,
        weights=config["weights"],
        selection_lookback=config["selection_lookback_months"],
        ranking_lookback=config["ranking_lookback_months"],
        etf_transition_start=config["etf_transition_start"],
        transaction_cost=config["transaction_cost"],
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result.monthly.to_csv(output / "monthly_returns.csv", encoding="utf-8-sig")
    result.sector_prices.to_csv(output / "hybrid_sector_prices.csv", encoding="utf-8-sig")
    result.sector_sources.to_csv(output / "sector_return_sources.csv", encoding="utf-8-sig")
    print(json.dumps(metrics(result.monthly), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
