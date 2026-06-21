"""USD / SOXL trend and volatility-target backtest page.

This page reuses the SOXX/SOXL v5 implementation and adapts it at runtime
so USD is the base signal and defensive sleeve while SOXL is the higher-gear
allocation. Because USD is a 2x semiconductor ETF and SOXL is 3x, SOXL is
modeled as 1.5x USD-equivalent risk.
"""

from __future__ import annotations

from pathlib import Path


SOURCE_PAGE = Path(__file__).with_name("5_soxx_soxl_vol_target_app_v5.py")

code = SOURCE_PAGE.read_text(encoding="utf-8")

code = code.replace(
    '"""SOXX / SOXL trend and volatility-target backtest v5."""',
    '"""USD / SOXL trend and volatility-target backtest v1."""',
)
code = code.replace(
    'SOXX = "SOXX"\nSOXL = "SOXL"',
    'USD = "USD"\nSOXL = "SOXL"\nSOXL_LEVERAGE = 1.5',
)
code = code.replace("SOXX", "USD")
code = code.replace("soxx", "usd")
code = code.replace(
    "Default: Strong Bull uses SOXL tactically, Weak Bull shifts toward USD, "
    "and deep-drawdown turnarounds stay active until short-term momentum breaks",
    "Default: USD is the base trend signal, Weak Bull stays mostly in USD, "
    "and Strong Bull adds SOXL as the higher-gear sleeve",
)
code = code.replace(
    'page_title="USD/SOXL Vol Target Backtest V5"',
    'page_title="USD/SOXL Vol Target Backtest V1"',
)
code = code.replace(
    "USD / SOXL Volatility Target Backtest V5",
    "USD / SOXL Volatility Target Backtest V1",
)
code = code.replace("/ 3).clip", "/ SOXL_LEVERAGE).clip")
code = code.replace("/ 3, 0.0)", "/ SOXL_LEVERAGE, 0.0)")
code = code.replace("* 3\n", "* SOXL_LEVERAGE\n")
code = code.replace("* 3\r\n", "* SOXL_LEVERAGE\r\n")
code = code.replace("1.5f}x USD-equivalent risk", "1.1f}x USD-equivalent risk")
code = code.replace("Loading USD/SOXL data...", "Loading USD/SOXL data...")

exec(compile(code, str(SOURCE_PAGE), "exec"), {"__file__": str(SOURCE_PAGE), "__name__": "__main__"})
