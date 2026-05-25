@echo off
setlocal

cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\send_korea_bull_bear_signal.py"
  exit /b %ERRORLEVEL%
)

"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "scripts\send_korea_bull_bear_signal.py"
exit /b %ERRORLEVEL%
