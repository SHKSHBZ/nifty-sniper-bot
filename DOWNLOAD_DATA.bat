@echo off
REM ============================================================
REM  Downloads Nifty spot + India VIX 1-minute candles from Upstox
REM  Requires: state\upstox_session.json with a fresh access_token
REM  Output:   data\NIFTY50_INDEX_1minute.csv
REM            data\INDIA_VIX_1minute.csv
REM ============================================================

cd /d "%~dp0"

if not exist "state\upstox_session.json" (
    echo.
    echo ERROR: state\upstox_session.json not found.
    echo.
    echo Create that file with this content first:
    echo.
    echo {
    echo   "access_token": "PASTE_YOUR_FRESH_UPSTOX_TOKEN_HERE"
    echo }
    echo.
    pause
    exit /b 1
)

python backtesting\download_spot_vix.py --months 1
if errorlevel 1 (
    echo.
    echo Download failed. See error above.
    pause
    exit /b 1
)

echo.
echo Done. Now open GitHub Desktop, commit the new files under data\, and push.
pause
