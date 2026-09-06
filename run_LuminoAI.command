#!/bin/bash
# LuminoAI - Solar Panel AI Inspection & Cropper Launcher Script for macOS
# Starts local FastAPI server, checks updates, verifies dependencies, and opens browser

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# Automatically remove macOS Gatekeeper quarantine flags and ad-hoc sign compiled binaries
if [ "$(uname)" = "Darwin" ]; then
    xattr -cr "$SCRIPT_DIR" 2>/dev/null || true
    find "$SCRIPT_DIR" -name "*.so" -exec codesign --force --deep --sign - {} + 2>/dev/null || true
fi

PORT=8005
URL="http://localhost:${PORT}/aipath"
REPO_URL="https://github.com/a360n/LuminoAI.git"

echo "=========================================================="
echo " 🌐 Checking Internet Connection & VPN Status..."
echo "=========================================================="

# Check internet connectivity & check updates if git repo
if ping -c 1 -W 2 8.8.8.8 &> /dev/null || curl -s --head --request GET https://github.com --connect-timeout 2 &> /dev/null; then
    if [ -d ".git" ]; then
        echo " ✅ Connected to Network/Internet. Checking updates from ${REPO_URL}..."
        if git fetch origin main --depth=1 --quiet 2>/dev/null; then
            LOCAL_HASH=$(git rev-parse HEAD 2>/dev/null)
            REMOTE_HASH=$(git rev-parse origin/main 2>/dev/null)
            if [ -n "$REMOTE_HASH" ] && [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
                echo " 🔄 New release detected. Synchronizing latest updates..."
                git reset --hard origin/main --quiet 2>/dev/null || true
                echo " ✅ Successfully updated to latest release!"
            else
                echo " ✅ System is up to date."
            fi
        fi
    else
        echo " ✅ Connected to Network/Internet."
    fi
else
    echo " ⚠️ Offline mode or restricted VPN connection. Skipping online update."
fi

echo "=========================================================="
echo " 📦 Verifying Required Python Libraries..."
echo "=========================================================="

if ! python3 -c "import fastapi, uvicorn, cv2, PIL, torch, torchvision, numpy" &> /dev/null; then
    echo " ⚠️ Missing Python dependencies. Installing required libraries..."
    python3 -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo " ⚠️ Retrying installation with pip..."
        pip3 install fastapi uvicorn opencv-python pillow torch torchvision numpy python-multipart
    fi
else
    echo " ✅ All required Python libraries are installed and ready!"
fi

echo "=========================================================="
echo " Starting LuminoAI Application..."
echo " 🌐 Opening browser at: ${URL}"
echo "=========================================================="

# Open browser automatically after 1.5 seconds delay
(
    sleep 1.5
    open "${URL}"
) &

python3 -m uvicorn main:app --host 127.0.0.1 --port ${PORT} --reload
