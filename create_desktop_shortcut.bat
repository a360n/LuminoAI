@echo off
setlocal enabledelayedexpansion
title LuminoAI - Create Desktop Shortcut
cd /d "%~dp0"

echo ==========================================================
echo   LuminoAI - Creating Windows Desktop Shortcut
echo ==========================================================
echo.

set "TARGET=%~dp0run_LuminoAI.bat"
set "ICON=%~dp0logo.ico"
set "WORKDIR=%~dp0"

if not exist "%TARGET%" (
    echo [ERROR] Target launcher not found: %TARGET%
    pause
    exit /b 1
)

if not exist "%ICON%" (
    if exist "%~dp0LuminoAI.ico" (
        set "ICON=%~dp0LuminoAI.ico"
    )
)

echo  Target: %TARGET%
echo  Icon:   %ICON%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$desktop = $ws.SpecialFolders('Desktop'); " ^
    "$shortcutPath = [System.IO.Path]::Combine($desktop, 'LuminoAI.lnk'); " ^
    "$shortcut = $ws.CreateShortcut($shortcutPath); " ^
    "$shortcut.TargetPath = '%TARGET%'; " ^
    "$shortcut.WorkingDirectory = '%WORKDIR%'; " ^
    "$shortcut.IconLocation = '%ICON%,0'; " ^
    "$shortcut.Description = 'LuminoAI - Solar Panel AI Inspection & Cropper'; " ^
    "$shortcut.Save(); " ^
    "Write-Host '  [OK] Shortcut successfully created on Desktop!' -ForegroundColor Green"

echo.
echo ==========================================================
echo   Desktop shortcut ready: Desktop\LuminoAI.lnk
echo ==========================================================
echo.
timeout /t 3 >nul
