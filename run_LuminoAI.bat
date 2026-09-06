@echo off
setlocal enabledelayedexpansion
:: LuminoAI - Solar Panel AI Inspection & Cropper Launcher Script for Windows
cd /d "%~dp0"

:: 🛡️ Windows Mark-of-the-Web (MotW) Self-Healing & Unblocker
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1

set PORT=8005
set URL=http://localhost:%PORT%/aipath
set REPO_URL=https://github.com/a360n/LuminoAI.git

echo ==========================================================
echo  Checking Internet Connection & VPN Status...
echo ==========================================================

ping -n 1 8.8.8.8 >nul 2>&1
if %errorlevel% == 0 (
    if exist ".git" (
        echo  Connected to Network/Internet. Checking updates from %REPO_URL%...
        git fetch origin main --depth=1 --quiet >nul 2>&1
        for /f "tokens=*" %%a in ('git rev-parse HEAD 2^>nul') do set LOCAL_HASH=%%a
        for /f "tokens=*" %%b in ('git rev-parse origin/main 2^>nul') do set REMOTE_HASH=%%b
        if defined REMOTE_HASH (
            if not "!LOCAL_HASH!" == "!REMOTE_HASH!" (
                echo  New release detected. Synchronizing latest updates...
                git reset --hard origin/main --quiet >nul 2>&1
                echo  Successfully updated to latest release!
            ) else (
                echo  System is up to date.
            )
        )
    ) else (
        echo  Connected to Network/Internet.
    )
) else (
    echo  Offline mode or restricted VPN connection. Skipping online update...
)

echo ==========================================================
echo  Verifying Required Python Libraries...
echo ==========================================================

python -c "import fastapi, uvicorn, cv2, PIL, torch, torchvision, numpy" >nul 2>&1
if %errorlevel% neq 0 (
    echo  Missing required Python libraries. Installing dependencies...
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo  Retrying installation with pip...
        pip install fastapi uvicorn opencv-python pillow torch torchvision numpy python-multipart
    )
) else (
    echo  All required Python libraries are installed and ready!
)

echo ==========================================================
echo  Starting LuminoAI Application...
echo  Opening browser at: %URL%
echo ==========================================================

start "" "%URL%"

python -m uvicorn main:app --host 127.0.0.1 --port %PORT% --reload
pause
