@echo off
title Sniper Command Center
echo Starting Nifty Sniper Web Dashboard...
start "Dashboard UI" cmd /k "python run_dashboard.py"

echo.
echo Dashboard launching at http://localhost:3000
echo Use the Play buttons in the dashboard to start each bot (NIFTY, SENSEX, T1, T2, ...).
echo.
pause
