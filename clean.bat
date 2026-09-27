@echo off
setlocal
cd /d "%~dp0"
title SimpleZapretGUI - clean

rem ============================================================
rem  Full cleanup of the project folder after debugging:
rem   - stops the running debug copy of the app and zapret (winws)
rem   - removes the zapret service / WinDivert driver / autostart task /
rem     hosts block created by the debug copy (only if they point to .\data)
rem   - deletes .\data (settings, logs, downloaded zapret), .venv,
rem     build outputs and caches
rem  Usage: clean.bat            (asks nothing, pauses at the end)
rem         clean.bat /nopause   (used by build.bat)
rem ============================================================

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    if "%~1"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs"
    )
    exit /b
)

echo [1/4] Closing the debug copy of SimpleZapretGUI...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m simplezapretgui*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul

echo [2/4] Removing services, driver, autostart and hosts entries of the debug copy...
if exist "data\zapret" if exist ".venv\Scripts\python.exe" (
    set "SZG_DATA_DIR=%~dp0data"
    ".venv\Scripts\python.exe" -m simplezapretgui --uninstall-cleanup
)
rem on the off chance python is gone: stop winws started from .\data
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process winws -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '%~dp0data\*' } | Stop-Process -Force -ErrorAction SilentlyContinue"
timeout /t 1 /nobreak >nul

echo [3/4] Deleting downloaded files and settings...
for %%D in (data .venv build dist dist-installer) do (
    if exist "%%D" (
        rmdir /s /q "%%D"
        if exist "%%D" echo   ! could not delete %%D - close programs that use it and run clean.bat again
    )
)
if exist "assets\icon.ico" del /q "assets\icon.ico"

echo [4/4] Deleting Python caches...
for /d /r "%~dp0" %%D in (__pycache__) do if exist "%%D" rmdir /s /q "%%D"

echo.
echo Clean finished.
if /i not "%~1"=="/nopause" pause
exit /b 0
