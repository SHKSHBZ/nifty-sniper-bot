@echo off
REM Start the Nifty Sniper Dashboard on Windows.
REM Opens two separate windows: one for the backend, one for the frontend.
REM Close either window to stop that side.
REM
REM Double-click this file, OR type start_dashboard.bat in Command Prompt.

setlocal

cd /d "%~dp0"

REM --- Pre-flight checks ---
python -c "import fastapi" 2>nul
if errorlevel 1 (
  echo ERROR: fastapi not installed. Run setup_dashboard.bat first.
  pause
  exit /b 1
)

if not exist "dashboard\node_modules" (
  echo ERROR: dashboard\node_modules missing. Run setup_dashboard.bat first.
  pause
  exit /b 1
)

echo.
echo ===============================================================
echo   Starting Nifty Sniper Dashboard
echo ===============================================================
echo.
echo  Backend will open in a new window on http://localhost:8000
echo  Frontend will open in a new window on http://localhost:3000
echo.
echo  Close either window to stop that side.
echo.
echo  Once both are running, open in your browser:
echo      http://localhost:3000
echo.
echo ===============================================================
echo.

REM --- Start backend in its own window ---
start "Sniper Backend" cmd /k "cd /d ""%~dp0"" && python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000"

REM Give backend ~3 seconds to come up before starting frontend
timeout /t 3 /nobreak >nul

REM --- Start frontend in its own window ---
start "Sniper Frontend" cmd /k "cd /d ""%~dp0\dashboard"" && npx next dev -p 3000"

echo Both servers are starting in separate windows.
echo Wait ~10 seconds, then open http://localhost:3000 in your browser.
echo.
pause
