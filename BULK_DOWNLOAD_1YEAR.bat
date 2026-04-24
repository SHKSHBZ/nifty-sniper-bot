@echo off
REM ============================================================
REM  Bulk-download 1 year of Nifty backtest data from Upstox.
REM    1) Extends Nifty spot + India VIX to 1 year
REM    2) Downloads option chains for every weekly expiry
REM       (ATM +/- 10 strikes, both CE and PE)
REM
REM  Requires: state\upstox_session.json with a fresh access_token
REM            Upstox PLUS plan (for expired-instruments API)
REM
REM  Outputs to: data\
REM  Expected runtime: ~20 minutes
REM  Expected disk: ~240 MB
REM ============================================================

cd /d "%~dp0"

if not exist "state\upstox_session.json" (
    echo.
    echo ERROR: state\upstox_session.json not found.
    echo.
    echo Create that file first with either the raw token on one line, or:
    echo.
    echo   { "access_token": "PASTE_YOUR_FRESH_UPSTOX_TOKEN_HERE" }
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 1/2  Spot + India VIX (1 year, 1-minute bars)
echo ============================================================
python backtesting\download_spot_vix.py --months 12
if errorlevel 1 (
    echo.
    echo Spot/VIX download failed — see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 2/2  Option chains (all weekly expiries, ATM +/- 10)
echo ============================================================
python backtesting\bulk_download.py --months 12 --strike-halfwidth 10
if errorlevel 1 (
    echo.
    echo Option chain download failed — see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  DONE — open GitHub Desktop, commit data\, push.
echo ============================================================
pause
