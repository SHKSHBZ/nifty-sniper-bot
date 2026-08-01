# install_data_collectors.ps1
# Creates Windows scheduled tasks that auto-start the data collectors
# at 9:00 AM IST every trading day (Mon-Fri).
# They run independently of the bot - data flows regardless of bot state.

$venvPython = "$PSScriptRoot\.venv\Scripts\python.exe"
$scriptDir = $PSScriptRoot

# Remove old tasks if they exist
Unregister-ScheduledTask -TaskName "NiftySniper-DataCollector-NIFTY" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "NiftySniper-DataCollector-SENSEX" -Confirm:$false -ErrorAction SilentlyContinue

# ── NIFTY Data Collector ──
$niftyAction = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument "-u `"$scriptDir\data_collector.py`" NIFTY" `
    -WorkingDirectory $scriptDir

$niftyTrigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "09:00" `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday

$niftySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 7)

Register-ScheduledTask `
    -TaskName "NiftySniper-DataCollector-NIFTY" `
    -Action $niftyAction `
    -Trigger $niftyTrigger `
    -Settings $niftySettings `
    -Description "Auto-starts NIFTY option chain data collector at 9:00 AM IST" `
    -Force

Write-Host "[OK] NIFTY data collector scheduled (9:00 AM daily)" -ForegroundColor Green

# ── SENSEX Data Collector ──
$sensexAction = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument "-u `"$scriptDir\data_collector.py`" SENSEX" `
    -WorkingDirectory $scriptDir

$sensexTrigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "09:00" `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday

Register-ScheduledTask `
    -TaskName "NiftySniper-DataCollector-SENSEX" `
    -Action $sensexAction `
    -Trigger $sensexTrigger `
    -Settings $niftySettings `
    -Description "Auto-starts SENSEX option chain data collector at 9:00 AM IST" `
    -Force

Write-Host "[OK] SENSEX data collector scheduled (9:00 AM daily)" -ForegroundColor Green
Write-Host ""
Write-Host "Both data collectors will auto-start at 9:00 AM IST on trading days."
Write-Host "They run independently - bots can crash, data keeps flowing."
Write-Host ""
Write-Host "To start them NOW (manual):"
Write-Host "  .venv\Scripts\python.exe data_collector.py NIFTY"
Write-Host "  .venv\Scripts\python.exe data_collector.py SENSEX"
