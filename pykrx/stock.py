"""Subset of pykrx.stock backed by FinanceDataReader and yfinance."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd


KOREAN_COLUMNS = {
    "open": "\uc2dc\uac00",
    "high": "\uace0\uac00",
    "low": "\uc800\uac00",
    "close": "\uc885\uac00",
    "volume": "\uac70\ub798\ub7c9",
}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(KOREAN_COLUMNS.values()))


def _normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _empty()

    df = raw.copy()
    df.columns = [str(col).lower() for col in df.columns]
    rename = {
        "adj close": "close",
        "adjclose": "close",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }
    df = df.rename(columns=rename)

    out = pd.DataFrame(index=pd.to_datetime(df.index).tz_localize(None).normalize())
    for source, target in KOREAN_COLUMNS.items():
        out[target] = pd.to_numeric(df[source], errors="coerce") if source in df.columns else pd.NA
    return out.dropna(how="all").sort_index()


def get_market_ohlcv_by_date(start: str, end: str, ticker: str) -> pd.DataFrame:
    """Return OHLCV using the column names expected by pykrx callers.

    Parameters match pykrx: ``start`` and ``end`` are usually ``YYYYMMDD``.
    """

    start_dt = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)
    start_iso = start_dt.strftime("%Y-%m-%d")
    end_iso = end_dt.strftime("%Y-%m-%d")

    try:
        import FinanceDataReader as fdr

        data = _normalize_ohlcv(fdr.DataReader(ticker, start_iso, end_iso))
        if not data.empty:
            return data
    except Exception:
        pass

    try:
        import yfinance as yf

        # yfinance's end date is exclusive.
        yf_end = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        raw = yf.download(f"{ticker}.KS", start=start_iso, end=yf_end, progress=False, auto_adjust=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return _normalize_ohlcv(raw)
    except Exception:
        return _empty()
