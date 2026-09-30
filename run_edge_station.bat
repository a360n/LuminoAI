@echo off
setlocal enabledelayedexpansion
:: LuminoAI - Satellite Edge Station Launcher for EcoLAB PC (192.168.101.225)
:: Automatically runs LuminoAI in Satellite Edge mode, watching local E:\EL_Repo and syncing over HTTP port 8005
cd /d "%~dp0"

set LUMINO_STATION_ROLE=edge
set URL=http://localhost:8005/settings#edge-sync

call "%~dp0run_LuminoAI.bat"
