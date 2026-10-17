#!/usr/bin/env bash
set -e

# WorldCams MediaMTX Downloader and Robust Setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BIN_DIR="$PROJECT_ROOT/bin"
VERSION="v1.21.2"
TAR_FILE="$BIN_DIR/mediamtx.tar.gz"

echo "=== Setting up MediaMTX ($VERSION) ==="
mkdir -p "$BIN_DIR"

if [ -x "$BIN_DIR/mediamtx" ]; then
    echo "[✓] MediaMTX is already installed at $BIN_DIR/mediamtx"
    "$BIN_DIR/mediamtx" --version || true
    exit 0
fi

# Detect system architecture
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) MTX_ARCH="amd64" ;;
    aarch64|arm64) MTX_ARCH="arm64v8" ;;
    armv7l) MTX_ARCH="armv7" ;;
    *) echo "[ERROR] Unsupported architecture: $ARCH"; exit 1 ;;
esac

OS="linux"
URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/mediamtx_${VERSION}_${OS}_${MTX_ARCH}.tar.gz"

# Remove any previous broken / empty download
rm -f "$TAR_FILE"

is_valid_gzip() {
    [ -f "$1" ] && [ -s "$1" ] && gzip -t "$1" >/dev/null 2>&1
}

echo "[*] Downloading MediaMTX for ${OS}-${MTX_ARCH}..."

# Method 1: Standard curl with follow redirects and retry
if command -v curl >/dev/null 2>&1; then
    echo "[*] Trying download via curl..."
    curl -fSL --retry 3 --retry-delay 2 "$URL" -o "$TAR_FILE" 2>/dev/null || true
fi

# Method 2: Curl with insecure SSL (handles minimal/enterprise Linux with outdated CA bundles)
if ! is_valid_gzip "$TAR_FILE" && command -v curl >/dev/null 2>&1; then
    echo "[!] Standard curl download failed. Retrying curl with certificate fallback..."
    rm -f "$TAR_FILE"
    curl -fkSL --retry 2 "$URL" -o "$TAR_FILE" 2>/dev/null || true
fi

# Method 3: wget fallback
if ! is_valid_gzip "$TAR_FILE" && command -v wget >/dev/null 2>&1; then
    echo "[!] Retrying download via wget..."
    rm -f "$TAR_FILE"
    wget -q --no-check-certificate -O "$TAR_FILE" "$URL" || true
fi

# Method 4: Python native fallback downloader
if ! is_valid_gzip "$TAR_FILE" && command -v python3 >/dev/null 2>&1; then
    echo "[!] Retrying download via Python urllib..."
    rm -f "$TAR_FILE"
    python3 -c "
import urllib.request, ssl, sys
url = '$URL'
out_path = '$TAR_FILE'
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
req = urllib.request.Request(url, headers={'User-Agent': 'WorldCams-Installer/1.0'})
try:
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    with opener.open(req, timeout=30) as resp, open(out_path, 'wb') as f:
        f.write(resp.read())
except Exception as e:
    print(f'Python download error: {e}', file=sys.stderr)
    sys.exit(1)
" || true
fi

# Final validation before extracting
if ! is_valid_gzip "$TAR_FILE"; then
    echo ""
    echo "[ERROR] Failed to download a valid MediaMTX gzip archive."
    if [ -f "$TAR_FILE" ]; then
        echo "Downloaded file content preview (first lines):"
        head -n 5 "$TAR_FILE" || true
        rm -f "$TAR_FILE"
    fi
    echo ""
    echo "Troubleshooting options for Oracle Linux / RHEL / CentOS:"
    echo "1. Update system CA certificates: sudo dnf install -y ca-certificates curl tar"
    echo "2. Or manually download MediaMTX from: $URL"
    echo "   and extract it directly to $BIN_DIR/mediamtx"
    exit 1
fi

echo "[*] Extracting MediaMTX binary..."
tar -xzf "$TAR_FILE" -C "$BIN_DIR"
rm -f "$TAR_FILE"
chmod +x "$BIN_DIR/mediamtx"

echo "[✓] MediaMTX installed successfully:"
"$BIN_DIR/mediamtx" --version
