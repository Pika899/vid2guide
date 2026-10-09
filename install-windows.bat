@echo off
rem vid2guide installer for Windows: double-click this file.
rem Runs scripts\install-windows.ps1 without changing the system execution policy.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1"
echo.
pause
