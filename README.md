# WorldCams: Exposed Camera Intelligence & Security Awareness Platform

[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows%20%7C%20macOS-blue.svg?style=flat)](https://github.com)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![MediaMTX](https://img.shields.io/badge/Streaming-MediaMTX-red.svg?style=flat)](https://github.com/bluenviron/mediamtx)
[![Shodan](https://img.shields.io/badge/OSINT-Shodan%20API-red.svg?style=flat)](https://www.shodan.io)
[![Leaflet](https://img.shields.io/badge/Map-Leaflet.js-199900.svg?style=flat&logo=leaflet)](https://leafletjs.com)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat&logo=python)](https://python.org)

**WorldCams** is a cybersecurity intelligence dashboard that discovers, maps, and audits internet-exposed IP surveillance cameras (RTSP, HTTP/MJPEG, and Web Management Consoles). It bridges RTSP streams to browser-native WebRTC via **MediaMTX**, handles SSL/TLS legacy camera quirks (TLS 1.0/1.1 and unencrypted port 443), and visualizes global exposures on an interactive map. 


_(A 10,000 cams database scraped from shodan API is already installed, feel free to delete and make one yourself if you want)._

---

## ⚡ Quickstart

### Prerequisites
- **Python 3.10+** (installed and added to PATH)
- Works natively on **Linux** (x86_64, ARM64/aarch64), **Windows** (x86_64), and **macOS**.

---

### 🐧 Linux / macOS

Run the supervisor script (automatically configures virtualenv, installs dependencies, downloads MediaMTX, and starts all services):

```bash
./scripts/run.sh
```

**Need to change the port?** (e.g. if port 8000 is occupied):
```bash
# Pass custom port directly:
./scripts/run.sh 8080

# Or using environment variable:
PORT=8080 ./scripts/run.sh
```

---

### 🪟 Windows (Native)

You can launch WorldCams with a single click:
- **Double-click `run.bat`** in the project root folder.
- Or pass a custom port from Command Prompt / PowerShell:
  ```cmd
  run.bat 8080
  ```
- Or run via **PowerShell**:
  ```powershell
  .\scripts\run.ps1 -Port 8080
  ```

The Windows launcher automatically:
1. Creates `venv\` and installs dependencies from `requirements.txt`.
2. Downloads and extracts the official 64-bit `mediamtx.exe` binary into `bin\`.
3. Starts **MediaMTX** and **FastAPI** concurrently, with automatic clean shutdown on `Ctrl+C`.

---

Once started, open your browser to:
👉 **`http://localhost:8000`** *(or your chosen custom port)*

---

## 🕹️ How to Use

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

# Web UI Server Bindings
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=True

# Optional: Set your VPS Public IP or Domain if behind NAT/reverse proxy
# (If omitted, WorldCams automatically detects it from the incoming Host header)
# MEDIAMTX_PUBLIC_HOST=your-vps-ip-or-domain

# MediaMTX Ports
MEDIAMTX_RTSP_PORT=8554
MEDIAMTX_API_URL=http://localhost:9997
MEDIAMTX_WHEP_URL=http://localhost:8889
MEDIAMTX_HLS_URL=http://localhost:8888
```

---

## 🌐 VPS / Remote Deployment (Oracle Linux, AWS, Hetzner, etc.)

When accessing WorldCams from an external device or different internet connection:

### 1. Firewall & Cloud Security Group Ports
Ensure the following ports are open in both your **VPS Cloud Security List / Security Group** and local firewall:

| Port | Protocol | Purpose |
|------|----------|---------|
| `8000` | TCP | FastAPI Web UI, API, MJPEG & Web Proxy Gateway |
| `8889` | TCP | MediaMTX WebRTC (WHEP) Player Frame |
| `8888` | TCP | MediaMTX HLS Stream Fallback |
| `8189` | UDP / TCP | MediaMTX WebRTC Media Track Transmission |

On **Oracle Linux / RHEL / CentOS (`firewalld`)**:
```bash
sudo firewall-cmd --permanent --add-port=8000/tcp --add-port=8889/tcp --add-port=8888/tcp --add-port=8189/udp --add-port=8189/tcp
sudo firewall-cmd --reload
```

On **Ubuntu / Debian (`ufw`)**:
```bash
sudo ufw allow 8000/tcp
sudo ufw allow 8889/tcp
sudo ufw allow 8888/tcp
sudo ufw allow 8189/udp
sudo ufw allow 8189/tcp
```

> [!NOTE]
> On Oracle Cloud, AWS, or GCP, remember to also add matching **Ingress Rules** in the Cloud Web Console (VCN Security List / Security Group).


---

## 🧪 Testing

Run the automated test suite to verify database models, streaming proxy, resolver, and admin permissions:

```bash
# Linux / macOS
./venv/bin/python3 tests/test_api.py

# Windows
.\venv\Scripts\python.exe tests\test_api.py
```

---

## ⚖️ Legal & Ethical Disclaimer

> [!WARNING]
> This platform is built solely for **educational, defensive auditing, and authorized security research purposes**.
> - Accessing or viewing non-public surveillance systems without authorization violates computer crime and privacy laws (e.g. U.S. CFAA, UK Computer Misuse Act, EU GDPR).
> - Do not attempt to bypass access controls or intercept private communications.
> - Report vulnerable infrastructure responsibly via Coordinated Vulnerability Disclosure (CVD).
