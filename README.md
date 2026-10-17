# WorldCams: Exposed Camera Intelligence & Security Awareness Platform

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![MediaMTX](https://img.shields.io/badge/Streaming-MediaMTX-red.svg?style=flat)](https://github.com/bluenviron/mediamtx)
[![Shodan](https://img.shields.io/badge/OSINT-Shodan%20API-red.svg?style=flat)](https://www.shodan.io)
[![Leaflet](https://img.shields.io/badge/Map-Leaflet.js-199900.svg?style=flat&logo=leaflet)](https://leafletjs.com)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat&logo=python)](https://python.org)

**WorldCams** is a cybersecurity intelligence dashboard that discovers, maps, and audits internet-exposed IP surveillance cameras (RTSP, HTTP/MJPEG, and Web Management Consoles). It bridges RTSP streams to low-latency WebRTC via **MediaMTX**, handles SSL/TLS legacy camera quirks, and visualizes global exposures on an interactive map.

---

## ⚡ Quickstart

### Prerequisites
- Linux (x86_64 or aarch64)
- Python 3.10+
- `curl`

### 1. Launch with One Command

Run the supervisor script (handles virtualenv, dependencies, MediaMTX binary, and server startup):

```bash
./scripts/run.sh
```

The runner automatically:
1. Creates `venv` and installs packages from `requirements.txt`.
2. Downloads and verifies the standalone `mediamtx` streaming binary.
3. Starts **MediaMTX** (RTSP `:8554`, WebRTC `:8889`, API `:9997`).
4. Starts **FastAPI** on `http://localhost:8000`.

Open your browser to:
👉 **`http://localhost:8000`**

---

### Manual Launch (Alternative)

If you prefer running services in separate terminals:

```bash
# Terminal 1: MediaMTX streaming gateway
./bin/mediamtx mediamtx.yml

# Terminal 2: FastAPI backend
source venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## ️ How to Use

### 1. Discover & Ingest Cameras
Click the **🔍 Shodan Search** button in the top navigation bar. Two reconnaissance modes are supported:
- **Paid Shodan API**: Full search across Shodan's indexed database with custom query filters (e.g. `port:554 has_screenshot:true`, `product:"Hikvision"`).
- **Free Web Scraper (Zero API Credits)**: Scrapes publicly accessible live listings and enriches them with GeoIP data without consuming Shodan query credits.
- **Limit Selector**: Choose how many cameras to fetch (up to 8,000+ per batch).

### 2. View Live Video & Web Consoles
Click any camera card or map pin to load it into the central player:
- **RTSP Streams (`port 554`, `8554`)**: Automatically bridged to browser-native **WebRTC (WHEP)** for sub-second streaming with live telemetry.
- **HTTP / MJPEG Streams (`port 80`, `8080`, `8000`, `8050`)**: Proxied directly through the backend to eliminate browser mixed-content and CORS blocks.
- **Web Consoles (`port 443`, `80`)**: Embedded directly inside the player with security headers (CSP/X-Frame-Options) stripped for interactive web viewing.

### 3. Automatic SSL Error Bypass (Port 443)
Many surveillance cameras run outdated firmware that causes modern browsers to fail with SSL errors:
- **`SSL_ERROR_RX_RECORD_TOO_LONG`**: Occurs when a camera serves unencrypted plaintext HTTP on port 443. WorldCams detects this protocol mismatch and automatically falls back to plaintext HTTP through the proxy gateway.
- **`SSL_ERROR_UNSUPPORTED_VERSION`**: Occurs because modern browsers have disabled TLS 1.0 and TLS 1.1. WorldCams includes a dedicated legacy SSL context (`@SECLEVEL=0`) that bridges older camera handshakes and serves them cleanly to your browser.

### 4. Smart Vendor RTSP Auto-Resolver
When an RTSP stream fails to negotiate:
- Click **⚡ Auto-Resolve RTSP** to trigger the multi-vendor scanning engine.
- Tests known manufacturer subpaths (Hikvision, Dahua, Reolink, Axis, Tapo, Uniview, Mobotix, etc.).
- Performs RFC Digest Authentication against common factory default credentials.

### 5. Multi-Faceted Filtering & Map Navigation
- Filter instantaneously by **Port** (`554`, `443`, `80`, `8000`, `8080`), **Country**, **Vendor/Product**, or **Authentication Status**.
- Click **Triage Mode** to expand the player to a full-width monitoring workstation.
- Use the dark-mode **Leaflet Map** to zoom into specific geographical regions.

### 6. Admin Token & Batch Deletion
- **Admin Key**: Configured via `ADMIN_TOKEN` in `.env` (default: `worldcams-admin-2026`).
- Enter the admin token in **⚙️ Settings** to unlock feed deletion:
  - Delete individual cameras directly from the player toolbar.
  - Enable **Select Cards** on the feed browser to batch-delete multiple feeds in one click.

---

## ⚙️ Configuration (`.env`)

Create or edit `.env` in the root directory:

```env
# Optional: Shodan API Key (can also be entered via UI Settings)
SHODAN_API_KEY=your_shodan_key_here

# Admin Access Token (required for deleting camera feeds)
ADMIN_TOKEN=worldcams-admin-2026

# Server Bindings
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=True

# MediaMTX Ports
MEDIAMTX_RTSP_PORT=8554
MEDIAMTX_API_URL=http://localhost:9997
MEDIAMTX_WHEP_URL=http://localhost:8889
MEDIAMTX_HLS_URL=http://localhost:8888
```

---

## 🧪 Testing

Run the automated test suite to verify database models, streaming proxy, resolver, and admin permissions:

```bash
./venv/bin/python3 tests/test_api.py
```

---

## ⚖️ Legal & Ethical Disclaimer

> [!WARNING]
> This platform is built solely for **educational, defensive auditing, and authorized security research purposes**.
> - Accessing or viewing non-public surveillance systems without authorization violates computer crime and privacy laws (e.g. U.S. CFAA, UK Computer Misuse Act, EU GDPR).
> - Do not attempt to bypass access controls or intercept private communications.
> - Report vulnerable infrastructure responsibly via Coordinated Vulnerability Disclosure (CVD).
