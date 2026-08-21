@echo off
setlocal
set "ROOT=C:\kota\pachi_analysis"
cd /d "%ROOT%"

:menu
cls
echo PSCUBE Local Capture
echo Press ESC during capture to stop safely.
echo.
echo 1. Start Chrome (CDP)
echo 2. Morning Capture - All 71
echo 3. Morning Capture - Selected Machines
echo 4. Rescue Screenshot
echo 5. Preflight Check
echo 6. Exit
echo.
set "CHOICE="
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" goto start_chrome
if "%CHOICE%"=="2" goto morning
if "%CHOICE%"=="3" goto selected
if "%CHOICE%"=="4" goto rescue
if "%CHOICE%"=="5" goto preflight
if "%CHOICE%"=="6" exit /b 0
echo ERROR: Invalid selection.
pause
goto menu

:start_chrome
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo ERROR: Chrome executable not found.
  pause
  goto menu
)
start "PSCUBE Chrome CDP" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%ROOT%\chrome_cdp_profile" "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c713848/"
echo Chrome started with CDP port 9222.
echo Complete PSCUBE and Cloudflare checks in Chrome, then return here.
pause
goto menu

:morning
call "%ROOT%\run_pscube_cdp_morning.bat"
goto menu

:selected
call "%ROOT%\run_pscube_cdp_morning_selected.bat"
goto menu

:rescue
call "%ROOT%\run_pscube_cdp_rescue_screenshot.bat"
goto menu

:preflight
set "CHECK_DATE="
set /p "CHECK_DATE=Business date YYYYMMDD: "
if not defined CHECK_DATE (
  echo ERROR: Business date is required.
  pause
  goto menu
)
python tools\pscube_cdp_preflight.py --targets-file pscube_targets.json --expected-count 71 --date "%CHECK_DATE%"
pause
goto menu
