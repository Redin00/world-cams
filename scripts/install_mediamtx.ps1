# WorldCams MediaMTX Downloader and Setup for Windows
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BinDir = Join-Path $ProjectRoot "bin"
$Version = "v1.21.2"
$ExePath = Join-Path $BinDir "mediamtx.exe"

Write-Host "=== Setting up MediaMTX ($Version) for Windows ===" -ForegroundColor Cyan

if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

if (Test-Path $ExePath) {
    Write-Host "[✓] MediaMTX is already installed at $ExePath" -ForegroundColor Green
    & $ExePath --version
    exit 0
}

$ZipUrl = "https://github.com/bluenviron/mediamtx/releases/download/$Version/mediamtx_${Version}_windows_amd64.zip"
$ZipPath = Join-Path $BinDir "mediamtx.zip"

Write-Host "[*] Downloading MediaMTX Windows x64 from $ZipUrl..." -ForegroundColor Yellow
Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing

Write-Host "[*] Extracting archive..." -ForegroundColor Yellow
Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
Remove-Item -Path $ZipPath -Force

if (Test-Path $ExePath) {
    Write-Host "[✓] MediaMTX installed successfully:" -ForegroundColor Green
    & $ExePath --version
} else {
    Write-Error "Failed to install MediaMTX. mediamtx.exe not found in $BinDir"
}
