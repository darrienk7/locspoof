@echo off
setlocal
cd /d "%~dp0"

REM start-tunnel is @sudo_required - re-launch elevated if we aren't already.
net session >nul 2>&1
if %errorlevel% equ 0 goto :elevated

echo Requesting administrator privileges...
set "ARGS=%*"
if defined ARGS (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%ARGS%' -Verb RunAs -ErrorAction Stop"
) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop"
)
if errorlevel 1 (
    echo.
    echo Elevation cancelled - nothing launched.
    pause
    exit /b 1
)

REM Elevated window is up. Close this console entirely ("exit", not "exit /b").
exit

:elevated
title locspoof

if not exist ".venv\Scripts\python.exe" (
    echo Could not find .venv\Scripts\python.exe
    echo Create the venv first:  py -m venv .venv ^&^& .venv\Scripts\pip install pymobiledevice3
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "%~dp0spoof.py" %*

echo.
pause
