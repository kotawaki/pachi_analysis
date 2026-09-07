@echo off
setlocal

cd /d "%~dp0"

for /f "usebackq delims=" %%D in (`python -c "import datetime; print(datetime.date.today().strftime('%%Y%%m%%d'))"`) do set "RUN_DATE=%%D"

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
