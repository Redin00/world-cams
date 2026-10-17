#!/usr/bin/env bash
set -e

# WorldCams MediaMTX Downloader and Setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BIN_DIR="$PROJECT_ROOT/bin"
VERSION="v1.21.2"

echo "=== Setting up MediaMTX ($VERSION) ==="
mkdir -p "$BIN_DIR"

if [ -x "$BIN_DIR/mediamtx" ]; then
    echo "MediaMTX is already installed at $BIN_DIR/mediamtx"
    "$BIN_DIR/mediamtx" --version
    exit 0
fi

ARCH=$(uname -m)
case "$ARCH" in
    x86_64) MTX_ARCH="amd64" ;;
    aarch64|arm64) MTX_ARCH="arm64v8" ;;
    armv7l) MTX_ARCH="armv7" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

OS="linux"
URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/mediamtx_${VERSION}_${OS}_${MTX_ARCH}.tar.gz"

echo "Downloading from $URL..."
curl -sL "$URL" -o "$BIN_DIR/mediamtx.tar.gz"
tar -xzf "$BIN_DIR/mediamtx.tar.gz" -C "$BIN_DIR"
rm "$BIN_DIR/mediamtx.tar.gz"
chmod +x "$BIN_DIR/mediamtx"

echo "MediaMTX installed successfully:"
"$BIN_DIR/mediamtx" --version
