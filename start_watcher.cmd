@echo off
setlocal

cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\jing5\anaconda3\python.exe"

title 2330dmi Fubon Watcher
"%PYTHON_EXE%" fubon_intraday_watcher.py --intraday-days 1 --poll-seconds 30 --poll-offhours-seconds 300 --request-gap-seconds 0.15 --reload-url http://127.0.0.1:8000/reload

endlocal
