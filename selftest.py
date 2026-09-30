"""
Check that transcription works, without a microphone.

    python selftest.py               # speaks a test sentence with the OS voice, then transcribes it
    python selftest.py some.wav      # transcribe a file you already have

Uses the model and settings from .env, in this process. Prints the text and
how long it took.
"""
import os
import subprocess
import sys
import tempfile
import time

SENTENCE = "Prompt Whisper is working. This sentence was spoken by the computer."


def make_sample() -> str:
    path = os.path.join(tempfile.gettempdir(), "prompt_whisper_selftest.wav")
    if sys.platform == "darwin":
        subprocess.run(["say", "-o", path, "--data-format=LEI16@16000", SENTENCE], check=True)
    elif sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.SetOutputToWaveFile('%s'); $s.Speak('%s'); $s.Dispose()" % (path, SENTENCE))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    else:
        raise SystemExit("No built-in voice on this OS; pass a WAV file: python selftest.py file.wav")
    return path


def main() -> int:
    from whisper_stt import WhisperSTT
    from config import WHISPER_MODEL

    path = sys.argv[1] if len(sys.argv) > 1 else make_sample()
    stt = WhisperSTT(WHISPER_MODEL)
    t0 = time.time()
    device = stt.ensure_working()
    t1 = time.time()
    text = stt.transcribe(path)
    t2 = time.time()
    print(f"model {WHISPER_MODEL} on {device}: loaded in {t1 - t0:.1f}s, transcribed in {t2 - t1:.1f}s")
    print(f"TEXT: {text}")
    return 0 if text.strip() else 1


if __name__ == "__main__":
    sys.exit(main())
