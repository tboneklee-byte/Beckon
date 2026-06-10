@echo off
setlocal
title Beckon Builder
color 0B

echo ==================================================
echo            BUILDING  Beckon.exe
echo ==================================================
echo.
echo This installs what's needed and builds the app.
echo First run takes a couple minutes. Don't close this.
echo.

cd /d "%~dp0"

if not exist "gesture_server.py" (
    echo [ERROR] gesture_server.py not found in this folder.
    echo Put "Build Beckon.bat" and "gesture_server.py" together.
    echo.
    pause
    exit /b 1
)

REM --- find a working Python (prefer the python.org 3.12 build) ---
set "PYCMD="
py -3.12 --version >nul 2>&1 && set "PYCMD=py -3.12"
if not defined PYCMD ( py --version >nul 2>&1 && set "PYCMD=py" )
if not defined PYCMD ( python --version >nul 2>&1 && set "PYCMD=python" )
if not defined PYCMD (
    echo [ERROR] No Python found.
    echo Install Python 3.12 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)
echo Using Python: %PYCMD%
echo.

echo [1/3] Installing dependencies...
%PYCMD% -m pip install --upgrade pip >nul
%PYCMD% -m pip install pynput pyinstaller
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency install failed. See above.
    pause
    exit /b 1
)

echo.
echo [2/3] Building the app...
%PYCMD% -m PyInstaller --onefile --name Beckon --clean gesture_server.py
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See above.
    pause
    exit /b 1
)

echo.
echo [3/3] Cleaning up...
if exist "build" rmdir /s /q "build" >nul 2>&1
if exist "Beckon.spec" del "Beckon.spec" >nul 2>&1

echo.
echo ==================================================
echo                    DONE!
echo ==================================================
echo.
echo Your app is here:
echo    %cd%\dist\Beckon.exe
echo.
echo HOW TO USE:
echo  - Double-click Beckon.exe.
echo  - A small console window opens and your browser
echo    launches to the gesture tracker.
echo  - Allow camera access in the browser.
echo  - To stop: just close that console window.
echo  - Windows may warn "protected your PC": More info -^> Run anyway.
echo.
explorer "%cd%\dist"
pause
