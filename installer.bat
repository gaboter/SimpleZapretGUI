@echo off
setlocal
cd /d "%~dp0"
title SimpleZapretGUI - installer

rem ============================================================
rem  Builds only the installer from the already built program
rem  (dist\SimpleZapretGUI). Called by build.bat, can be run alone.
rem  Usage: installer.bat            (pauses at the end)
rem         installer.bat /nopause   (used by build.bat)
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

if not exist "dist\SimpleZapretGUI\SimpleZapretGUI.exe" (
    echo [ERROR] dist\SimpleZapretGUI\SimpleZapretGUI.exe not found - run build.bat first.
    pause
    exit /b 1
)

rem ---- Inno Setup 6
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo Inno Setup 6 not found - installing it with winget...
    winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
)
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [ERROR] Inno Setup 6 is required. Install it from https://jrsoftware.org/isdl.php and run installer.bat again.
    pause
    exit /b 1
)

rem ---- The antivirus scans the freshly created Setup.exe while Inno Setup writes its icon,
rem ---- which fails with "EndUpdateResource failed (110)". Exclude the output folder
rem ---- from Windows Defender for the duration of the build and remove the exclusion afterwards.
if not exist "dist-installer" mkdir "dist-installer"
echo Adding a temporary Windows Defender exclusion for dist-installer...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-MpPreference -ExclusionPath '%~dp0dist-installer' -ErrorAction Stop; '  done' } catch { '  skipped - Windows Defender is not available' }"

set "ISS_OK="
for %%i in (1 2 3) do (
    if not defined ISS_OK (
        echo.
        echo ---- Inno Setup, attempt %%i of 3 ----
        "%ISCC%" installer\SimpleZapretGUI.iss
        if not errorlevel 1 (
            set "ISS_OK=1"
        ) else (
            echo Attempt %%i failed, retrying in 5 seconds...
            timeout /t 5 /nobreak >nul
        )
    )
)

echo Removing the temporary Windows Defender exclusion...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-MpPreference -ExclusionPath '%~dp0dist-installer' -ErrorAction SilentlyContinue"

if not defined ISS_OK (
    echo.
    echo [ERROR] Inno Setup failed three times.
    echo If the error is EndUpdateResource failed, add this folder to your antivirus exclusions
    echo and run installer.bat again:  %~dp0dist-installer
    pause
    exit /b 1
)

rem intermediate PyInstaller files are not needed
if exist "build" rmdir /s /q "build"

echo.
echo ============================================================
echo  Done. Installer:
for %%F in ("dist-installer\SimpleZapretGUI-Setup-*.exe") do echo    %%~fF
echo ============================================================
explorer "%~dp0dist-installer"
if /i not "%~1"=="/nopause" pause
exit /b 0
