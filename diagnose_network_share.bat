@echo off
title LuminoAI - Network Share and EcoLAB Diagnostics Tool
color 0F
cd /d "%~dp0"

set "TARGET_HOST=192.168.101.225"
set "TARGET_SHARE=EL_Repo"
set "TARGET_USER=ecodba"
set "TARGET_PASS=eco.PWD"
set "TARGET_UNC=\\%TARGET_HOST%\\%TARGET_SHARE%"

:menu
cls
echo ==============================================================================
echo       LuminoAI - Advanced Network Share and EcoLAB Station Diagnostics
echo ==============================================================================
echo  Target Server IP:     %TARGET_HOST%
echo  Target Share Name:    %TARGET_SHARE% (%TARGET_UNC%)
echo  Target Credentials:   %TARGET_HOST%\%TARGET_USER%
echo ==============================================================================
echo.
echo  [1] Run Full Diagnostic Scan (Ping, Ports 445/1433, SMB, Credentials, Images)
echo  [2] Quick Connect and Map as Drive Z: (net use Z: %TARGET_UNC%)
echo  [3] Register Credentials in Windows Credential Manager (cmdkey)
echo  [4] Change Windows Network Profile to PRIVATE (Fixes SMB Block)
echo  [5] Enable SMB Client Compatibility (Allow Insecure Guest / Disable Signing)
echo  [6] Open Network Share in Windows Explorer (explorer %TARGET_UNC%)
echo  [7] Clear Stale Network Sessions (net use * /delete)
echo  [8] Change Target Server IP / Credentials
echo  [9] Exit
echo.
set /p "CHOICE=Select an option [1-9] (Default: 1): "
if "%CHOICE%"=="" set "CHOICE=1"

if "%CHOICE%"=="1" goto :run_diagnostics
if "%CHOICE%"=="2" goto :map_drive_z
if "%CHOICE%"=="3" goto :register_cmdkey
if "%CHOICE%"=="4" goto :fix_network_private
if "%CHOICE%"=="5" goto :fix_smb_signing
if "%CHOICE%"=="6" goto :open_explorer
if "%CHOICE%"=="7" goto :clear_sessions
if "%CHOICE%"=="8" goto :change_targets
if "%CHOICE%"=="9" exit /b 0
goto :menu

:run_diagnostics
cls
echo ==============================================================================
echo                    RUNNING FULL NETWORK DIAGNOSTIC SCAN                       
echo ==============================================================================
echo.

:: ---------------------------------------------------------
:: TEST 1: Local Network Interface & IP Configuration
:: ---------------------------------------------------------
echo [TEST 1/7] Inspecting Local IP Configuration...
powershell -NoProfile -Command ^
    "$nics = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }; " ^
    "if (-not $nics) { Write-Host '  [WARN] No active network interfaces detected.' -ForegroundColor Yellow } " ^
    "else { " ^
    "  foreach ($n in $nics) { " ^
    "    Write-Host ('  [OK] Local IP: ' + $n.IPAddress + ' / Interface: ' + $n.InterfaceAlias) -ForegroundColor Cyan " ^
    "  } " ^
    "}"
echo.

:: ---------------------------------------------------------
:: TEST 2: Windows Network Profile (Private vs Public)
:: ---------------------------------------------------------
echo [TEST 2/7] Checking Windows Network Category (Private vs Public)...
powershell -NoProfile -Command ^
    "$prof = Get-NetConnectionProfile -ErrorAction SilentlyContinue; " ^
    "if (-not $prof) { Write-Host '  [?] Could not detect network profile.' -ForegroundColor Gray } " ^
    "else { " ^
    "  foreach ($p in $prof) { " ^
    "    $cat = $p.NetworkCategory; " ^
    "    if ($cat -eq 'Public') { " ^
    "      Write-Host ('  [WARN] Interface [' + $p.InterfaceAlias + '] is set to PUBLIC. Windows blocks SMB on Public networks.') -ForegroundColor Red; " ^
    "      Write-Host '         Tip: Use Menu Option [4] to switch this connection to Private.' -ForegroundColor Yellow " ^
    "    } else { " ^
    "      Write-Host ('  [OK] Interface [' + $p.InterfaceAlias + '] is ' + $cat + '.') -ForegroundColor Green " ^
    "    } " ^
    "  } " ^
    "}"
echo.

:: ---------------------------------------------------------
:: TEST 3: Physical and IP Reachability (ICMP Ping)
:: ---------------------------------------------------------
echo [TEST 3/7] Testing Physical Network Reachability (Ping %TARGET_HOST%)...
ping -n 2 -w 1500 %TARGET_HOST% >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Server %TARGET_HOST% responded to network ping.
) else (
    echo   [FAILED] Server %TARGET_HOST% did NOT respond to ping.
    echo            Possible causes:
    echo            1. Ethernet cable unplugged or Wi-Fi connected to different network.
    echo            2. Server %TARGET_HOST% is powered off or IP has changed.
    echo            3. ICMP Ping blocked by Windows Defender Firewall on %TARGET_HOST%.
)
echo.

:: ---------------------------------------------------------
:: TEST 4: Port 445 (SMB File Sharing) and Port 1433 (SQL Server)
:: ---------------------------------------------------------
echo [TEST 4/7] Testing TCP Ports (Port 445 SMB and Port 1433 SQL)...
powershell -NoProfile -Command ^
    "$t445 = Test-NetConnection -ComputerName '%TARGET_HOST%' -Port 445 -WarningAction SilentlyContinue; " ^
    "if ($t445.TcpTestSucceeded) { Write-Host '  [OK] Port 445 (SMB File Sharing): OPEN and accepting connections.' -ForegroundColor Green } " ^
    "else { Write-Host '  [FAILED] Port 445 (SMB File Sharing): CLOSED / BLOCKED by Firewall on %TARGET_HOST%.' -ForegroundColor Red; " ^
    "       Write-Host '           Fix on %TARGET_HOST%: Run as Admin: netsh advfirewall firewall set rule group=\"\"File and Printer Sharing\"\" new enable=Yes' -ForegroundColor Yellow }; " ^
    "$t1433 = Test-NetConnection -ComputerName '%TARGET_HOST%' -Port 1433 -WarningAction SilentlyContinue; " ^
    "if ($t1433.TcpTestSucceeded) { Write-Host '  [OK] Port 1433 (SQL Server): OPEN and ready.' -ForegroundColor Green } " ^
    "else { Write-Host '  [WARN] Port 1433 (SQL Server): CLOSED or dynamic port.' -ForegroundColor Yellow }"
echo.

:: ---------------------------------------------------------
:: TEST 5: Remote Share Discovery (net view)
:: ---------------------------------------------------------
echo [TEST 5/7] Querying Available Shares on %TARGET_HOST% (net view)...
net view \\%TARGET_HOST% 2>&1 | findstr /i "%TARGET_SHARE%" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Share '%TARGET_SHARE%' was successfully discovered on %TARGET_HOST%.
) else (
    echo   [INFO] Could not enumerate shares anonymously (Standard Windows security).
    echo          Proceeding to authenticated connection test...
)
echo.

:: ---------------------------------------------------------
:: TEST 6: Authenticated SMB Connection Test (net use and cmdkey)
:: ---------------------------------------------------------
echo [TEST 6/7] Authenticating to %TARGET_UNC% using credentials...
echo   - Injecting credentials into Windows Credential Manager...
cmdkey /add:%TARGET_HOST% /user:%TARGET_HOST%\%TARGET_USER% /pass:%TARGET_PASS% >nul 2>&1
cmdkey /add:%TARGET_HOST% /user:%TARGET_USER% /pass:%TARGET_PASS% >nul 2>&1

echo   - Testing connection via net use with host-qualified user (%TARGET_HOST%\%TARGET_USER%)...
net use %TARGET_UNC% /delete /y >nul 2>&1
net use %TARGET_UNC% %TARGET_PASS% /user:%TARGET_HOST%\%TARGET_USER% /persistent:no > "%TEMP%\lumino_smb_test.log" 2>&1

if %errorlevel% equ 0 (
    echo   [OK] SMB Connection SUCCESSFUL. Windows successfully mounted %TARGET_UNC%.
) else (
    echo   - Testing fallback with plain user (%TARGET_USER%)...
    net use %TARGET_UNC% %TARGET_PASS% /user:%TARGET_USER% /persistent:no > "%TEMP%\lumino_smb_test.log" 2>&1
    if errorlevel 1 (
        echo   [FAILED] Connection failed.
        echo   ------------------------------------------------------------
        echo   RAW WINDOWS ERROR OUTPUT:
        type "%TEMP%\lumino_smb_test.log"
        echo   ------------------------------------------------------------
        findstr /i "1326" "%TEMP%\lumino_smb_test.log" >nul 2>&1
        if not errorlevel 1 (
            echo   [DIAGNOSIS]: Error 1326 = Logon failure: unknown username or bad password.
            echo                Resolution: On machine %TARGET_HOST%, open Computer Management -^> Local Users,
            echo                ensure user '%TARGET_USER%' exists and password is '%TARGET_PASS%'.
            echo                Also right-click '%TARGET_SHARE%' -^> Properties -^> Sharing -^> Add Everyone (Read).
        )
        findstr /i "53" "%TEMP%\lumino_smb_test.log" >nul 2>&1
        if not errorlevel 1 (
            echo   [DIAGNOSIS]: Error 53 = The network path was not found.
            echo                Resolution:
            echo                1. Inbound File Sharing is blocked by Windows Firewall on %TARGET_HOST%.
            echo                   Run as Admin on %TARGET_HOST%:
            echo                   netsh advfirewall firewall set rule group="File and Printer Sharing" new enable=Yes
            echo                2. Make sure your network profile is set to PRIVATE (Option [4]).
        )
        findstr /i "5" "%TEMP%\lumino_smb_test.log" >nul 2>&1
        if not errorlevel 1 (
            echo   [DIAGNOSIS]: Error 5 = Access is denied.
            echo                Resolution: On machine %TARGET_HOST%, right-click folder '%TARGET_SHARE%' -^>
            echo                Properties -^> Security tab -^> Edit -^> Add Everyone (Read ^& execute).
        )
        findstr /i "1219" "%TEMP%\lumino_smb_test.log" >nul 2>&1
        if not errorlevel 1 (
            echo   [DIAGNOSIS]: Error 1219 = Multiple connections to server with different usernames.
            echo                Resolution: Use Option [7] to clear stale sessions, then retry.
        )
    ) else (
        echo   [OK] SMB Connection SUCCESSFUL. Windows successfully mounted %TARGET_UNC%.
    )
)
echo.

:: ---------------------------------------------------------
:: TEST 7: Directory and Image File Inspection
:: ---------------------------------------------------------
echo [TEST 7/7] Checking for .tif Images in %TARGET_UNC%...
powershell -NoProfile -Command ^
    "try { " ^
    "  if (Test-Path '%TARGET_UNC%') { " ^
    "    $files = Get-ChildItem -Path '%TARGET_UNC%' -Filter '*.tif' -Recurse -Depth 4 -ErrorAction SilentlyContinue | Select-Object -First 100; " ^
    "    if ($files) { " ^
    "      $latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1; " ^
    "      Write-Host ('  [OK] Successfully listed ' + $files.Count + ' .tif image files.') -ForegroundColor Green; " ^
    "      Write-Host ('       Latest Image: ' + $latest.Name) -ForegroundColor Cyan; " ^
    "      Write-Host ('       Modified:     ' + $latest.LastWriteTime) -ForegroundColor White; " ^
    "      Write-Host ('       Size:         ' + [math]::Round($latest.Length / 1MB, 2) + ' MB') -ForegroundColor White; " ^
    "    } else { " ^
    "      Write-Host '  [OK] Folder is accessible, but 0 .tif files found inside.' -ForegroundColor Yellow " ^
    "    } " ^
    "  } else { " ^
    "    Write-Host '  [SKIP] Folder not accessible, cannot list images.' -ForegroundColor Red " ^
    "  } " ^
    "} catch { Write-Host ('  [ERROR] Image scan error: ' + $_.Exception.Message) -ForegroundColor Red }"
echo.

echo ==============================================================================
echo                         DIAGNOSTIC SCAN COMPLETE                              
echo ==============================================================================
echo.
pause
goto :menu

:map_drive_z
cls
echo ==============================================================================
echo                 QUICK MAP NETWORK SHARE TO DRIVE Z:                           
echo ==============================================================================
echo.
echo Clearing any existing Z: mapping...
net use Z: /delete /y >nul 2>&1
cmdkey /add:%TARGET_HOST% /user:%TARGET_HOST%\%TARGET_USER% /pass:%TARGET_PASS% >nul 2>&1

echo Mapping %TARGET_UNC% to Drive Z:...
net use Z: %TARGET_UNC% %TARGET_PASS% /user:%TARGET_HOST%\%TARGET_USER% /persistent:yes
if %errorlevel% equ 0 (
    echo.
    echo  [SUCCESS] Successfully mapped to Drive Z:.
    echo  LuminoAI and Windows Explorer can now access images directly via Z:\.
    echo.
) else (
    echo.
    echo  [RETRY] Retrying with plain user %TARGET_USER%...
    net use Z: %TARGET_UNC% %TARGET_PASS% /user:%TARGET_USER% /persistent:yes
    if %errorlevel% equ 0 (
        echo.
        echo  [SUCCESS] Successfully mapped to Drive Z:.
    ) else (
        echo.
        echo  [FAILED] Could not map drive Z:. See diagnostic scan for details.
    )
)
echo.
pause
goto :menu

:register_cmdkey
cls
echo ==============================================================================
echo          INJECTING CREDENTIALS INTO WINDOWS CREDENTIAL MANAGER                
echo ==============================================================================
echo.
echo Adding credentials for %TARGET_HOST%...
cmdkey /add:%TARGET_HOST% /user:%TARGET_HOST%\%TARGET_USER% /pass:%TARGET_PASS%
cmdkey /add:%TARGET_HOST% /user:%TARGET_USER% /pass:%TARGET_PASS%
echo.
echo Current credentials stored for %TARGET_HOST%:
cmdkey /list | findstr /i "%TARGET_HOST%"
echo.
echo  [OK] Windows OS and Explorer will now automatically use these credentials
echo       whenever %TARGET_HOST% or %TARGET_UNC% is accessed.
echo.
pause
goto :menu

:fix_network_private
cls
echo ==============================================================================
echo           SWITCHING WINDOWS NETWORK PROFILE TO PRIVATE                        
echo ==============================================================================
echo.
echo Requesting Administrator privilege to switch network profile...
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -Command \"Get-NetConnectionProfile | Where-Object NetworkCategory -eq Public | Set-NetConnectionProfile -NetworkCategory Private; Write-Host ''[OK] Active network profiles switched to Private.'' -ForegroundColor Green; Start-Sleep -Seconds 2\"'"
echo.
echo  [OK] Network profile switch requested.
echo.
pause
goto :menu

:fix_smb_signing
cls
echo ==============================================================================
echo          ENABLING SMB CLIENT COMPATIBILITY AND GUEST FALLBACK                  
echo ==============================================================================
echo.
echo Applying Windows 10/11 SMB client compatibility settings...
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -Command \"Set-SmbClientConfiguration -RequireSecuritySignature \$false -Confirm:\$false -ErrorAction SilentlyContinue; Set-ItemProperty -Path ''HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters'' -Name ''AllowInsecureGuestAuth'' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue; Write-Host ''[OK] SMB Client Compatibility Settings Applied.'' -ForegroundColor Green; Start-Sleep -Seconds 2\"'"
echo.
echo  [OK] Compatibility settings requested.
echo.
pause
goto :menu

:open_explorer
cls
echo Opening %TARGET_UNC% in Windows Explorer...
cmdkey /add:%TARGET_HOST% /user:%TARGET_HOST%\%TARGET_USER% /pass:%TARGET_PASS% >nul 2>&1
start "" explorer "%TARGET_UNC%"
echo If prompted for credentials by Windows Explorer, enter:
echo   Username: %TARGET_HOST%\%TARGET_USER%
echo   Password: %TARGET_PASS%
echo   [x] Check "Remember my credentials"
echo.
pause
goto :menu

:clear_sessions
cls
echo ==============================================================================
echo                 CLEARING STALE NETWORK SMB SESSIONS                           
echo ==============================================================================
echo.
net use * /delete /y
echo.
echo  [OK] All network share sessions cleared.
echo.
pause
goto :menu

:change_targets
cls
echo ==============================================================================
echo                     CHANGE TARGET IP AND CREDENTIALS                           
echo ==============================================================================
echo.
set /p "TARGET_HOST=Enter Target Server IP (Current: %TARGET_HOST%): "
set /p "TARGET_SHARE=Enter Target Share Name (Current: %TARGET_SHARE%): "
set /p "TARGET_USER=Enter Target Username (Current: %TARGET_USER%): "
set /p "TARGET_PASS=Enter Target Password (Current: %TARGET_PASS%): "
set "TARGET_UNC=\\%TARGET_HOST%\\%TARGET_SHARE%"
echo.
echo  [OK] Target updated to %TARGET_UNC%.
echo.
pause
goto :menu
