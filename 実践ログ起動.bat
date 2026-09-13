@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found in PATH.
    pause
    exit /b 1
)

if not exist "tools\practice_log_server.py" (
    echo ERROR: tools\practice_log_server.py was not found.
    pause
    exit /b 1
)

set "LAN_IP="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$ip = Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction SilentlyContinue ^| Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } ^| Select-Object -First 1 -ExpandProperty IPAddress; if ($ip) { $ip }"`) do set "LAN_IP=%%I"
if not defined LAN_IP set "LAN_IP=<this-PC-LAN-IP>"

echo ====================================
echo Pachi Practice Log Server
echo ====================================
echo.
echo PC:
echo http://127.0.0.1:8777/practice_log/
echo.
echo iPhone:
echo http://%LAN_IP%:8777/practice_log/
echo.
echo Stop:
echo Press Ctrl+C in this window
echo.
echo ====================================
echo.

python tools\practice_log_server.py --host 0.0.0.0 --port 8777
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo.
    echo Practice Log Server stopped.
) else (
    echo.
    echo Practice Log Server failed.
    echo Exit Code: %EXIT_CODE%
)
pause
exit /b %EXIT_CODE%
