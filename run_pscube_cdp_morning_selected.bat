@echo off
setlocal
set "ROOT=C:\kota\pachi_analysis"
set "LAST_DATE_FILE=%ROOT%\config\last_morning_date.txt"
cd /d "%ROOT%"
if not exist "%ROOT%\config" mkdir "%ROOT%\config"
set "LAST_DATE="
if exist "%LAST_DATE_FILE%" set /p "LAST_DATE="<"%LAST_DATE_FILE%"
set "TARGET_DATE=%LAST_DATE%"
set /p "TARGET_DATE=Business date [%LAST_DATE%]: "
if not defined TARGET_DATE (
  echo ERROR: Business date is required.
  pause
  exit /b 2
)
echo(%TARGET_DATE%|%SystemRoot%\System32\findstr.exe /r /x "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]" >nul
if errorlevel 1 (
  echo ERROR: Business date must be exactly 8 digits.
  pause
  exit /b 2
)
>"%LAST_DATE_FILE%" echo %TARGET_DATE%
set "MACHINES="
set /p "MACHINES=Machines (space-separated, enabled targets only): "
if not defined MACHINES (
  echo ERROR: At least one machine is required.
  pause
  exit /b 2
)
echo Running selected-machine preflight...
python tools\pscube_cdp_preflight.py --targets-file pscube_targets.json --machines %MACHINES% --date "%TARGET_DATE%"
if errorlevel 1 (
  echo Preflight FAILED. Capture will not start.
  pause
  exit /b 3
)
echo Preflight OK.
echo Press ESC during capture to stop safely.
python tools\pscube_cdp_morning_capture.py --machine %MACHINES% --date "%TARGET_DATE%" --retries 2 --delay-min 3 --delay-max 5
set "RESULT=%ERRORLEVEL%"
echo Selected capture finished: exit=%RESULT%.
pause
exit /b %RESULT%
