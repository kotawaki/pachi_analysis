@echo off
setlocal
set "ROOT=C:\kota\pachi_analysis"
set "LAST_DATE_FILE=%ROOT%\config\last_rescue_date.txt"
cd /d "%ROOT%"
if not exist "%ROOT%\config" mkdir "%ROOT%\config"
set "LAST_DATE="
if exist "%LAST_DATE_FILE%" set /p "LAST_DATE="<"%LAST_DATE_FILE%"
set "TARGET_DATE=%LAST_DATE%"
set /p "TARGET_DATE=Rescue date [%LAST_DATE%]: "
if not defined TARGET_DATE (
  echo ERROR: Rescue date is required.
  pause
  exit /b 2
)
echo(%TARGET_DATE%|%SystemRoot%\System32\findstr.exe /r /x "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]" >nul
if errorlevel 1 (
  echo ERROR: Rescue date must be exactly 8 digits.
  pause
  exit /b 2
)
>"%LAST_DATE_FILE%" echo %TARGET_DATE%
echo Rescue screenshot capture is not implemented yet.
echo No rescue capture was started.
pause
exit /b 0
