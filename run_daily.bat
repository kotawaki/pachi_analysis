@echo off
setlocal

cd /d "%~dp0"

for /f "usebackq delims=" %%D in (`python -c "import datetime; print(datetime.date.today().strftime('%%Y%%m%%d'))"`) do set "DEFAULT_DATE=%%D"

set "RUN_DATE="
set /p "RUN_DATE=Business date [%DEFAULT_DATE%]: "
if not defined RUN_DATE set "RUN_DATE=%DEFAULT_DATE%"

echo(%RUN_DATE%|%SystemRoot%\System32\findstr.exe /r /x "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]" >nul
if errorlevel 1 (
    echo ========================================
    echo  Pachi Analysis Daily Runner
    echo ========================================
    echo Invalid business date: %RUN_DATE%
    echo Expected exactly 8 digits: YYYYMMDD
    echo DAILY RUNNER FAILED
    echo Exit Code: 2
    pause
    exit /b 2
)

if not defined RUN_DATE (
    echo ========================================
    echo  Pachi Analysis Daily Runner
    echo ========================================
    echo Failed to determine date.
    echo DAILY RUNNER FAILED
    echo Exit Code: 2
    pause
    exit /b 2
)

echo ========================================
echo  Pachi Analysis Daily Runner
echo  Date: %RUN_DATE%
echo ========================================
echo.

python tools/run_daily.py --date %RUN_DATE%
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo.
    echo DAILY RUNNER FINISHED
) else (
    echo.
    echo DAILY RUNNER FAILED
    echo Exit Code: %EXIT_CODE%
)

pause
exit /b %EXIT_CODE%
