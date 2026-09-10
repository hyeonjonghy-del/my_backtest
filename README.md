# my_backtest

Streamlit workspace for the strategies that remain in active use, reference, or monitoring.

## Run

```powershell
pip install -r requirements.txt
streamlit run main.py
```

## Strategy set

| Decision | Strategy | Role |
| --- | --- | --- |
| Execute | KOSPI200 Bull/Bear | Korea core |
| Reference | KOSPI200 Bull/Bear v1 (Aggressive) | Korea comparison |
| Monitor | Samsung Electronics Trend / Leverage | Wait for more live leveraged-ETF history |
| Execute | S&P500 Momentum | US stock-selection core |
| Reference | S&P500 Bull/Bear | US regime comparison |
| Execute | SOXX / SOXL Vol Target | Semiconductor strategy |
| Reference | SOXX Vol Target | Unleveraged semiconductor comparison |
| Execute | QQQ / TQQQ Holdings | Nasdaq growth strategy |
| Keep | QQQ / Gold / SGOV Momentum | Multi-asset allocation |
| Review | US Integrated Strategy | Retained for further evaluation |

Only strategies marked **Execute** are intended to receive capital. Reference, Monitor, and Review pages remain available for comparison without a separate allocation.

## Execution alerts

The scripts under `scripts/` read configured Kiwoom accounts and send Telegram instructions. They do not submit orders automatically. See `docs/EXECUTION_TELEGRAM_ALERTS.md` and `docs/KOREA_BULL_BEAR_TELEGRAM.md`.
