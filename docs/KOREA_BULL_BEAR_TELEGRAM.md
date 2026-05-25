# KODEX Bull/Bear Telegram Signal

This automation runs `scripts/send_korea_bull_bear_signal.py` on weekdays after
the KRX close and sends the v1 target allocation to Telegram.

## Secrets

Use either environment variables:

```powershell
setx TELEGRAM_BOT_TOKEN "123456:your-bot-token"
setx TELEGRAM_CHAT_ID "123456789"
```

Or create `.streamlit/secrets.toml` locally:

```toml
[telegram]
bot_token = "123456:your-bot-token"
chat_id = "123456789"
```

`.streamlit/` is ignored by Git, so this file is not committed.

## Register Windows Task Scheduler

Run this once from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_korea_bull_bear_scheduler.ps1
```

The default task name is `KODEX Bull Bear Telegram Signal`, scheduled for
weekdays at 15:35 Korea time. The script creates `.venv`, installs
`requirements.txt`, and registers the scheduled task.

## Manual Test

After adding Telegram secrets, run:

```powershell
.\scripts\run_korea_bull_bear_signal.cmd
```
