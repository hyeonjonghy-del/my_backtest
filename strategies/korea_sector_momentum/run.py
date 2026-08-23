from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from pykrx import stock

from strategy import backtest, metrics


def load_monthly_prices(sectors, start: str, end: str) -> pd.DataFrame:
    tickers = sorted({ticker for values in sectors.values() for ticker in values})
    frames = []
    for ticker in tickers:
        daily = stock.get_market_ohlcv_by_date(start.replace("-", ""), end.replace("-", ""), ticker)
        if daily.empty:
            raise RuntimeError(f"No KRX data returned for {ticker}")
        series = daily["종가"].rename(ticker)
        frames.append(series)
    prices = pd.concat(frames, axis=1).sort_index()
    return prices.resample("ME").last().ffill()


def load_kospi200_monthly_prices(start: str, end: str) -> pd.Series:
    """Load KOSPI200 monthly levels, with KODEX 200 as a compatibility proxy."""
    start_text = start.replace("-", "")
    end_text = end.replace("-", "")

    # Some deployed pykrx versions do not expose the index endpoint.
    index_loader = getattr(stock, "get_index_ohlcv_by_date", None)
    if index_loader is not None:
        try:
            daily = index_loader(start_text, end_text, "1028")
            if not daily.empty and "종가" in daily.columns:
                return daily["종가"].resample("ME").last().ffill().rename("KOSPI200")
        except Exception:
            pass

    # KODEX 200 (069500) tracks the KOSPI200 and is available through the
    # stock OHLCV endpoint in older and newer pykrx versions alike.
    daily = stock.get_market_ohlcv_by_date(start_text, end_text, "069500")
    if daily.empty:
        raise RuntimeError("No KOSPI200/KODEX 200 benchmark data returned")
    return daily["종가"].resample("ME").last().ffill().rename("KOSPI200")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output", default="results")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    prices = load_monthly_prices(config["sectors"], args.start, args.end)
    result = backtest(
        prices,
        config["sectors"],
        args.start,
        args.end,
        weights=config["weights"],
        selection_lookback=config["selection_lookback_months"],
        ranking_lookback=config["ranking_lookback_months"],
        downside_lookback=config["downside_lookback_months"],
        transaction_cost=config["transaction_cost"],
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result.monthly.to_csv(output / "monthly_returns.csv", encoding="utf-8-sig")
    result.sector_prices.to_csv(output / "sector_prices.csv", encoding="utf-8-sig")
    print(json.dumps(metrics(result.monthly), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
