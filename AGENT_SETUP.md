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
- NVIDIA GPU? Run `nvidia-smi`. If it works, transcription runs on the GPU and the default model is right.
  If not, it runs on the CPU: use the `small` model (step 3).

**macOS** (the Mac version has not been tested yet; tell the user so)
- Homebrew: `brew --version`. If missing, send the user to https://brew.sh; it needs their password.
- Python 3.10 or newer with Tk: `python3 -c "import sys, tkinter; print(sys.version)"`. If that fails:
  `brew install python@3.12 python-tk@3.12`.
- Apple Silicon (`uname -m` prints `arm64`): keep the default model. Intel Mac: use `small` (step 3).

## 3. Write `.env`

Copy `.env.example` to `.env` in the install folder and set what applies:

```
WHISPER_MODEL=small                    # only for CPU-only Windows PCs and Intel Macs
WHISPER_LANGUAGE=en                    # only if the user always speaks one language
WHISPER_VOCAB=Name1, Product2, Term3   # the user's answer to question 2
```

## 4. Install

Run the setup script from the install folder. It creates `venv`, installs the dependencies, and downloads
the Whisper model (large-v3-turbo is about 1.6 GB, so give this up to 15 minutes).

- Windows: `setup.bat`
- macOS: `sh setup.sh` (installs PortAudio with Homebrew if needed)

## 5. Check transcription

```
Windows:  venv\Scripts\python selftest.py
macOS:    venv/bin/python selftest.py
```

It speaks a test sentence with the computer's built-in voice ("Prompt Whisper is working. This sentence
was spoken by the computer.") and transcribes it. Expect a `TEXT:` line close to that sentence; the robot
voice and smaller models garble a word or two, which is fine. An error or an empty result is not: fix
that before going on.

## 6. Start at login (Windows, if the user said yes)

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Prompt Whisper.lnk")
$s.TargetPath = "$env:USERPROFILE\prompt-whisper\venv\Scripts\pythonw.exe"
$s.Arguments = "main.py"
$s.WorkingDirectory = "$env:USERPROFILE\prompt-whisper"
$s.Save()
```

(Adjust the paths if you installed somewhere else.)

## 7. Start it

- Windows: `run.bat`
- macOS: `sh run.sh`

## 8. Tell the user

- Press **Ctrl+Space**, talk, press **Ctrl+Space** again: the text is pasted where the cursor is. **Esc** cancels.
- The first dictation after a pause takes a few extra seconds while the model loads.
- macOS only:
  - The first time, macOS asks to allow **Microphone**, **Accessibility** and **Input Monitoring** for the
    terminal app that started Prompt Whisper. All three are needed. If a prompt was missed: System
    Settings > Privacy & Security, allow the terminal app in each, then run `sh run.sh` again.
  - If Ctrl+Space switches the keyboard language instead, turn that shortcut off in System Settings >
    Keyboard > Keyboard Shortcuts > Input Sources.
  - The Mac version has not been tested on a real Mac yet.
- Quit it from the tray (Windows) or menu-bar (macOS) icon.
