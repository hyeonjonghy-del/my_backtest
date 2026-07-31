# Strategy execution alerts

The existing Telegram bot and chat are reused. No order is ever submitted by
these scripts; they only read each Kiwoom account and send whole-share
instructions.

## Notifications

- KODEX base and aggressive strategies: two separate messages on weekdays at
  15:35 KST. Each contains current holdings, full target holdings, the 70%
  after-hours closing-price order, and the remaining 30% next-open order.
- SOXX/SOXL and QQQ/TQQQ Holdings V2: two separate messages Tuesday-Saturday
  at 06:30 KST. Each contains its account's USD cash, current shares, target
  shares, and exact next-regular-open orders.

The US schedule deliberately uses Korean calendar days Tuesday-Saturday
because it follows the preceding US trading session.

## Install or refresh Windows tasks

Run PowerShell from the repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_korea_bull_bear_scheduler.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\setup_us_holdings_scheduler.ps1
```

Test the US calculation without sending Telegram:

```powershell
python .\scripts\send_us_holdings_execution.py --dry-run
```

Logs are written under `data/logs`.

