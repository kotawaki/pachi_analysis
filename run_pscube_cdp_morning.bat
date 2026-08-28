@echo off
setlocal
set "ROOT=C:\kota\pachi_analysis"
set "LAST_DATE_FILE=%ROOT%\config\last_morning_date.txt"
set "DRY_RUN=0"
if /i "%~1"=="--dry-run" set "DRY_RUN=1"
cd /d "%ROOT%"

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found.
  pause
  exit /b 1
)

echo ========================================
echo PSCUBE CDP morning batch - 71 machines
echo ========================================
echo Press ESC during capture to stop safely.
echo.
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

echo.
echo Running preflight checks...
python tools\pscube_cdp_preflight.py --targets-file pscube_targets.json --expected-count 71 --date "%TARGET_DATE%"
if errorlevel 1 (
  echo.
  echo Preflight FAILED. Capture will not start.
  pause
  exit /b 3
)

echo.
echo Preflight OK.
if "%DRY_RUN%"=="1" (
  echo DRY-RUN: Capture will not start.
  pause
  exit /b 0
)

echo Starting PSCUBE morning capture...
python tools\pscube_cdp_morning_capture.py --targets-file pscube_targets.json --expected-count 71 --date "%TARGET_DATE%" --retries 2 --delay-min 5 --delay-max 8
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
  echo Capture finished: status=complete.
) else (
  echo Capture finished: status=incomplete_or_failed.
)
echo.
pause
exit /b %RESULT%
