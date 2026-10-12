@echo off
chcp 65001 >nul
title LuminoAI - Historical Backfill Trigger

echo ===================================================
echo   LuminoAI - Historical Backfill Engine (27k+)
echo ===================================================
echo.
echo Sending start signal to LuminoAI server...
echo.

curl -s -X POST http://localhost:8005/api/backfill/start

echo.
echo ---------------------------------------------------
echo Backfill worker initiated successfully in background!
echo.
echo You can monitor real-time progress via:
echo 1. Web UI: http://localhost:8005/settings (EcoLAB Database tab)
echo 2. API Status: http://localhost:8005/api/backfill/status
echo ---------------------------------------------------
echo.
pause
