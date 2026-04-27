@echo off
setlocal

if /i not "%~1"=="child" (
    start "2330dmi Shell" cmd /k call "%~f0" child
    exit /b
)

cd /d "%~dp0"
call "C:\Users\jing5\anaconda3\Scripts\activate.bat" purple

title 2330dmi Shell
echo purple environment ready.
