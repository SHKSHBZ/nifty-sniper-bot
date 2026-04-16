@echo off
title Sniper Command Center
echo Starting Nifty Sniper Web Dashboard...
start "Dashboard UI" cmd /k "python run_dashboard.py"

echo Starting Sensex Scalper Node...
start "Sensex Scalper Bot (Live)" cmd /k "python scalper_main.py config_sensex.json"

echo "Command Center launched! Both scripts are opening in separate windows."
echo "You can view the scalper's log directly on the dashboard or in the new window."
pause
