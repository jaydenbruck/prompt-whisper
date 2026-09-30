"""
Prompt Whisper - out-of-process Whisper worker.

Why this exists
---------------
faster-whisper is by far the heaviest thing this app touches. Measured with
large-v3-turbo on an RTX 3060:

    importing faster_whisper .................  +180 MB working set
    loading the model (cuda/float16) .........  +154 MB WS, +2.1 GB VRAM
    first transcription (cuDNN kernels) ......  +510 MB WS
    ------------------------------------------------------------------
    resident afterwards ......................  ~900 MB WS / ~4 GB commit
                                                ~2.2 GB VRAM

None of that can be handed back inside the process: CUDA contexts and cuDNN
kernels stay mapped for the lifetime of the interpreter, so a tray app that
loads Whisper once keeps all of it until you quit — even though it spends
99% of its life doing nothing.

So the model lives in *this* process instead. The tray app spawns it when you
press the hotkey (while you are still talking, so the load is free) and shuts
it down once you stop dictating. Between recordings Prompt Whisper is a ~35 MB
tray icon holding zero VRAM.

Transcription itself is the same WhisperSTT code with the same decoding
parameters as the in-process path.

Protocol — one JSON object per line, both directions:

    parent -> worker   {"cmd":"transcribe","id":1,"path":"...wav"}
                       {"cmd":"ping"}
                       {"cmd":"quit"}
    worker -> parent   {"event":"ready","device":"cuda"}
                       {"event":"result","id":1,"text":"..."}
                       {"event":"error","id":1,"message":"..."}

stdout carries the protocol and nothing else — sys.stdout is repointed at
stderr on entry so that library chatter can never corrupt a JSON line. The
parent forwards our stderr into its own log.
"""
import json
import os
import sys
import threading
import time
from queue import Queue

# Safety net only. The parent owns the worker's lifetime and sends "quit";
# this exists so a worker orphaned by a parent crash cannot sit on 2 GB of
# VRAM forever.
DEFAULT_IDLE_GUARD_S = 900.0


def _send(fh, obj):
    # ensure_ascii keeps the pipe pure ASCII, so no encoding mismatch between
    # parent and child can ever mangle umlauts in a German transcript.
    fh.write(json.dumps(obj, ensure_ascii=True) + "\n")
    fh.flush()


def worker_main(model_name: str, idle_guard_s: float = DEFAULT_IDLE_GUARD_S,
                force_cpu: bool = False) -> int:
    # Claim the real stdout for the protocol before anything else can print.
    proto = sys.stdout
    if proto is None:
        # No pipe to answer on — e.g. a windowed build launched without one.
        # Bail out rather than load 2 GB of model nobody can talk to; the
        # parent notices the exit and falls back to an in-process model.
        return 1
    sys.stdout = sys.stderr

    jobs: "Queue" = Queue()
    send_lock = threading.Lock()
    state = {"last": time.monotonic(), "busy": False}

    def emit(obj):
        with send_lock:
            try:
                _send(proto, obj)
            except Exception:
                os._exit(0)  # parent's pipe is gone; nothing left to do

    # ---- 1. Every import happens first, on the main thread, with nothing
    #         else in this process touching sys.stdin.
    #
    # Two ordering rules were learned the hard way here, both of which hang
    # the worker forever if broken, and neither of which reproduces outside a
    # spawned child process:
    #
    #  * Do not import faster_whisper from a background thread. Its CUDA/ONNX
    #    extension modules load DLLs, and doing that off the main thread this
    #    early in a spawned process deadlocks on the Windows loader lock.
    #  * Do not have a thread blocked in sys.stdin.readline() while importing.
    #    That thread holds the TextIOWrapper's lock, and something in
    #    faster-whisper's import chain inspects sys.stdin — so the import
    #    blocks on a lock held by a thread waiting for input that will never
    #    come. Measured: 2 s to import with no reader thread, never finishes
    #    with one.
    #
    # So: import everything, load the model, and only then start reading
    # commands. Jobs the parent sends meanwhile simply wait in the pipe
    # buffer; they are a few dozen bytes each.
    from whisper_stt import WhisperSTT

    stt = WhisperSTT(model_name)
    try:
        # Loads the model AND proves it can run one tiny transcription,
        # falling back from GPU to CPU if it cannot. This both absorbs the
        # cold-start cost (VAD session + cuDNN kernels) while the user is
        # still speaking, and catches a GPU that loaded but cannot compute —
        # which used to wedge CTranslate2 on the first real transcription.
        device = stt.ensure_working(force_cpu=force_cpu)
    except Exception as e:
        emit({"event": "fatal", "message": f"model unusable: {e}"})
        os._exit(1)

    state["last"] = time.monotonic()
    emit({"event": "ready", "device": device})

    # ---- 2. Imports are done; reading stdin on a thread is safe now.
    def read_stdin():
        """Feed commands to the main thread. A sentinel means 'stop'."""
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("cmd") == "quit":
                    break
                # Any message counts as "the parent still wants me", which is
                # what keeps the idle guard below from firing. Pings exist
                # solely for that and are never queued as work: a long
                # dictation sends no commands at all, and a worker that
                # suicided mid-recording used to force a cold reload at the
                # worst possible moment.
                state["last"] = time.monotonic()
                if msg.get("cmd") != "ping":
                    jobs.put(msg)
        except Exception:
            pass
        jobs.put(None)  # "quit", or stdin EOF because the parent died

    def watchdog():
        while idle_guard_s > 0:
            time.sleep(5.0)
            if (not state["busy"] and jobs.empty()
                    and time.monotonic() - state["last"] > idle_guard_s):
                print("Worker idle guard expired — exiting", file=sys.stderr)
                os._exit(0)

    threading.Thread(target=read_stdin, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

    # ---- 3. Serve jobs.
    while True:
        job = jobs.get()
        if job is None:
            break
        state["busy"] = True
        jid = job.get("id")
        try:
            cmd = job.get("cmd")
            if cmd == "transcribe":
                text = stt.transcribe(job["path"])
                emit({"event": "result", "id": jid, "text": text})
            else:
                emit({"event": "error", "id": jid,
                      "message": f"unknown command {cmd!r}"})
        except Exception as e:
            emit({"event": "error", "id": jid, "message": str(e)})
        finally:
            state["busy"] = False
            state["last"] = time.monotonic()

    # Exit hard: CTranslate2/CUDA teardown is slow and occasionally hangs, and
    # the OS reclaims the GPU context and every byte of this process anyway.
    os._exit(0)


def parse_args(argv: list) -> tuple:
    """(model, idle_guard_seconds, force_cpu) from a worker argv tail."""
    force_cpu = "--cpu" in argv
    rest = [a for a in argv if a != "--cpu"]
    model = rest[0] if rest else "large-v3-turbo"
    try:
        guard = float(rest[1]) if len(rest) > 1 else DEFAULT_IDLE_GUARD_S
    except ValueError:
        guard = DEFAULT_IDLE_GUARD_S
    return model, guard, force_cpu


if __name__ == "__main__":
    sys.exit(worker_main(*parse_args(sys.argv[1:])))
