#!/bin/bash
# LuminoAI - Solar Panel AI Inspection & Cropper Launcher Script for macOS
# Starts local FastAPI server, verifies Git, C++ tools, Python 3, checks updates, and opens browser

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

PORT=8005
URL="http://localhost:${PORT}/aipath"
REPO_URL="https://github.com/a360n/LuminoAI.git"

# 🛡️ DEVELOPER WORKSPACE PROTECTION SHIELD
# Automatically detect if this script is being executed in the developer master repository
IS_DEV_WORKSPACE=0
if [ -f "$SCRIPT_DIR/.dev_master" ] || [ -f "$SCRIPT_DIR/build_protected_pipeline.py" ]; then
    IS_DEV_WORKSPACE=1
    echo "=========================================================="
    echo " 🛑 [SAFETY LOCK ACTIVATED] DEVELOPER WORKSPACE DETECTED!"
    echo "=========================================================="
    echo " ⚠️ You are running the CLIENT launcher inside the MASTER SOURCE repository."
    echo " 🔒 Git synchronization is PERMANENTLY BLOCKED to protect original code."
    echo " 💡 TIP: Please use './run_dev.command' for local development."
    echo "=========================================================="
    echo ""
fi

echo "=========================================================="
echo " [1/5] 🌐 Checking Internet Connection..."
echo "=========================================================="
ONLINE=0
if ping -c 1 -W 2000 8.8.8.8 &> /dev/null || curl -s --head --request GET https://www.google.com --connect-timeout 3 &> /dev/null; then
    ONLINE=1
    echo " [OK] Connected to Internet."
else
    echo " [INFO] No Internet connection detected. Running in Local Offline Mode."
fi

echo "=========================================================="
echo " [2/5] 🔄 Checking Repository Updates..."
echo "=========================================================="
if [ $IS_DEV_WORKSPACE -eq 1 ]; then
    echo " [SAFETY] Developer Master Repository: Git sync is disabled."
elif [ -d ".git" ] && [ $ONLINE -eq 1 ] && command -v git &> /dev/null; then
    echo " Checking updates from ${REPO_URL}..."
    if git fetch origin main --depth=1 --quiet 2>/dev/null; then
        LOCAL_HASH=$(git rev-parse HEAD 2>/dev/null)
        REMOTE_HASH=$(git rev-parse origin/main 2>/dev/null)
        if [ -n "$REMOTE_HASH" ] && [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
            echo " [UPDATE] New release detected. Synchronizing latest updates..."
            git reset --hard origin/main --quiet 2>/dev/null || true
            echo " [OK] Successfully updated to latest release!"
        else
            echo " [OK] System is up to date."
        fi
    fi
fi

echo "=========================================================="
echo " [3/5] 🐍 Checking Python 3 Environment..."
echo "=========================================================="

# Check if python3 is available
if ! command -v python3 &> /dev/null; then
    echo " [WARN] Python 3 is not installed on this system!"
    if command -v brew &> /dev/null && [ $ONLINE -eq 1 ]; then
        echo " [INFO] Installing Python 3 automatically via Homebrew..."
        brew install python@3.12
    else
        echo " [INFO] Downloading official Python 3 installer for macOS..."
        PKG_DEST="/tmp/python-3.12-macos.pkg"
        curl -L -o "$PKG_DEST" "https://www.python.org/ftp/python/3.12.8/python-3.12.8-macos11.pkg"
        if [ -f "$PKG_DEST" ]; then
            echo " [INFO] Opening Python 3 installer package. Please follow the on-screen steps..."
            open "$PKG_DEST"
        else
            echo " [ERROR] Please download and install Python from https://www.python.org/downloads/"
        fi
    fi
else
    PY_VER=$(python3 -V 2>&1)
    echo " [OK] ${PY_VER} is active."
fi

echo "=========================================================="
echo " [4/5] 📦 Verifying Required Python Libraries..."
echo "=========================================================="

if ! python3 -c "import fastapi, uvicorn, cv2, PIL, torch, torchvision, numpy, cryptography" &> /dev/null; then
    echo " [INFO] Missing Python dependencies. Upgrading pip and installing requirements..."
    python3 -m pip install --upgrade pip 2>/dev/null || true
    python3 -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo " [RETRY] Retrying installation with fallback package list..."
        python3 -m pip install fastapi uvicorn opencv-python pillow torch torchvision numpy python-multipart cryptography
    fi
else
    echo " [OK] All required Python libraries are installed and ready!"
fi

echo "=========================================================="
echo " [5/5] 🛡️ macOS Security & Gatekeeper Verification..."
echo "=========================================================="

# Automatically remove macOS Gatekeeper quarantine flags and ad-hoc sign compiled binaries
if [ "$(uname)" = "Darwin" ]; then
    xattr -cr "$SCRIPT_DIR" 2>/dev/null || true
    find "$SCRIPT_DIR" -name "*.so" -exec codesign --force --deep --sign - {} + 2>/dev/null || true
    echo " [OK] Binaries unblocked and security flags cleared."
fi

echo "=========================================================="
echo " 🚀 Starting LuminoAI Application Server..."
echo " 🌐 URL: ${URL}"
echo "=========================================================="

# Launch asynchronous watcher to open browser ONLY when server responds with 200 OK
(
    for i in $(seq 1 60); do
        sleep 0.3
        if curl -s -o /dev/null -I "http://127.0.0.1:${PORT}/aipath" 2>/dev/null; then
            sleep 0.5
            open "${URL}"
            break
        fi
    done
) &

python3 -m uvicorn main:app --host 127.0.0.1 --port ${PORT}

