# Registers two Windows Scheduled Tasks:
#
#   1. SniperBot_Dashboard   — daily at 07:00 UAE (08:30 IST), Mon-Fri.
#                              Auto-starts the dashboard so you can hit
#                              the bot Play buttons from your phone.
#
#   2. SniperBot_EODSummary  — daily at 14:05 UAE (15:35 IST), Mon-Fri.
#                              Sends one Telegram message with all 4
#                              bot's P&L for the day.
#
# Run this script ONCE as Administrator from the repo root:
#
#     powershell -ExecutionPolicy Bypass -File .\install_scheduled_tasks.ps1
#
# To remove the tasks later:
#
#     Unregister-ScheduledTask -TaskName "SniperBot_Dashboard" -Confirm:$false
#     Unregister-ScheduledTask -TaskName "SniperBot_EODSummary" -Confirm:$false

$ErrorActionPreference = "Stop"

$repoRoot = (Get-Item -Path $PSScriptRoot).FullName
$startBat = Join-Path $repoRoot "start_dashboard.bat"
$eodPy    = Join-Path $repoRoot "eod_summary.py"

if (-not (Test-Path $startBat)) {
    Write-Error "start_dashboard.bat not found at $startBat. Run this from the repo root."
    exit 1
}
if (-not (Test-Path $eodPy)) {
    Write-Error "eod_summary.py not found at $eodPy."
    exit 1
}

# Resolve python.exe (assumes 'python' is on PATH; otherwise edit below)
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    Write-Error "python.exe not found on PATH. Edit this script with the full path."
    exit 1
}

$weekdays = "Monday","Tuesday","Wednesday","Thursday","Friday"

# Common settings: keep tasks resilient on a personal laptop.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)

# Run only when the user is logged on (so it can reach the GUI / network drive).
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# ---------------------------------------------------------------------------
# Task 1: Dashboard auto-start at 07:00 UAE local (= 08:30 IST)
# ---------------------------------------------------------------------------
Write-Host "==> Registering SniperBot_Dashboard ..."

$dashAction = New-ScheduledTaskAction `
    -Execute $startBat `
    -WorkingDirectory $repoRoot

$dashTrigger = New-ScheduledTaskTrigger `
    -Weekly -DaysOfWeek $weekdays `
    -At "07:00"

# Remove existing version first (idempotent re-install)
Unregister-ScheduledTask -TaskName "SniperBot_Dashboard" -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName "SniperBot_Dashboard" `
    -Description "Auto-starts the Sniper Bot dashboard at 07:00 UAE (08:30 IST). User taps Play per bot from phone." `
    -Action $dashAction `
    -Trigger $dashTrigger `
    -Settings $settings `
    -Principal $principal | Out-Null

Write-Host "    OK"

# ---------------------------------------------------------------------------
# Task 2: EOD Telegram summary at 14:05 UAE local (= 15:35 IST)
# ---------------------------------------------------------------------------
Write-Host "==> Registering SniperBot_EODSummary ..."

$eodAction = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "`"$eodPy`"" `
    -WorkingDirectory $repoRoot

$eodTrigger = New-ScheduledTaskTrigger `
    -Weekly -DaysOfWeek $weekdays `
    -At "14:05"

Unregister-ScheduledTask -TaskName "SniperBot_EODSummary" -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName "SniperBot_EODSummary" `
    -Description "Sends Telegram EOD summary at 14:05 UAE (15:35 IST) - PnL for all 4 bots." `
    -Action $eodAction `
    -Trigger $eodTrigger `
    -Settings $settings `
    -Principal $principal | Out-Null

Write-Host "    OK"

Write-Host ""
Write-Host "==============================================================="
Write-Host "  Two scheduled tasks installed."
Write-Host "    SniperBot_Dashboard    runs Mon-Fri at 07:00 UAE / 08:30 IST"
Write-Host "    SniperBot_EODSummary   runs Mon-Fri at 14:05 UAE / 15:35 IST"
Write-Host ""
Write-Host "  Manage them in Task Scheduler (taskschd.msc) under:"
Write-Host "    Task Scheduler Library -> SniperBot_*"
Write-Host ""
Write-Host "  Test EOD summary right now:"
Write-Host "    python .\eod_summary.py"
Write-Host "==============================================================="
