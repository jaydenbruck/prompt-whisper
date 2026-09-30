#!/bin/sh
# Prompt Whisper setup for macOS: virtual environment, dependencies, Whisper model.
set -e
cd "$(dirname "$0")"

PY=$(command -v python3 || true)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Prompt Whisper needs Python 3.10+. Install it with: brew install python@3.12" >&2
    exit 1
fi
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "This Python has no Tk support. Install it with: brew install python-tk@3.12 (match your Python version)" >&2
    exit 1
fi

# PyAudio builds against PortAudio.
if [ "$(uname)" = Darwin ]; then
    if ! command -v brew >/dev/null 2>&1; then
        echo "PyAudio needs PortAudio from Homebrew. Install Homebrew first: https://brew.sh" >&2
        exit 1
    fi
    brew list portaudio >/dev/null 2>&1 || brew install portaudio
    BREW=$(brew --prefix)
    export CFLAGS="-I$BREW/include ${CFLAGS:-}" LDFLAGS="-L$BREW/lib ${LDFLAGS:-}"
fi

echo "Creating virtual environment..."
"$PY" -m venv venv
. venv/bin/activate
python -m pip install --upgrade pip >/dev/null
echo "Installing dependencies..."
pip install -r requirements.txt
echo "Downloading the Whisper model (one time)..."
python download_model.py
echo
echo "Done. Start Prompt Whisper with ./run.sh, then press Ctrl+Space and talk."
echo "macOS will ask for Microphone, Accessibility and Input Monitoring permission for your terminal app: allow all three."