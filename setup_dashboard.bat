@echo off
REM One-time setup for the dashboard on Windows.
REM Double-click this file, OR type setup_dashboard.bat in Command Prompt.

setlocal

cd /d "%~dp0"

echo.
echo ===============================================================
echo  Setup: Nifty Sniper Dashboard
echo ===============================================================
echo.

echo [1/3] Checking Python...
python --version
if errorlevel 1 (
  echo.
  echo ERROR: Python not found. Install Python 3.9+ from python.org and try again.
  echo During install, TICK the box "Add Python to PATH".
  pause
  exit /b 1
)

echo.
echo [2/3] Installing Python packages (this can take 1-2 minutes)...
python -m pip install --upgrade pip
python -m pip install fastapi uvicorn psutil python-dotenv pytz requests pandas numpy upstox-python-sdk
if errorlevel 1 (
  echo.
  echo ERROR: pip install failed. Scroll up to see why.
  pause
  exit /b 1
)

echo.
echo [3/3] Checking Node.js and installing dashboard packages...
node --version
if errorlevel 1 (
  echo.
  echo ERROR: Node.js not found. Install Node 18+ from nodejs.org and try again.
  pause
  exit /b 1
)

cd dashboard
call npm install
if errorlevel 1 (
  echo.
  echo ERROR: npm install failed. Scroll up to see why.
  pause
  exit /b 1
)
cd ..

echo.
echo ===============================================================
echo   Setup complete!
echo   Now run:  start_dashboard.bat
echo ===============================================================
echo.
pause
