@echo off
:: LuminoAI - One-Click Local Windows C-Binary (.pyd) Builder
cd /d "%~dp0"
echo ==========================================================
echo  Compiling LuminoAI C-Binaries for Windows (.pyd)
echo ==========================================================

python -m pip install --upgrade pip setuptools wheel cython >nul 2>&1
python setup_win.py build_ext --inplace

if %errorlevel% equ 0 (
    echo ==========================================================
    echo  SUCCESS! Native Windows .pyd binaries compiled!
    echo ==========================================================
) else (
    echo  Build error encountered. Ensure Visual C++ Build Tools are installed.
)
pause
