param(
    [string]$TaskName = "KODEX Bull Bear Telegram Signal",
    [string]$Time = "15:35",
    [string]$Python = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $RepoRoot "scripts\run_korea_bull_bear_signal.cmd"

if (-not (Test-Path $VenvPython)) {
    & $Python -m venv (Join-Path $RepoRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements.txt")

$Action = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Send KODEX 200 / Leverage ON-OFF v1 target weights to Telegram after KRX close." `
    -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "Schedule: weekdays at $Time"
Write-Host "Runner: $Runner"
