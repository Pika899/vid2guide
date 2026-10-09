@echo off
rem vid2guide launcher for Windows: double-click to open the app in the browser.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo vid2guide non e' ancora installato: fai prima doppio clic su install-windows.bat.
  pause
  exit /b 1
)
chcp 65001 >nul
".venv\Scripts\python.exe" -m app.server
if errorlevel 1 (
  echo.
  echo vid2guide si e' chiuso con un errore.
  pause
)
