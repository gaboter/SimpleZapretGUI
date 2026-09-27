@echo off
setlocal
cd /d "%~dp0"
title SimpleZapretGUI - debug run

rem ============================================================
rem  Debug launch from sources. All data and logs go to .\data
rem ============================================================

rem ---- 1. Administrator rights (WinDivert driver and services need them)
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

rem ---- 2. Find Python 3.9+
set "BASEPY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 set "BASEPY=py -3"
if not defined BASEPY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
    if not errorlevel 1 set "BASEPY=python"
)
if not defined BASEPY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set BASEPY="%%D\python.exe"
    )
)
if not defined BASEPY (
    echo [ERROR] Python 3.9 or newer was not found.
    echo Install it from https://www.python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)
echo Base Python:
%BASEPY% --version

rem ---- 3. Virtual environment: create, or recreate if broken
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" (
    "%VPY%" -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo Virtual environment is broken - recreating it...
        rmdir /s /q ".venv"
    )
)
if exist ".venv" if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment is incomplete - recreating it...
    rmdir /s /q ".venv"
)
if not exist "%VPY%" (
    echo Creating virtual environment...
    %BASEPY% -m venv ".venv"
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
"%VPY%" -m pip --version >nul 2>&1
if errorlevel 1 "%VPY%" -m ensurepip --upgrade

"%VPY%" -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
    echo [ERROR] 32-bit Python is installed. PySide6 needs 64-bit Python.
    echo Install the 64-bit version from https://www.python.org
    pause
    exit /b 1
)

rem ---- 4. Dependencies
"%VPY%" -c "import PySide6, psutil" >nul 2>&1
if errorlevel 1 (
    echo Installing PySide6 and psutil. The first run takes a few minutes...
    "%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. See the messages above.
        pause
        exit /b 1
    )
)

rem ---- 5. Run without a console window. Logs and errors: .\data\logs
if not exist "data\logs" mkdir "data\logs"
set "SZG_DATA_DIR=%~dp0data"
set "SZG_DEBUG=1"
echo Starting SimpleZapretGUI... Logs: %~dp0data\logs
start "" "%~dp0.venv\Scripts\pythonw.exe" -m simplezapretgui
exit /b 0
