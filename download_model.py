"""
Download the Whisper model before first use.

    python download_model.py                  # the model named in .env (default large-v3-turbo)
    python download_model.py small            # any other size

Models come from Hugging Face (Systran's faster-whisper conversions) and are
stored in the models/ folder next to this script. Running the app without
this step also works: the model is then downloaded the first time you dictate.
"""
import os
import sys

from faster_whisper.utils import download_model

from config import MODELS_DIR, WHISPER_MODEL

SIZES = {
    "tiny": "75 MB", "base": "150 MB", "small": "500 MB", "medium": "1.5 GB",
    "large-v3": "3 GB", "large-v3-turbo": "1.6 GB",
}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else WHISPER_MODEL
    if os.path.isdir(name):
        print(f"{name} is a local folder; nothing to download.")
        return 0
    print(f"Downloading Whisper model '{name}' ({SIZES.get(name, 'size unknown')}) into {MODELS_DIR} ...")
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = download_model(name, cache_dir=MODELS_DIR)
    print(f"Done: {path}")
    if name != WHISPER_MODEL:
        print(f"To use it, set WHISPER_MODEL={name} in .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
