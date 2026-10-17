@echo off
setlocal enabledelayedexpansion

title WorldCams Launcher

echo ==========================================================
echo   WORLD-CAMS // Exposed RTSP Intelligence ^& Viewer (Windows)
echo ==========================================================

cd /d "%~dp0"

:: Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from https://python.org and check "Add python.exe to PATH".
    pause
    exit /b 1
)

:: Run launcher via PowerShell
powershell -ExecutionPolicy Bypass -File "scripts\run.ps1" %*

pause
