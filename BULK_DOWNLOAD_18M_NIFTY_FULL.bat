@echo off
REM ============================================================
REM  18-MONTH NIFTY FULL DATA PACK
REM
REM  Downloads everything needed by the multi-pillar confluence
REM  backtester (per nifty_trading_system_config.json):
REM
REM    1. NIFTY 50 spot 1-min OHLCV (18 months)
REM    2. India VIX 1-min (18 months)
REM    3. NIFTY monthly futures with VOLUME (last 18 contracts)
REM       -> required for Volume Profile / HVN / LVN / POC
REM    4. Stitched continuous front-month futures series
REM    5. NIFTY weekly option chains, ATM +/- 10 strikes (18 months)
REM       -> required for execution simulation in backtest
REM
REM  Auth:    state\upstox_session.json (created by `python upstox_auth.py`)
REM  Output:  data\
REM  Time:    ~60-80 minutes (rate-limited; mostly waiting on API)
REM  Disk:    ~400 MB
REM  Resume:  all four scripts skip files already on disk
REM ============================================================

cd /d "%~dp0"

if not exist "state\upstox_session.json" (
    echo.
    echo ERROR: state\upstox_session.json not found.
    echo.
    echo Run this first to log in:
    echo     python upstox_auth.py
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 1/4  Spot + India VIX (18 months, 1-min bars)
echo ============================================================
python backtesting\download_spot_vix.py --months 18
if errorlevel 1 (
    echo.
    echo Spot/VIX download failed - see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 2/4  NIFTY monthly futures with VOLUME (last 18 contracts)
echo ============================================================
python -m backtesting.nifty_futures_downloader --months 18
if errorlevel 1 (
    echo.
    echo Futures download failed - see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 3/4  Stitch futures into continuous front-month series
echo ============================================================
python backtesting\nifty_futures_continuous.py
if errorlevel 1 (
    echo.
    echo Futures stitching failed - see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 4/4  Weekly option chains, ATM +/- 10 (18 months)
echo ============================================================
python backtesting\bulk_download.py --months 18 --strike-halfwidth 10
if errorlevel 1 (
    echo.
    echo Option chain download failed - see error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  DONE - data\ now contains the full 18-month NIFTY pack.
echo  Check row counts match docs\data_manifest.json expectations.
echo ============================================================
pause
