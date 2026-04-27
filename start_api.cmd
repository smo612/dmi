@echo off
setlocal

cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\jing5\anaconda3\python.exe"

title 2330dmi API
"%PYTHON_EXE%" -m uvicorn backend_api:app --host 0.0.0.0 --port 8000 --reload

endlocal
