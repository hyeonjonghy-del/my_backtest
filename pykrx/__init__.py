"""Small local pykrx compatibility shim for Streamlit Cloud.

The upstream pykrx package can fail during import on Streamlit Cloud because it
initializes KRX auth/session helpers before stock data is requested. The app only
needs ``from pykrx import stock`` and ``stock.get_market_ohlcv_by_date`` for ETF
OHLCV, so this local package provides that narrow API.
"""

from . import stock

__all__ = ["stock"]
