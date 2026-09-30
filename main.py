"""
Prompt Whisper - press Ctrl+Space, talk, press Ctrl+Space again.

Your words are transcribed locally with Whisper and pasted into the app you
were using. Nothing is stored: the audio lives in a temp file only until it
has been transcribed, and the text only goes to your clipboard.
"""
import os
import sys

# The packaged .exe re-enters itself to run the out-of-process Whisper worker
# (see whisper_worker.py). This must happen before the stdout guard below,
# because the worker speaks JSON over stdout, and before the GUI imports,
# because the worker needs none of them.
if "--whisper-worker" in sys.argv:
    from whisper_worker import worker_main, parse_args
    _i = sys.argv.index("--whisper-worker")
    sys.exit(worker_main(*parse_args(sys.argv[_i + 1:])))

# pythonw.exe and windowed builds have no console: send diagnostics nowhere
# rather than crash on the first print. Transcripts are never printed anyway.
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _devnull
    sys.stderr = sys.stderr or _devnull
else:
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass

import ctypes
import queue
import tempfile
import threading
import time
import traceback
from typing import Optional

import customtkinter as ctk
import pyperclip
import pystray
from PIL import Image, ImageDraw
from pynput import keyboard

from audio_recorder import AudioRecorder
from config import HOTKEY, SAMPLE_RATE, WHISPER_IDLE_TIMEOUT, WHISPER_KEEP_LOADED, WHISPER_MODEL, APP_DIR
from paste import paste_from_clipboard
from ui import RecordingWindow
from whisper_service import WhisperService

IS_MAC = sys.platform == "darwin"
CTRL_KEYS = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}


def _mac_hide_dock_icon():
    """Accessory app: no Dock icon, and showing the overlay never takes focus from the app you dictate into."""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)   # NSApplicationActivationPolicyAccessory
    except Exception as e:
        print(f"Could not hide the Dock icon: {e}")


def _mac_frontmost_app():
    try:
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None


class PromptWhisperApp:
    def __init__(self):
        self.audio_recorder = AudioRecorder(sample_rate=SAMPLE_RATE)
        # Whisper does not live in this process - see whisper_service.py.
        # It is spawned on the hotkey and released when you stop dictating.
        self.whisper = WhisperService(
            model_name=WHISPER_MODEL,
            app_dir=APP_DIR,
            idle_timeout=WHISPER_IDLE_TIMEOUT,
            use_worker=WHISPER_IDLE_TIMEOUT >= 0,
            keep_loaded=WHISPER_KEEP_LOADED,
        )
        self.recording_window: Optional[RecordingWindow] = None
        self.is_processing = False
        self.temp_audio_file = os.path.join(tempfile.gettempdir(), "prompt_whisper_recording.wav")
        self.hotkey_listener = None
        self.tray_icon = None
        self.current_keys = set()
        self._root = None
        self._front_app = None   # macOS: the app to hand focus back to before pasting
        # Tk is single-threaded: every UI change from another thread goes through here.
        self.ui_queue = queue.Queue()

        print(f"Prompt Whisper ready. {HOTKEY} to start/stop dictation, Esc to cancel.")

    def start(self):
        # Keep-loaded mode: start loading the model now, so the first dictation does not wait for it.
        if self.whisper.keep_loaded:
            self.whisper.prewarm()
        self._root = ctk.CTk()
        self._root.withdraw()
        if IS_MAC:
            _mac_hide_dock_icon()
        self._check_ui_queue()
        self._setup_hotkey()
        self._setup_tray_icon()
        try:
            self.hotkey_listener.start()
            self._root.mainloop()
        except KeyboardInterrupt:
            self.cleanup()

    def _check_ui_queue(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        finally:
            self._root.after(100, self._check_ui_queue)

    # ---------- tray ----------

    def _create_tray_image(self):
        """A simple white microphone."""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([22, 8, 42, 20], fill="white")
        draw.rectangle([22, 14, 42, 38], fill="white")
        draw.ellipse([22, 30, 42, 44], fill="white")
        draw.arc([18, 28, 46, 52], start=0, end=180, fill="white", width=3)
        draw.rectangle([30, 52, 34, 58], fill="white")
        draw.rectangle([24, 56, 40, 60], fill="white")
        return img

    def _setup_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem("Prompt Whisper - Ctrl+Space to dictate", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self.ui_queue.put(self.cleanup)),
        )
        self.tray_icon = pystray.Icon("PromptWhisper", self._create_tray_image(), "Prompt Whisper", menu)
        if IS_MAC:
            # macOS allows one UI event loop, on the main thread; Tk runs it, the menu-bar icon rides along.
            try:
                self.tray_icon.run_detached()
            except Exception as e:
                print(f"Menu-bar icon unavailable ({e}); quit with Ctrl+C in the terminal.")
        else:
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    # ---------- hotkey ----------

    def _setup_hotkey(self):
        def on_press(key):
            try:
                self.current_keys.add(key)
                ctrl = bool(CTRL_KEYS & self.current_keys)
                if key == keyboard.Key.space and ctrl:
                    self._on_hotkey()
                elif key == keyboard.Key.esc and self.recording_window and self.recording_window.window:
                    self.ui_queue.put(self._on_recording_cancel)
            except Exception as e:
                print(f"Key press error: {e}")

        def on_release(key):
            self.current_keys.discard(key)

        self.hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)

    def _on_hotkey(self):
        if self.is_processing:
            return
        if IS_MAC and not (self.recording_window and self.recording_window.window):
            self._front_app = _mac_frontmost_app()
        self.ui_queue.put(self._handle_hotkey)

    def _handle_hotkey(self):
        try:
            if self.recording_window and self.recording_window.window:
                if self.recording_window.is_recording:
                    self.recording_window.stop_recording()
                else:
                    # Still starting up: a second press just closes it.
                    self.recording_window.close()
                    self.recording_window = None
            else:
                self._open_recording_window()
        except Exception:
            traceback.print_exc()

    # ---------- recording ----------

    def _open_recording_window(self):
        # Start loading Whisper now, in its own process. The load is hidden
        # behind the time spent talking, so the model is warm when you stop.
        self.whisper.prewarm()
        self.recording_window = RecordingWindow(
            on_start_recording=self._on_recording_start,
            on_stop_recording=self._on_recording_stop,
            on_cancel=self._on_recording_cancel,
        )
        self.recording_window.show()
        self._root.after(500, self._auto_start_recording)

    def _auto_start_recording(self):
        if self.recording_window and self.recording_window.window:
            self.recording_window.start_recording()

    def _on_recording_start(self):
        def amp_cb(a: float):
            win = self.recording_window
            if win is not None:
                try:
                    win.push_amplitude(a)
                except Exception:
                    pass
        self.audio_recorder.on_amplitude_update = amp_cb
        self.audio_recorder.start_recording(self.temp_audio_file)

    def _on_recording_stop(self):
        audio_path = self.audio_recorder.stop_recording(self.temp_audio_file)
        if not audio_path:
            self._close_recording_window()
            self.whisper.release()
            return
        self.is_processing = True
        threading.Thread(target=self._process_audio, args=(audio_path,), daemon=True).start()

    def _process_audio(self, audio_path: str):
        """Transcribe, put the text on the clipboard, paste it, delete the audio."""
        try:
            if self.whisper.wait_ready():
                self.ui_queue.put(lambda: self._set_processing_status("Transcribing…"))
            else:
                self.ui_queue.put(lambda: self._set_processing_status("Loading Whisper…"))
                threading.Thread(target=self._status_when_ready, daemon=True).start()

            text = self.whisper.transcribe(audio_path)
            if text and self._copy_to_clipboard(text):
                if IS_MAC and self._front_app is not None:
                    try:
                        self._front_app.activateWithOptions_(1 << 1)   # NSApplicationActivateIgnoringOtherApps
                    except Exception:
                        pass
                    time.sleep(0.15)
                time.sleep(0.1)
                if not paste_from_clipboard():
                    print("Paste was blocked by the foreground window; the text is on the clipboard.")
            self.ui_queue.put(self._close_recording_window)
        except Exception as e:
            print(f"Transcription failed: {e}")
            self.ui_queue.put(lambda: self._set_processing_status("Transcription failed"))
            time.sleep(3)
            self.ui_queue.put(self._close_recording_window)
        finally:
            self.is_processing = False
            self._delete_temp_audio()
            # Start the countdown to handing back the model's RAM and VRAM.
            self.whisper.release()

    def _copy_to_clipboard(self, text: str) -> bool:
        """Put the transcript on the clipboard and check that it stuck.

        Another app holding the clipboard open is enough to make a write fail,
        so this reads the value back and retries rather than trusting it.
        """
        for _ in range(3):
            try:
                pyperclip.copy(text)
                if pyperclip.paste() == text:
                    return True
            except Exception as e:
                print(f"Clipboard write failed: {e}")
            time.sleep(0.15)
        print("Could not put the transcript on the clipboard.")
        return False

    def _status_when_ready(self):
        if self.whisper.wait_ready(timeout=300) and self.is_processing:
            self.ui_queue.put(lambda: self._set_processing_status("Transcribing…"))

    def _set_processing_status(self, message: str):
        if self.recording_window:
            self.recording_window.set_processing_status(message)

    def _close_recording_window(self):
        if self.recording_window:
            self.recording_window.close()
            self.recording_window = None

    def _on_recording_cancel(self):
        if self.audio_recorder.is_recording:
            self.audio_recorder.stop_recording()
        self._close_recording_window()
        if not self.is_processing:
            # Mid-transcription the worker is still reading the file;
            # _process_audio deletes it when it finishes.
            self._delete_temp_audio()
            self.whisper.release()

    def _delete_temp_audio(self):
        try:
            if os.path.exists(self.temp_audio_file):
                os.remove(self.temp_audio_file)
        except OSError:
            pass

    def cleanup(self):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self.whisper.shutdown()
        self.audio_recorder.cleanup()
        self._delete_temp_audio()
        if self._root:
            self._root.quit()


_instance_lock = None


def _claim_single_instance() -> bool:
    """One instance at a time: a second copy would fight over the hotkey."""
    global _instance_lock
    if sys.platform == "win32":
        _instance_lock = ctypes.windll.kernel32.CreateMutexW(None, True, "PromptWhisper_SingleInstance")
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    import fcntl
    _instance_lock = open(os.path.join(tempfile.gettempdir(), "prompt_whisper.lock"), "w")
    try:
        fcntl.flock(_instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    if not _claim_single_instance():
        print("Prompt Whisper is already running.")
        sys.exit(0)
    PromptWhisperApp().start()


if __name__ == "__main__":
    main()
