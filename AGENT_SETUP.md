# Setting up Prompt Whisper

These steps are for a coding agent (Claude Code, Codex, Cursor, Grok, ...) that a user asked to install
Prompt Whisper. Do every step yourself. Stop to ask the user only where a step says so, and ask all of
those questions together at the start.

Prompt Whisper is a push-to-talk dictation app: Ctrl+Space starts recording, Ctrl+Space again
transcribes locally with Whisper and pastes the text at the cursor. It keeps no recordings and no
transcripts.

## 0. Ask the user (one message)

1. Which language(s) do they dictate in? If it is always one language, you will set `WHISPER_LANGUAGE`.
2. Any names or terms Whisper should spell right (company, product, people)? These go in `WHISPER_VOCAB`.
3. Windows only: should Prompt Whisper start automatically when they log in?
4. May you install missing prerequisites (Python, Homebrew packages) if you find any?

## 1. Get the code

Install to `~/prompt-whisper` (Windows: `%USERPROFILE%\prompt-whisper`) unless the user named another
folder. If that folder already holds a clone of this repository, run `git pull` in it instead.

```
git clone https://github.com/jaydenbruck/prompt-whisper.git ~/prompt-whisper
```

## 2. Check prerequisites

**Windows**
- Python 3.10 or newer: `py -3 --version`. If missing, and the user agreed: `winget install -e --id Python.Python.3.12`.

**macOS** (the Mac version has not been tested yet; tell the user so)
- Homebrew: `brew --version`. If missing, send the user to https://brew.sh; it needs their password.
- Python 3.10 or newer with Tk: `python3 -c "import sys, tkinter; print(sys.version)"`. If that fails:
  `brew install python@3.12 python-tk@3.12`.

## 3. Pick the model for this computer

Prompt Whisper runs Whisper on the user's own hardware, so the right model size depends on the machine.
Look at it first:

```
Windows:  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader     (fails = no NVIDIA GPU)
          powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB); (Get-CimInstance Win32_Processor).Name"
macOS:    uname -m                                   (arm64 = Apple Silicon)
          sysctl -n hw.memsize machdep.cpu.brand_string
```

Then choose from this table:

| the computer has | `WHISPER_MODEL` | `WHISPER_KEEP_LOADED` |
|---|---|---|
| NVIDIA GPU with 4 GB VRAM or more | `large-v3-turbo` | `1` |
| NVIDIA GPU with less than 4 GB VRAM | `small` | `1` |
| Apple Silicon Mac with 16 GB RAM or more | `large-v3-turbo` | `1` |
| Apple Silicon Mac with 8 GB RAM | `small` | `1` |
| no NVIDIA GPU (or an Intel Mac), 16 GB RAM or more | `small` | `1` |
| no NVIDIA GPU (or an Intel Mac), 8 GB RAM | `base` | `1` |
| less than 8 GB RAM | `base` | `0` |

What the settings mean:

- **Model size** trades accuracy for speed. Without an NVIDIA GPU, Whisper runs on the CPU, where each
  size step is several times slower. faster-whisper has no Apple GPU backend, so Macs use the CPU too.
- **`WHISPER_KEEP_LOADED=1`** (the default) loads the model when the app starts and keeps it, so every
  dictation is transcribed at once. The model's memory stays in use: large-v3-turbo holds about 0.7 GB of
  RAM plus 2.2 GB of VRAM on a GPU, or 0.9 GB of RAM on a CPU; small about 0.4 GB; base about 0.2 GB.
  **`0`** loads the model only while the user dictates and frees it after 90 idle seconds. That saves
  memory, but the first dictation after a pause waits for the model to load (up to 10-20 seconds).
  Use `0` only on machines that are short on memory.
- If the user said accuracy matters more to them than speed, you may go one size up. If they said the
  computer is usually busy with heavy work, you may go one size down.

Tell the user in one line which model you picked and why.

## 4. Write `.env`

Copy `.env.example` to `.env` in the install folder and set what applies (python-dotenv reads `KEY=value`
lines; keep comments on their own lines):

```
WHISPER_MODEL=small
WHISPER_KEEP_LOADED=1
WHISPER_LANGUAGE=en
WHISPER_VOCAB=Name1, Product2, Term3
```

Leave out `WHISPER_LANGUAGE` if the user speaks more than one language, and `WHISPER_VOCAB` if they named
no terms.

## 5. Install

Run the setup script from the install folder. It creates `venv`, installs the dependencies, and downloads
the model named in `.env` (large-v3-turbo is about 1.6 GB, so give this up to 15 minutes).

- Windows: `setup.bat`
- macOS: `sh setup.sh` (installs PortAudio with Homebrew if needed)

## 6. Check transcription and speed

```
Windows:  venv\Scripts\python selftest.py
macOS:    venv/bin/python selftest.py
```

It speaks a test sentence (about five seconds) with the computer's built-in voice ("Prompt Whisper is
working. This sentence was spoken by the computer.") and transcribes it. It prints a line like
`model small on cpu: loaded in 6.1s, transcribed in 1.2s` and then `TEXT: ...`.

- The `TEXT:` line should be close to the sentence; the robot voice and smaller models garble a word or
  two, which is fine. An error or an empty result is not: fix it before going on.
- If `transcribed in` is over 3 seconds, the model is too big for this computer: step one size down
  (large-v3-turbo -> small -> base), download it with `venv/bin/python download_model.py <size>` (Windows:
  `venv\Scripts\python`), update `.env`, and run the self-test again.
- If it says `on cpu` but the computer has an NVIDIA GPU, CUDA did not load. It still works, just slower;
  mention it to the user.

## 7. Start at login (Windows, if the user said yes)

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Prompt Whisper.lnk")
$s.TargetPath = "$env:USERPROFILE\prompt-whisper\venv\Scripts\pythonw.exe"
$s.Arguments = "main.py"
$s.WorkingDirectory = "$env:USERPROFILE\prompt-whisper"
$s.Save()
```

(Adjust the paths if you installed somewhere else.)

## 8. Start it

- Windows: `run.bat`
- macOS: `sh run.sh`

## 9. Tell the user

- Which model you picked, and whether it stays loaded.
- Press **Ctrl+Space**, talk, press **Ctrl+Space** again: the text is pasted where the cursor is. **Esc** cancels.
- With `WHISPER_KEEP_LOADED=1` the model is loading for a few seconds right after the app starts; after
  that every dictation is quick.
- macOS only:
  - The first time, macOS asks to allow **Microphone**, **Accessibility** and **Input Monitoring** for the
    terminal app that started Prompt Whisper. All three are needed. If a prompt was missed: System
    Settings > Privacy & Security, allow the terminal app in each, then run `sh run.sh` again.
  - If Ctrl+Space switches the keyboard language instead, turn that shortcut off in System Settings >
    Keyboard > Keyboard Shortcuts > Input Sources.
  - The Mac version has not been tested on a real Mac yet.
- Quit it from the tray (Windows) or menu-bar (macOS) icon.
