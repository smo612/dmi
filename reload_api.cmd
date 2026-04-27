@echo off
setlocal

cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\jing5\anaconda3\python.exe"

title 2330dmi Reload API
"%PYTHON_EXE%" -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/reload').read().decode('utf-8', errors='ignore'))"

endlocal
