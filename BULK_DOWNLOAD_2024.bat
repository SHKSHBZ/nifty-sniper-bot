@echo off
REM ============================================================
REM  Downloads calendar year 2024 of Nifty backtest data:
REM    1) Extends spot + India VIX (merges with existing file)
REM    2) Downloads option chains for every weekly expiry in 2024
REM       (ATM +/- 10 strikes, both CE and PE, Tuesday OR Thursday
REM        expiry days handled automatically)
REM
REM  Requires: state\upstox_session.json with a fresh access_token
REM            Upstox PLUS plan (for expired-instruments API)
REM
REM  Outputs to: data\
REM  Expected runtime: ~25 minutes
REM  Expected disk: ~300 MB additional
REM
REM  Safe to re-run: skips files that already exist on disk;
REM  spot/VIX files are merged (existing rows preserved).
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
echo  STEP 1/2  Spot + India VIX for calendar year 2024
echo ============================================================
python backtesting\download_spot_vix.py --from 2024-01-01 --to 2024-12-31
if errorlevel 1 (
    echo.
    echo Spot/VIX download failed -- see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 2/2  Option chains for every weekly expiry in 2024
echo ============================================================
python backtesting\bulk_download.py --from 2024-01-01 --to 2024-12-31 --strike-halfwidth 10
if errorlevel 1 (
    echo.
    echo Option chain download failed -- see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  DONE -- open GitHub Desktop, commit data\, push.
echo  Then ping me to run all phases against 2024 data.
echo ============================================================
pause
