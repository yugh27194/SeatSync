@echo off
chcp 65001 >nul
REM SeatSync를 외부 링크(https://....trycloudflare.com)로 공유한다. 가입 불필요, 이 창을 닫으면 공유가 끝난다.
cd /d "%~dp0"
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo [안내] cloudflared가 설치되어 있지 않습니다. 아래 명령으로 설치한 뒤 cmd 창을 새로 열어 다시 실행하세요.
  echo        winget install --id Cloudflare.cloudflared
  pause
  exit /b 1
)
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
start "SeatSync 서버" cmd /k python manage.py serve --port 5000
timeout /t 4 /nobreak >nul
echo.
echo ================================================================
echo  잠시 후 아래에 나오는  https://xxxx.trycloudflare.com  주소를
echo  다른 사람에게 보내면 됩니다.  (끄려면 이 창에서 Ctrl+C)
echo ================================================================
echo.
cloudflared tunnel --url http://localhost:5000
