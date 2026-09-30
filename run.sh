#!/bin/sh
# Start Prompt Whisper in the background (macOS). Stop it from its menu-bar icon.
cd "$(dirname "$0")"
if [ ! -x venv/bin/python ]; then
    echo "Run ./setup.sh first." >&2
    exit 1
fi
nohup venv/bin/python main.py >/dev/null 2>&1 &
echo "Prompt Whisper is running. Press Ctrl+Space to dictate."