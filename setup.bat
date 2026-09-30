@echo off
setlocal
cd /d "%~dp0"
echo Creating virtual environment...
py -3 -m venv venv || python -m venv venv || goto :fail
call venv\Scripts\activate.bat
echo Installing dependencies...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || goto :fail
echo Downloading the Whisper model (one time)...
python download_model.py || goto :fail
echo.
echo Done. Start Prompt Whisper with run.bat, then press Ctrl+Space and talk.
exit /b 0
:fail
echo Setup failed. See the messages above.
exit /b 1