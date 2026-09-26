@echo off
setlocal enabledelayedexpansion
:: LuminoAI - Solar Panel AI Inspection & Cropper Launcher Script for Windows
:: Automatically verifies C++ Runtime, Git, Python 3.12, and AI Dependencies
cd /d "%~dp0"

:: 🛡️ Windows Mark-of-the-Web (MotW) Self-Healing & Unblocker
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1
attrib -r "%~dp0*.db" >nul 2>&1
attrib -r "%~dp0*.db-*" >nul 2>&1

:: Check if launched from C:\Windows\System32
echo "%~dp0" | findstr /i "System32" >nul 2>&1
if not errorlevel 1 (
    echo ==========================================================
    echo  [NOTICE] LuminoAI is running from Windows System32!
    echo  Windows restricts file modifications in System32.
    echo  Audit database has been safely routed to %%LOCALAPPDATA%%\LuminoAI.
    echo  Tip: For standard setup, move LuminoAI to C:\LuminoAI or Documents.
    echo ==========================================================
)

set PORT=8005
set URL=http://localhost:%PORT%/aipath
set REPO_URL=https://github.com/a360n/LuminoAI.git

:: 🛡️ DEVELOPER WORKSPACE PROTECTION SHIELD
set IS_DEV_WORKSPACE=0
if exist "%~dp0.dev_master" set IS_DEV_WORKSPACE=1
if exist "%~dp0build_protected_pipeline.py" set IS_DEV_WORKSPACE=1

if "%IS_DEV_WORKSPACE%"=="1" (
    echo ==========================================================
    echo  [SAFETY LOCK ACTIVATED] DEVELOPER MASTER WORKSPACE DETECTED!
    echo  Git synchronization is PERMANENTLY BLOCKED to protect code.
    echo ==========================================================
)

echo ==========================================================
echo  [1/4] Checking Internet Connection...
echo ==========================================================
set ONLINE=0
ping -n 1 -w 2000 8.8.8.8 >nul 2>&1
if %errorlevel% equ 0 (
    set ONLINE=1
    echo  [OK] Connected to Internet.
) else (
    echo  [INFO] No Internet connection detected. Running in Local Offline Mode.
)

echo ==========================================================
echo  [2/4] Checking Repository Updates...
echo ==========================================================
if "%IS_DEV_WORKSPACE%"=="1" (
    echo  [SAFETY] Developer Master Repository: Git sync is disabled.
    goto :skip_git_sync
)

if exist .git (
    if "%ONLINE%"=="1" (
        where git >nul 2>&1
        if !errorlevel! equ 0 (
            echo  Checking updates from %REPO_URL%...
            git fetch origin main --depth=1 --quiet >nul 2>&1
            for /f "tokens=*" %%a in ('git rev-parse HEAD 2^>nul') do set LOCAL_HASH=%%a
            for /f "tokens=*" %%b in ('git rev-parse origin/main 2^>nul') do set REMOTE_HASH=%%b
            if defined REMOTE_HASH (
                if not "!LOCAL_HASH!" == "!REMOTE_HASH!" (
                    echo  [UPDATE] New release detected. Synchronizing latest updates...
                    git reset --hard origin/main --quiet >nul 2>&1
                    echo  [OK] Successfully updated to latest release!
                ) else (
                    echo  [OK] System is up to date.
                )
            )
        )
    )
)
:skip_git_sync

echo ==========================================================
echo  [3/4] Checking Python 3.12 Environment...
echo ==========================================================
set PY_CMD=

:: 1. Check if default 'python' in PATH is Python 3.12
python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1
if not errorlevel 1 (
    set PY_CMD=python
)

:: 2. If not, check if 'py -3.12' launcher works
if not defined PY_CMD (
    py -3.12 -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set PY_CMD=py -3.12
    )
)

:: 3. If not, check standard Python 3.12 directory locations
if not defined PY_CMD (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set PY_CMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    ) else if exist "%ProgramFiles%\Python312\python.exe" (
        set PY_CMD="%ProgramFiles%\Python312\python.exe"
        set "PATH=%ProgramFiles%\Python312;%ProgramFiles%\Python312\Scripts;%PATH%"
    )
)

:: 4. If Python 3.12 is still not found, auto-install it!
if not defined PY_CMD (
    echo  [WARN] Python 3.12 64-bit is required for LuminoAI native C-binaries.
    echo  [INFO] Installing Python 3.12 automatically...
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo  [INFO] Installing Python 3.12 via winget...
        winget install --id Python.Python.3.12 -e --source winget --silent --accept-source-agreements --accept-package-agreements
    ) else (
        echo  [INFO] Downloading official Python 3.12 installer...
        curl -L -o "%TEMP%\python-3.12-installer.exe" "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
        echo  [INFO] Running silent installer...
        start /wait "" "%TEMP%\python-3.12-installer.exe" /passive PrependPath=1 Include_test=0 SimpleInstall=1
    )
    :: Set PATH and locate the installed binary
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%ProgramFiles%\Python312;%ProgramFiles%\Python312\Scripts;%PATH%"
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set PY_CMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else if exist "%ProgramFiles%\Python312\python.exe" (
        set PY_CMD="%ProgramFiles%\Python312\python.exe"
    ) else (
        set PY_CMD=py -3.12
    )
)

:: Print active Python version cleanly (stderr redirected to nul)
for /f "tokens=*" %%v in ('%PY_CMD% -V 2^>nul') do echo  [OK] Active: %%v

echo ==========================================================
echo  [4/4] Verifying Required Python Libraries...
echo ==========================================================
%PY_CMD% -c "import fastapi, uvicorn, cv2, PIL, torch, torchvision, numpy, cryptography" >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Installing required libraries via pip for Python 3.12...
    %PY_CMD% -m pip install --upgrade pip >nul 2>&1
    %PY_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [RETRY] Retrying pip installation...
        %PY_CMD% -m pip install fastapi uvicorn opencv-python pillow torch torchvision numpy python-multipart cryptography pymssql pyodbc
    )
) else (
    echo  [OK] All required Python libraries are installed and ready!
)

:: Ensure Windows Desktop shortcut exists with custom logo icon
if not exist "%USERPROFILE%\Desktop\LuminoAI.lnk" (
    if exist "%~dp0logo.ico" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([System.IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'LuminoAI.lnk')); $s.TargetPath = '%~dp0run_LuminoAI.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%~dp0logo.ico,0'; $s.Description = 'LuminoAI - Solar Panel AI Inspection & Cropper'; $s.Save()" >nul 2>&1
    )
)

echo ==========================================================
echo  [5/5] 🛡️ Checking Windows Defender Firewall (Ports 8005, 8006 & 8007)...
echo ==========================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Get-NetFirewallRule -DisplayName 'LuminoAI Server (Port 8005)' -ErrorAction SilentlyContinue; if (!$r) { New-NetFirewallRule -DisplayName 'LuminoAI Server (Port 8005)' -Direction Inbound -LocalPort 8005 -Protocol TCP -Action Allow -ErrorAction Stop | Out-Null; Write-Host ' [OK] Inbound Windows Firewall rule for Port 8005 configured.' -ForegroundColor Green } else { Write-Host ' [OK] Inbound Windows Firewall rule for Port 8005 is ACTIVE.' -ForegroundColor Green } } catch {}"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Get-NetFirewallRule -DisplayName 'LuminoAI HTTPS (Port 8006)' -ErrorAction SilentlyContinue; if (!$r) { New-NetFirewallRule -DisplayName 'LuminoAI HTTPS (Port 8006)' -Direction Inbound -LocalPort 8006 -Protocol TCP -Action Allow -ErrorAction Stop | Out-Null; Write-Host ' [OK] Inbound Windows Firewall rule for Port 8006 (HTTPS) configured.' -ForegroundColor Green } else { Write-Host ' [OK] Inbound Windows Firewall rule for Port 8006 is ACTIVE.' -ForegroundColor Green } } catch {}"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Get-NetFirewallRule -DisplayName 'LuminoAI Buzzer (Port 8007)' -ErrorAction SilentlyContinue; if (!$r) { New-NetFirewallRule -DisplayName 'LuminoAI Buzzer (Port 8007)' -Direction Inbound -LocalPort 8007 -Protocol TCP -Action Allow -ErrorAction Stop | Out-Null; Write-Host ' [OK] Inbound Windows Firewall rule for Port 8007 configured.' -ForegroundColor Green } else { Write-Host ' [OK] Inbound Windows Firewall rule for Port 8007 is ACTIVE.' -ForegroundColor Green } } catch {}"

:: Automatically free ports 8005, 8006, and 8007 if occupied by orphaned processes
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo  [INFO] Port %PORT% is in use by PID %%p. Freeing port...
    taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":8006" ^| findstr "LISTENING"') do (
    echo  [INFO] Port 8006 is in use by PID %%p. Freeing port...
    taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":8007" ^| findstr "LISTENING"') do (
    echo  [INFO] Port 8007 is in use by PID %%p. Freeing port...
    taskkill /F /PID %%p >nul 2>&1
)

echo ==========================================================
echo  📡 Active Network Adapters & Factory Station Listeners (Wi-Fi Only):
echo ==========================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "$found=0; Get-NetIPConfiguration | Where-Object { $_.IPv4Address -ne $null } | ForEach-Object { $alias = $_.InterfaceAlias; $ip = $_.IPv4Address.IPAddress; $gw = $_.IPv4DefaultGateway.NextHop; $adapter = Get-NetAdapter -Name $alias -ErrorAction SilentlyContinue; if ($adapter.Status -eq 'Up' -and $alias -notmatch '(?i)vpn|wireguard|tailscale|zerotier|hamachi|tap|tun|ppp|virtual|vmware|vbox|loopback|docker' -and $ip -notmatch '^100\.' -and $ip -notmatch '^169\.254\.' -and $ip -notmatch '^127\.') { $found=1; $type = if ($alias -match '(?i)wi-?fi|wireless|wlan') { 'Wi-Fi Network' } else { 'Factory LAN' }; Write-Host ('   • ' + $alias + ' (' + $type + ')') -ForegroundColor Cyan; Write-Host ('     IP Address:   ' + $ip) -ForegroundColor White; Write-Host ('     ⚡ Rework Mobile (HTTPS):      https://' + $ip + ':8006/rework') -ForegroundColor Green; Write-Host ('     🔔 Before-Buzzer Station:      http://' + $ip + ':%PORT%/before-buzzer') -ForegroundColor Yellow; Write-Host ('     👁️ Human-Vision (HTTPS):       https://' + $ip + ':8006/human-vision') -ForegroundColor Blue; Write-Host ('     🏁 Central QR Code Hub:        http://' + $ip + ':%PORT%/qrcodes') -ForegroundColor Magenta } }; if ($found -eq 0) { Write-Host '   ⚠️ No connected Wi-Fi/LAN adapter found. Connect PC to Wi-Fi or Ethernet.' -ForegroundColor Yellow }"
echo ==========================================================
echo  💻 Main Station (Host PC): http://localhost:%PORT%/aipath
echo ==========================================================

:: Launch background watcher to open browser ONLY when server is fully bound and ready
start /min powershell -NoProfile -WindowStyle Hidden -Command "$u='%URL%'; for ($i=0; $i -lt 60; $i++) { Start-Sleep -Milliseconds 300; try { $tcp = New-Object System.Net.Sockets.TcpClient; $tcp.Connect('127.0.0.1', %PORT%); if ($tcp.Connected) { $tcp.Close(); Start-Sleep -Milliseconds 600; Start-Process $u; break } } catch {} }"

%PY_CMD% -m uvicorn main:app --host 0.0.0.0 --port %PORT%
pause

