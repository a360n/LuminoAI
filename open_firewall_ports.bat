@echo off
title LuminoAI - Open Windows Firewall Ports
echo ==========================================================
echo   LuminoAI - Automated Firewall Port Configuration
echo ==========================================================
echo.

:: Check for administrative permissions
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [REQUEST] Administrative permissions required.
    echo  [ELEVATING] Prompting for Administrator access...
    powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c \"\"%~dp0open_firewall_ports.bat\"\"' -Verb RunAs"
    exit /b
)

echo  [1/3] Allowing TCP Port 8005 (LuminoAI Core HTTP REST)...
netsh advfirewall firewall delete rule name="LuminoAI Port 8005" >nul 2>&1
netsh advfirewall firewall add rule name="LuminoAI Port 8005" dir=in action=allow protocol=TCP localport=8005 profile=any >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "New-NetFirewallRule -DisplayName 'LuminoAI Port 8005' -Direction Inbound -LocalPort 8005 -Protocol TCP -Action Allow -Profile Any -ErrorAction SilentlyContinue" >nul 2>&1

echo  [2/3] Allowing TCP Port 8006 (LuminoAI Mobile HTTPS)...
netsh advfirewall firewall delete rule name="LuminoAI Port 8006" >nul 2>&1
netsh advfirewall firewall add rule name="LuminoAI Port 8006" dir=in action=allow protocol=TCP localport=8006 profile=any >nul 2>&1

echo  [3/3] Allowing TCP Port 8007 (LuminoAI Hardware Buzzer)...
netsh advfirewall firewall delete rule name="LuminoAI Port 8007" >nul 2>&1
netsh advfirewall firewall add rule name="LuminoAI Port 8007" dir=in action=allow protocol=TCP localport=8007 profile=any >nul 2>&1

echo.
echo ==========================================================
echo  [SUCCESS] All LuminoAI network ports are now OPEN!
echo  Any machine on the factory network can now connect.
echo ==========================================================
echo.
pause
