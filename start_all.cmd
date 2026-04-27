@echo off
setlocal

cd /d "%~dp0"

start "2330dmi API" cmd /k call "%~dp0start_api.cmd"
timeout /t 1 >nul
start "2330dmi ngrok" cmd /k call "%~dp0start_ngrok.cmd"
timeout /t 1 >nul
start "2330dmi Shell" cmd /k call "%~dp0start_shell.cmd"
timeout /t 1 >nul
start "2330dmi Watcher" cmd /k call "%~dp0start_watcher.cmd"
timeout /t 2 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; & '%~dp0arrange_windows.ps1'"

endlocal
