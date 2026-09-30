@echo off
cd /d "%~dp0"
if not exist venv\Scripts\pythonw.exe (
    echo Run setup.bat first.
    exit /b 1
)
start "" venv\Scripts\pythonw.exe main.py
echo Prompt Whisper is running in the tray. Press Ctrl+Space to dictate.