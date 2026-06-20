"""SOXX / USD 2x trend and volatility-target backtest page.

This page reuses the SOXX/SOXL v5 implementation and adapts it at runtime
from SOXL, a 3x semiconductor ETF, to USD, a 2x semiconductor ETF.
"""

from __future__ import annotations

from pathlib import Path


SOURCE_PAGE = Path(__file__).with_name("5_soxx_soxl_vol_target_app_v5.py")

code = SOURCE_PAGE.read_text(encoding="utf-8")
code = code.replace(
    '"""SOXX / SOXL trend and volatility-target backtest v5."""',
    '"""SOXX / USD 2x trend and volatility-target backtest v1."""',
)
code = code.replace('SOXL = "SOXL"', 'USD = "USD"\nLEVERAGE = 2.0')
code = code.replace("SOXL", "USD")
code = code.replace(
    'page_title="SOXX/USD Vol Target Backtest V5"',
    'page_title="SOXX/USD 2x Vol Target Backtest V1"',
)
code = code.replace(
    "SOXX / USD Volatility Target Backtest V5",
    "SOXX / USD 2x Volatility Target Backtest V1",
)
code = code.replace(
    "Default: Strong Bull uses USD tactically",
    "Default: Strong Bull uses USD (2x) tactically",
)
code = code.replace("/ 3).clip", "/ LEVERAGE).clip")
code = code.replace("/ 3, 0.0)", "/ LEVERAGE, 0.0)")
code = code.replace("* 3\n", "* LEVERAGE\n")
code = code.replace("* 3\r\n", "* LEVERAGE\r\n")
code = code.replace("Loading SOXX/USD data...", "Loading SOXX/USD 2x data...")

exec(compile(code, str(SOURCE_PAGE), "exec"), {"__file__": str(SOURCE_PAGE), "__name__": "__main__"})
