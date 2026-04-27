@echo off
setlocal

cd /d "%~dp0"

title 2330dmi ngrok
ngrok http 8000

endlocal
