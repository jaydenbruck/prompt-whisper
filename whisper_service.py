"""
Prompt Whisper - client side of the out-of-process Whisper worker.

This is what the tray app talks to instead of holding a WhisperModel. It keeps
the heavy process alive only for as long as it is actually useful:

    hotkey pressed   -> prewarm()    spawn the worker, load the model while
                                     you are still talking (so the ~5 s load
                                     costs you nothing)
    recording stops  -> transcribe() send the job, get the text back
    done             -> release()    arm an idle timer; when it fires the
                                     worker exits and gives back ~900 MB of
                                     RAM and ~2.2 GB of VRAM

Consecutive recordings inside the idle window reuse the warm worker, so a
dictation burst pays the model load exactly once.

If the worker cannot be spawned for any reason, everything transparently
falls back to loading Whisper in this process — the old behaviour. Speech to
text never breaks because of an optimisation.
"""
import json
import os
import subprocess
import sys
import threading
import time
import wave
from typing import Callable, Optional

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# How long to wait for the worker to report that its model is loaded and
# healthy. Cold loads measured 4-15s; this only has to be generous enough to
# cover a machine that is paging hard.
READY_TIMEOUT_S = 240.0

# Floor for a single transcription, plus how many times realtime we allow.
# GPU runs ~20x faster than realtime, CPU int8 perhaps 2x, so 6x realtime is
# a wide margin that still catches a wedged worker in well under a minute for
# a typical dictation.
TRANSCRIBE_MIN_S = 120.0
TRANSCRIBE_REALTIME_FACTOR = 6.0

# No single transcription may take longer than this, whatever the arithmetic
# above says. A wedged worker must never hold a recording hostage for an hour.
TRANSCRIBE_MAX_S = 1800.0

# How often to tell the worker we still want it, so its own idle guard cannot
# fire in the middle of a long dictation.
HEARTBEAT_S = 60.0


def _kill_tree(proc: subprocess.Popen, log: Callable[[str], None]):
    """Kill the worker AND its children.

    A venv's python.exe is often a small launcher stub that re-execs the real
    interpreter, so proc.kill() would take out the 4 MB stub and leave the
    ~900 MB worker (and its 2.2 GB of VRAM) running. Observed exactly that
    with a worker wedged inside CUDA.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=_CREATE_NO_WINDOW,
                           timeout=20)
        else:
            proc.kill()
    except Exception as e:
        log(f"Could not kill Whisper worker tree: {e}")
        try:
            proc.kill()
        except Exception:
            pass


def audio_duration_s(path: str) -> float:
    """Length of a recording in seconds; 0.0 if it cannot be determined."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        try:
            # Unreadable header - estimate from size as 16 kHz 16-bit mono.
            # Only used to size a timeout.
            return os.path.getsize(path) / 32000.0
        except Exception:
            return 0.0


class _Pending:
    """One in-flight request, waiting for the worker's reply."""

    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error: Optional[str] = None


class WhisperService:
    def __init__(self,
                 model_name: str,
                 app_dir: str,
                 idle_timeout: float = 90.0,
                 use_worker: bool = True,
                 log: Callable[[str], None] = print):
        self.model_name = model_name
        self.app_dir = app_dir
        # Seconds of inactivity after which the worker is shut down.
        # 0 disables the worker entirely (model stays in this process).
        self.idle_timeout = idle_timeout
        self.use_worker = use_worker and idle_timeout >= 0
        self.log = log

        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._ready = threading.Event()
        # Set when the current worker dies or reports itself unusable, so a
        # wait for "ready" ends the moment there is nothing left to wait for
        # rather than burning the whole READY_TIMEOUT_S.
        self._dead = threading.Event()
        self._pending: dict = {}
        self._next_id = 1
        self._idle_timer: Optional[threading.Timer] = None
        self._fallback = None          # in-process WhisperSTT, if ever needed
        self._worker_broken = False    # give up on spawning after a hard failure

    # ---------- lifecycle ----------

    def prewarm(self):
        """Start the worker and begin loading the model. Returns immediately.

        Called the moment the hotkey opens the recording window, so the model
        is ready by the time the user stops speaking.
        """
        if not self.use_worker or self._worker_broken:
            return
        with self._lock:
            self._cancel_idle_timer()
            self._ensure_worker()

    def release(self):
        """Recording finished — start counting down to shutting the worker
        (and its ~900 MB / 2.2 GB VRAM) down."""
        if not self.use_worker:
            return
        with self._lock:
            self._cancel_idle_timer()
            if self._proc is None or self.idle_timeout <= 0:
                if self.idle_timeout <= 0:
                    self.shutdown()
                return
            self._idle_timer = threading.Timer(self.idle_timeout, self._idle_expired)
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def shutdown(self):
        """Stop the worker now."""
        with self._lock:
            self._cancel_idle_timer()
            proc, self._proc = self._proc, None
            self._ready.clear()
            for p in self._pending.values():
                p.error = "worker shut down"
                p.done.set()
            self._pending.clear()
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.stdin.write('{"cmd":"quit"}\n')
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            pass
        finally:
            if proc.poll() is None:
                # It ignored "quit" — it is wedged, so take the tree down.
                _kill_tree(proc, self.log)

    def _idle_expired(self):
        self.log(f"Whisper idle for {self.idle_timeout:.0f}s — releasing model "
                 f"(~900 MB RAM / ~2.2 GB VRAM)")
        self.shutdown()

    def _cancel_idle_timer(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    # ---------- worker plumbing ----------

    def _worker_argv(self, force_cpu: bool = False) -> list:
        guard = str(max(300.0, self.idle_timeout * 4))
        tail = [self.model_name, guard] + (["--cpu"] if force_cpu else [])
        if getattr(sys, "frozen", False):
            # The packaged .exe re-enters itself in worker mode.
            return [sys.executable, "--whisper-worker"] + tail
        return [sys.executable, "-u",
                os.path.join(self.app_dir, "whisper_worker.py")] + tail

    def _ensure_worker(self, force_cpu: bool = False) -> bool:
        """Spawn the worker if it isn't running. Caller holds the lock."""
        if self._proc is not None and self._proc.poll() is None:
            return True
        self._proc = None
        self._ready.clear()
        self._dead.clear()
        try:
            proc = subprocess.Popen(
                self._worker_argv(force_cpu),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.app_dir,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            self.log(f"Could not start Whisper worker ({e}) — "
                     f"falling back to in-process model")
            self._worker_broken = True
            return False

        self._proc = proc
        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
        threading.Thread(target=self._heartbeat, args=(proc,), daemon=True).start()
        self.log(f"Whisper worker starting on "
                 f"{'CPU' if force_cpu else 'GPU'} (model loads in the background)")
        return True

    def _heartbeat(self, proc: subprocess.Popen):
        """Tell the worker we still want it, every HEARTBEAT_S.

        The worker has its own idle guard so that a worker orphaned by a
        crashed parent cannot sit on 2.2 GB of VRAM forever. But a long
        dictation sends no commands for minutes at a time, and that guard
        used to fire mid-recording — the worker exited after 6 minutes of an
        8-minute dictation, forcing a cold reload exactly when the user
        stopped talking. This keeps it alive for as long as we are alive;
        shutting it down stays our decision.
        """
        while proc.poll() is None:
            time.sleep(HEARTBEAT_S)
            with self._lock:
                if self._proc is not proc or proc.poll() is not None:
                    return
                try:
                    proc.stdin.write('{"cmd":"ping"}\n')
                    proc.stdin.flush()
                except Exception:
                    return

    def _read_stdout(self, proc: subprocess.Popen):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                event = msg.get("event")
                if event == "ready":
                    self._ready.set()
                    self.log("Whisper worker ready")
                elif event == "fatal":
                    self.log(f"Whisper worker failed: {msg.get('message')}")
                    self._worker_broken = True
                    self._dead.set()
                elif event in ("result", "error"):
                    self._resolve(msg)
        except Exception:
            pass
        # Pipe closed: the worker is gone. Never leave a caller blocked.
        self._on_worker_death(proc)

    def _read_stderr(self, proc: subprocess.Popen):
        for line in proc.stderr:
            try:
                line = line.rstrip()
                if line:
                    self.log(f"[whisper] {line}")
            except Exception:
                # A console that cannot encode the worker's output must not be
                # able to kill this thread — it is also the pipe drain, and a
                # full stderr pipe would stall the worker.
                pass

    def _on_worker_death(self, proc: subprocess.Popen):
        with self._lock:
            if self._proc is proc:
                self._proc = None
                self._ready.clear()
                self._dead.set()
            pending, self._pending = self._pending, {}
        for p in pending.values():
            p.error = "whisper worker exited"
            p.done.set()

    def _resolve(self, msg: dict):
        with self._lock:
            p = self._pending.pop(msg.get("id"), None)
        if p is None:
            return
        if msg.get("event") == "error":
            p.error = msg.get("message", "unknown error")
        else:
            p.value = msg
        p.done.set()

    def _request(self, payload: dict, retry: bool = True, timeout: float = 300.0):
        """Send a job and block until the worker answers. Returns the reply
        dict, or raises."""
        with self._lock:
            if not self._ensure_worker():
                raise RuntimeError("worker unavailable")
            proc = self._proc
            jid = self._next_id
            self._next_id += 1
            payload = dict(payload, id=jid)
            p = _Pending()
            self._pending[jid] = p
            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
            except Exception as e:
                self._pending.pop(jid, None)
                if retry:
                    self._proc = None
                    self._ready.clear()
                else:
                    raise RuntimeError(f"could not reach worker: {e}")
                proc = None

        if proc is None:
            # The worker died between "alive" and "write" — respawn and retry
            # exactly once, then give up to the fallback.
            return self._request(payload, retry=False, timeout=timeout)

        # A dead worker closes the pipe, which resolves every pending request,
        # so this normally returns as soon as the job is done. The timeout only
        # guards against a worker that is alive but wedged.
        if not p.done.wait(timeout):
            with self._lock:
                self._pending.pop(jid, None)
            raise RuntimeError(f"worker did not answer within {timeout:.0f}s")
        if p.error:
            raise RuntimeError(p.error)
        return p.value

    # ---------- jobs ----------

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio to text. Identical output to the in-process path —
        it is literally the same WhisperSTT code, just in another process.

        Every wait here is bounded. A worker can load its model successfully
        and still wedge on the first matrix multiply (CTranslate2 does not
        always raise when CUDA fails), and an unbounded wait on that turns
        into a recording that never comes back. If the worker misses a
        deadline it is killed outright and the transcription is redone in
        this process, which never involves the GPU handoff that failed.
        """
        if not self.use_worker:
            # Legacy mode only: the user asked for the model to live in this
            # process. ensure_working() still applies, so a GPU that loads but
            # cannot compute falls back to CPU instead of wedging.
            return self._fallback_stt().transcribe(audio_path)

        # Each attempt gets a brand-new worker if the last one misbehaved.
        # Everything here is killable and time-boxed, which the in-process
        # model is not: once CTranslate2 wedges inside this process there is
        # no way to interrupt it, and the app is stuck until it is killed.
        # So the tray process never loads a model outside legacy mode.
        attempts = [
            ("warm worker", False),
            ("fresh worker", False),
            ("fresh worker on CPU", True),
        ]
        last_error = None
        for label, force_cpu in attempts:
            try:
                return self._worker_transcribe(audio_path, force_cpu)
            except Exception as e:
                last_error = e
                self.log(f"Transcription via {label} failed ({e})")
                self.shutdown()          # kills the tree if it is wedged
                self._worker_broken = False   # a fresh process deserves a go

        raise RuntimeError(f"every transcription attempt failed: {last_error}")

    def _worker_transcribe(self, audio_path: str, force_cpu: bool = False) -> str:
        with self._lock:
            if not self._ensure_worker(force_cpu):
                raise RuntimeError("worker could not be started")

        # The model may still be loading — prewarm normally hides this behind
        # the time spent talking, but a two-second recording can outrun it.
        deadline = time.monotonic() + READY_TIMEOUT_S
        while not self._ready.wait(0.25):
            if self._dead.is_set():
                raise RuntimeError("worker exited before its model was ready")
            if time.monotonic() > deadline:
                raise RuntimeError(f"model not ready within {READY_TIMEOUT_S:.0f}s")

        duration = audio_duration_s(audio_path)
        timeout = min(TRANSCRIBE_MAX_S,
                      max(TRANSCRIBE_MIN_S, duration * TRANSCRIBE_REALTIME_FACTOR))
        return self._request({"cmd": "transcribe", "path": audio_path},
                             timeout=timeout)["text"]

    # ---------- fallback ----------

    def _fallback_stt(self):
        """Legacy mode: load the model in this process.

        Only reachable when the worker is disabled outright
        (WHISPER_IDLE_TIMEOUT=-1). ensure_working() rather than load_model()
        so that even here a GPU that loads but cannot compute is caught and
        swapped for the CPU, instead of wedging a process nothing can
        interrupt.
        """
        if self._fallback is None:
            from whisper_stt import WhisperSTT
            self._fallback = WhisperSTT(self.model_name)
            self._fallback.ensure_working()
        return self._fallback

    def wait_ready(self, timeout: float = 0.0) -> bool:
        """True if the worker has the model loaded (used only for status UI)."""
        return self._ready.wait(timeout) if timeout else self._ready.is_set()
