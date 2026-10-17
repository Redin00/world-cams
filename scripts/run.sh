#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "=========================================================="
echo "  WORLD-CAMS // Exposed RTSP Intelligence & Viewer"
echo "=========================================================="

# 1. Check Python virtual environment
if [ ! -d "venv" ]; then
    echo "[!] Virtual environment not found. Creating..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

source venv/bin/activate

# 2. Check MediaMTX binary
if [ ! -x "./bin/mediamtx" ]; then
    echo "[!] MediaMTX not found in bin/. Installing..."
    ./scripts/install_mediamtx.sh
fi

# 3. Create .env if not exists
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "[*] Initializing .env from .env.example..."
    cp .env.example .env
fi

# 4. Cleanup trap for graceful shutdown
cleanup() {
    echo ""
    echo "[*] Shutting down WorldCams services..."
    if [ -n "$MTX_PID" ]; then
        kill "$MTX_PID" 2>/dev/null || true
    fi
    if [ -n "$UVICORN_PID" ]; then
        kill "$UVICORN_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo "[✓] All services stopped cleanly."
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 5. Launch MediaMTX
echo "[*] Launching MediaMTX streaming gateway on :8554 (RTSP), :8888 (HLS), :8889 (WebRTC)..."
./bin/mediamtx mediamtx.yml > /dev/null 2>&1 &
MTX_PID=$!
sleep 1

# 6. Launch FastAPI backend
echo "[*] Launching FastAPI backend on http://localhost:8000..."
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
UVICORN_PID=$!

echo ""
echo "=========================================================="
echo "  [✓] WorldCams is running!"
echo "  -> Web UI & Dashboard: http://localhost:8000"
echo "  -> API Documentation: http://localhost:8000/docs"
echo "  -> MediaMTX WebRTC:   http://localhost:8889"
echo "  -> MediaMTX HLS:      http://localhost:8888"
echo "=========================================================="
echo "Press Ctrl+C to terminate all services."

# Wait for background processes
wait "$UVICORN_PID"
