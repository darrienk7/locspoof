@echo off
setlocal
cd /d "%~dp0"

REM locspoof launcher for Windows.
REM
REM Connecting to an iPhone on Windows needs Administrator, so this re-launches
REM itself elevated. First run creates a private Python environment in .venv
REM and installs what locspoof needs; after that it just starts the app.

net session >nul 2>&1
if %errorlevel% equ 0 goto :elevated

echo Requesting administrator rights...
set "ARGS=%*"
if defined ARGS (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%ARGS%' -Verb RunAs -ErrorAction Stop"
) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop"
)
if errorlevel 1 (
    echo.
    echo Administrator rights were declined - locspoof was not started.
    pause
    exit /b 1
)
REM The elevated window is open. Close this one.
exit

:elevated
title locspoof

if exist ".venv\Scripts\python.exe" goto :install

echo First run - setting up locspoof. This takes a minute, once.
set "PYTHON="
where py >nul 2>&1 && set "PYTHON=py -3"
if not defined PYTHON (
    where python >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON goto :nopython
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :nopython
%PYTHON% -m venv .venv
if errorlevel 1 goto :failed

:install
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if not errorlevel 1 goto :run
echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :failed
copy /y requirements.txt .venv\requirements.installed >nul
echo Done.
echo.

:run
".venv\Scripts\python.exe" main.py %*
echo.
pause
exit /b 0

:nopython
echo.
echo Python 3.10 or newer is required and wasn't found.
echo Install it from https://www.python.org/downloads/ - tick "Add python.exe to PATH" -
echo then run this again.
pause
exit /b 1

:failed
echo.
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
