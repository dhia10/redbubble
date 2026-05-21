@echo off
:: ============================================================
::  start_debug_browser.bat
::  Launches Opera GX with remote debugging enabled on port 9222.
::  Run this ONCE before running main.py.
::  Keep the browser window open while the pipeline runs.
:: ============================================================

set OPERA_EXE=%LOCALAPPDATA%\Programs\Opera GX\opera.exe
set PROFILE_DIR=%~dp0data\opera_debug_profile

if not exist "%OPERA_EXE%" (
    echo Opera GX not found at %OPERA_EXE%
    echo Please check the path and edit this bat file.
    pause
    exit /b 1
)

echo Starting Opera GX with remote debugging on port 9222...
echo Profile: %PROFILE_DIR%
echo.
echo Leave this browser window open while running main.py
echo You can close it when the pipeline finishes.
echo.

start "" "%OPERA_EXE%" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%PROFILE_DIR%" ^
    --no-first-run ^
    --no-default-browser-check ^
    --disable-blink-features=AutomationControlled ^
    "https://www.redbubble.com/"
