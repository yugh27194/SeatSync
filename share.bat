@echo off
REM SeatSync: share this PC's server via a Cloudflare quick tunnel (https://....trycloudflare.com)
REM Messages are in English on purpose: Korean text in .bat files breaks in some cmd code pages.
cd /d "%~dp0"
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo [!] cloudflared is not installed. Install it, open a NEW cmd window, and run share.bat again:
  echo     winget install --id Cloudflare.cloudflared
  pause
  exit /b 1
)
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
start "SeatSync server" cmd /k python manage.py serve --port 5000
timeout /t 4 /nobreak >nul
echo.
echo ================================================================
echo   Share the https://xxxx.trycloudflare.com address shown below.
echo   Keep this window and the "SeatSync server" window open.
echo   Press Ctrl+C here to stop sharing.
echo ================================================================
echo.
cloudflared tunnel --url http://localhost:5000
