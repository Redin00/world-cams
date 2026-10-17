param (
    [string]$Port = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  WORLD-CAMS // Exposed RTSP Intelligence & Viewer (Windows)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Python
try {
    $pyVer = & python --version 2>&1
    Write-Host "[✓] Found Python: $pyVer" -ForegroundColor Green
} catch {
    Write-Error "Python 3 is not found in PATH. Please install Python 3.10+ from python.org and check 'Add python.exe to PATH'."
}

# 2. Virtual Environment
$VenvDir = Join-Path $ProjectRoot "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[*] Creating virtual environment..." -ForegroundColor Yellow
    & python -m venv venv
    Write-Host "[*] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
    & $VenvPip install --upgrade pip
    & $VenvPip install -r requirements.txt
}

# 3. MediaMTX binary
$BinExe = Join-Path $ProjectRoot "bin\mediamtx.exe"
if (-not (Test-Path $BinExe)) {
    Write-Host "[*] MediaMTX binary not found. Downloading..." -ForegroundColor Yellow
    & powershell -ExecutionPolicy Bypass -File "$ProjectRoot\scripts\install_mediamtx.ps1"
}

# 4. Check .env and resolve port
$AppPort = "8000"
$AppHost = "0.0.0.0"

if (Test-Path "$ProjectRoot\.env") {
    Get-Content "$ProjectRoot\.env" | ForEach-Object {
        if ($_ -match '^\s*APP_PORT\s*=\s*(.+)$') { $AppPort = $matches[1].Trim('"'' ') }
        if ($_ -match '^\s*APP_HOST\s*=\s*(.+)$') { $AppHost = $matches[1].Trim('"'' ') }
    }
}

if ($Port -ne "") {
    $AppPort = $Port
}

$DisplayHost = if ($AppHost -eq "0.0.0.0") { "localhost" } else { $AppHost }

# 5. Start MediaMTX
Write-Host "[*] Starting MediaMTX streaming gateway..." -ForegroundColor Green
$mtxProcess = Start-Process -FilePath $BinExe -ArgumentList "$ProjectRoot\mediamtx.yml" -PassThru -NoNewWindow
Start-Sleep -Seconds 1

# 6. Start FastAPI
Write-Host "[*] Starting FastAPI backend on http://${DisplayHost}:${AppPort}..." -ForegroundColor Green
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  [✓] WorldCams is running!" -ForegroundColor Green
Write-Host "  -> Web UI & Dashboard: http://${DisplayHost}:${AppPort}" -ForegroundColor Cyan
Write-Host "  -> API Documentation: http://${DisplayHost}:${AppPort}/docs" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop all services." -ForegroundColor DarkGray
Write-Host ""

try {
    & $VenvPython -m uvicorn backend.main:app --host $AppHost --port $AppPort --reload
} finally {
    Write-Host "`n[*] Stopping MediaMTX..." -ForegroundColor Yellow
    if ($mtxProcess -and -not $mtxProcess.HasExited) {
        Stop-Process -Id $mtxProcess.Id -Force
    }
    Write-Host "[✓] All services stopped cleanly." -ForegroundColor Green
}
