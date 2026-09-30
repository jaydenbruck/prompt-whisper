"""
Prompt Whisper - configuration.

Every setting can be overridden in a .env file next to main.py (see
.env.example). The defaults work out of the box.
"""
import os
import sys

from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(APP_DIR, ".env"))

# Whisper model: a size name (tiny, base, small, medium, large-v3,
# large-v3-turbo) or a path to a local CTranslate2 model folder.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")

# Where downloaded models are kept. download_model.py fills it; if it is empty
# the model is downloaded on first use instead.
MODELS_DIR = os.getenv("MODELS_DIR", os.path.join(APP_DIR, "models"))

# Spoken language as an ISO code ("en", "de", ...). Empty = auto-detect.
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "").strip() or None

# Optional comma-separated words Whisper tends to misspell (names, product
# terms). They are fed to Whisper as a spelling hint.
WHISPER_VOCAB = [w.strip() for w in os.getenv("WHISPER_VOCAB", "").split(",") if w.strip()]

# How long the model may sit idle before it is released, in seconds.
#
# The model does not live in the tray app: it runs in a worker process that is
# spawned when you press the hotkey (loading while you talk, so it costs no
# waiting) and shut down again once you have been idle this long.
#
#   90  (default) keeps the model warm through a burst of dictations
#   0   releases it the moment each transcription finishes
#   -1  loads Whisper into the app process and keeps it there
WHISPER_IDLE_TIMEOUT = float(os.getenv("WHISPER_IDLE_TIMEOUT", "90"))

# The hotkey is fixed: Ctrl+Space starts and stops a dictation, Esc cancels.
HOTKEY = "ctrl+space"

# Microphone sample rate. Whisper works at 16 kHz internally.
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
