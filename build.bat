@echo off
setlocal
cd /d "%~dp0"
title SimpleZapretGUI - build installer

rem ============================================================
rem  Builds the installer: dist-installer\SimpleZapretGUI-Setup-<ver>.exe
rem   1. full cleanup (clean.bat)
rem   2. fresh virtual environment + PySide6, psutil, PyInstaller, Pillow
rem   3. application icon, SimpleZapretGUI.exe (PyInstaller)
rem   4. installer (Inno Setup 6, installed automatically via winget if missing)
rem ============================================================

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ==== Step 1/4: cleanup ====
call "%~dp0clean.bat" /nopause

echo.
echo ==== Step 2/4: Python environment ====
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
    echo [ERROR] Python 3.9 or newer was not found. Install it from https://www.python.org
    pause
    exit /b 1
)
%BASEPY% --version
%BASEPY% -m venv ".venv"
if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
)
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip --version >nul 2>&1
if errorlevel 1 "%VPY%" -m ensurepip --upgrade
"%VPY%" -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
    echo [ERROR] 32-bit Python is installed. A 64-bit Python is required.
    pause
    exit /b 1
)
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo ==== Step 3/4: SimpleZapretGUI.exe ====
rem version number lives in simplezapretgui\core\paths.py (APP_VERSION); sync it into the exe properties
"%VPY%" tools\set_version.py
"%VPY%" tools\make_icon.py
if errorlevel 1 (
    echo [ERROR] Could not create the icon.
    pause
    exit /b 1
)
"%VPY%" -m PyInstaller --noconfirm --clean SimpleZapretGUI.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller failed. See the messages above.
    pause
    exit /b 1
)
if not exist "dist\SimpleZapretGUI\SimpleZapretGUI.exe" (
    echo [ERROR] dist\SimpleZapretGUI\SimpleZapretGUI.exe was not created.
    pause
    exit /b 1
)

echo.
echo ==== Step 4/4: installer ====
call "%~dp0installer.bat" /nopause
if errorlevel 1 exit /b 1
pause
exit /b 0

