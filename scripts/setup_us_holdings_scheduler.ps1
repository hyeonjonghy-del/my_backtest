param(
    [string]$TaskName = "US Holdings Telegram Execution",
    [string]$Time = "06:30",
    [string]$Python = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runner = Join-Path $RepoRoot "scripts\run_us_holdings_execution.cmd"

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Runner not found: $Runner"
}

$Action = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Send separate SOXX/SOXL and QQQ/TQQQ next-open execution messages to Telegram." `
    -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "Schedule: Tuesday-Saturday at $Time KST"
Write-Host "Runner: $Runner"

