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

# 5. Extract MEDIAMTX_PUBLIC_HOST if set, and pass to MediaMTX env
ENV_PUB_HOST=$(grep -E '^\s*MEDIAMTX_PUBLIC_HOST=' .env 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || true)
if [ -n "$ENV_PUB_HOST" ]; then
    export MTX_WEBRTCADDITIONALHOSTS="$ENV_PUB_HOST"
    echo "[*] Configured WebRTC Additional Host (Public IP): $ENV_PUB_HOST"
fi

# Launch MediaMTX
echo "[*] Launching MediaMTX streaming gateway on :8554 (RTSP), :8888 (HLS), :8889 (WebRTC)..."
./bin/mediamtx mediamtx.yml > /dev/null 2>&1 &
MTX_PID=$!
sleep 1

# 6. Read APP_PORT and APP_HOST from .env, with CLI argument or env override
ENV_PORT=$(grep -E '^\s*APP_PORT=' .env 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || true)
ENV_HOST=$(grep -E '^\s*APP_HOST=' .env 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || true)

APP_PORT="${1:-${PORT:-${ENV_PORT:-8000}}}"
APP_HOST="${ENV_HOST:-0.0.0.0}"

# VPS protection: if APP_HOST is set to an external public IP, bind to 0.0.0.0
# to avoid Linux [Errno 99] Cannot assign requested address
BIND_HOST="0.0.0.0"
if [ "$APP_HOST" = "127.0.0.1" ] || [ "$APP_HOST" = "localhost" ]; then
    BIND_HOST="$APP_HOST"
fi

DISPLAY_HOST="${ENV_PUB_HOST:-$APP_HOST}"
if [ "$DISPLAY_HOST" = "0.0.0.0" ]; then
    DISPLAY_HOST="localhost"
fi

echo "[*] Launching FastAPI backend on http://${DISPLAY_HOST}:${APP_PORT} (listening on ${BIND_HOST})..."
python3 -m uvicorn backend.main:app --host "$BIND_HOST" --port "$APP_PORT" --reload &
UVICORN_PID=$!

echo ""
echo "=========================================================="
echo "  [✓] WorldCams is running!"
echo "  -> Web UI & Dashboard: http://${DISPLAY_HOST}:${APP_PORT}"
echo "  -> API Documentation: http://${DISPLAY_HOST}:${APP_PORT}/docs"
echo "  -> MediaMTX WebRTC:   http://localhost:8889"
echo "  -> MediaMTX HLS:      http://localhost:8888"
echo "=========================================================="
echo "Press Ctrl+C to terminate all services."

# Wait for background processes
wait "$UVICORN_PID"
