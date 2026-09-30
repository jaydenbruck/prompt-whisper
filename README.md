# Prompt Whisper

Press **Ctrl+Space**, talk, press **Ctrl+Space** again. Your words are typed into whatever app you were
using.

Prompt Whisper is a small dictation app for Windows and macOS. It transcribes your voice locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and pastes the text at your cursor. It works
in any text field: your editor, a terminal, a chat box, a prompt to a coding agent.

- **Local.** The Whisper model runs on your computer. No cloud service, no API key, no account.
- **Nothing is kept.** The audio is written to a temp file only until it has been transcribed, then
  deleted. The text goes to your clipboard and nowhere else. There is no history file and no log of what
  you said.
- **Light when idle.** The model loads in a separate worker process while you are still talking, and is
  released after 90 seconds without a dictation. Between dictations the app uses about 50 MB of RAM.

## Install: paste one prompt into your agent

Open Claude Code, Codex, Cursor or Grok and paste:

```
Install Prompt Whisper for me: clone https://github.com/jaydenbruck/prompt-whisper and follow AGENT_SETUP.md in it.
```

The agent asks you three short questions (your language, words to spell right, start at login), checks
the prerequisites, installs everything, downloads the model, proves transcription works, and starts the
app. [AGENT_SETUP.md](AGENT_SETUP.md) is what it follows.

## Controls

| key | action |
|---|---|
| `Ctrl+Space` | start dictating; press again to stop, transcribe and paste |
| `Esc` | cancel the current dictation |

While you talk, a small waveform pill floats near the bottom of the screen. Quit from the tray
(Windows) or menu-bar (macOS) icon.

## Platforms

| | Windows | macOS |
|---|---|---|
| status | tested | **not yet tested on a Mac** |
| needs | Windows 10/11, Python 3.10+ | macOS on Apple Silicon or Intel, Homebrew, Python 3.10+ with Tk |
| speed | NVIDIA GPU with CUDA 12: well under a second per dictation; CPU works too | runs on the CPU (faster-whisper has no Apple GPU backend) |
| install | `setup.bat`, then `run.bat` | `sh setup.sh`, then `sh run.sh` |
| paste | Ctrl+V through the Win32 SendInput API | Cmd+V through pynput |

### Manual install

```
git clone https://github.com/jaydenbruck/prompt-whisper.git
cd prompt-whisper
setup.bat      then   run.bat          (Windows)
sh setup.sh    then   sh run.sh        (macOS)
```

The setup script creates a virtual environment, installs the dependencies, and downloads the Whisper
model into `models/` (large-v3-turbo, about 1.6 GB, one time). On macOS it also installs PortAudio with
Homebrew, which the microphone library needs.

`python selftest.py` (with the venv's Python) speaks a sentence with the computer's built-in voice and
transcribes it, so you can check the model works without a microphone.

To start it with Windows, put a shortcut to `run.bat` in `shell:startup`.

### macOS notes

- The first time, macOS asks to allow **Microphone**, **Accessibility** (to paste) and **Input
  Monitoring** (for the Ctrl+Space hotkey) for the terminal app that started Prompt Whisper. All three are
  needed. If you missed a prompt, allow the terminal app in System Settings > Privacy & Security and run
  `sh run.sh` again.
- macOS can use Ctrl+Space to switch keyboard languages. If it does on your Mac, turn that off in System
  Settings > Keyboard > Keyboard Shortcuts > Input Sources.
- The Mac version is written but has **not been tested on a real Mac yet**. Reports and fixes are welcome.

## The model

The model is downloaded from Hugging Face (Systran's faster-whisper conversions) the first time, then
used offline. To pick another size:

```
venv/bin/python download_model.py small        (Windows: venv\Scripts\python download_model.py small)
```

and set `WHISPER_MODEL=small` in `.env`.

| model | download | notes |
|---|---|---|
| `large-v3-turbo` | 1.6 GB | default; very accurate; fast on an NVIDIA GPU, slower on a CPU |
| `large-v3` | 3 GB | most accurate, slower |
| `medium` | 1.5 GB | |
| `small` | 500 MB | good choice for CPU-only PCs and Intel Macs |
| `base` / `tiny` | 150 / 75 MB | fastest, least accurate |

`WHISPER_MODEL` also accepts a path to a local faster-whisper model folder.

## Settings

Copy `.env.example` to `.env` and uncomment what you want to change:

| setting | default | meaning |
|---|---|---|
| `WHISPER_MODEL` | `large-v3-turbo` | model size or local model folder |
| `WHISPER_LANGUAGE` | auto-detect | force a language, e.g. `en` or `de` |
| `WHISPER_VOCAB` | empty | comma-separated words Whisper keeps misspelling (names, product terms) |
| `WHISPER_IDLE_TIMEOUT` | `90` | seconds the model stays loaded after a dictation; `0` releases at once, `-1` keeps it loaded in the app |
| `MODELS_DIR` | `models/` | where models are stored |
| `SAMPLE_RATE` | `16000` | microphone sample rate |

## Transcription cleanup

Whisper sometimes invents text from silence ("Thank you.", "Thanks for watching") and can get stuck
repeating a word. Prompt Whisper filters silence with VAD before transcribing, then strips those
phrases when they are the whole result or trail off the end, and collapses runs like "the the the".
The rules are in `text_corrections.py`.

## How it works

```
main.py              tray icon, Ctrl+Space hotkey, record -> transcribe -> clipboard -> paste
audio_recorder.py    microphone capture, streamed to a temp WAV
whisper_service.py   starts, times out and restarts the Whisper worker; falls back to CPU if the GPU fails
whisper_worker.py    separate process that owns the model and answers over a JSON pipe
whisper_stt.py       faster-whisper settings (VAD, anti-hallucination thresholds)
text_corrections.py  cleanup of repetition and silence hallucinations
paste.py             Ctrl+V (Windows) or Cmd+V (macOS) into the app you were using
ui.py                the waveform overlay
download_model.py    fetches a model into models/
selftest.py          transcribes a spoken test sentence to check the setup
```

Each transcription has a time limit. If the worker hangs (a GPU that loads but cannot compute), it is
killed and the dictation is retried on a fresh worker, then on the CPU.

## Tests

```
venv/bin/pip install pytest && venv/bin/python -m pytest tests
```

(Windows: `venv\Scripts\...`.)

## License

MIT, © 2026 Jayden Bruck.
