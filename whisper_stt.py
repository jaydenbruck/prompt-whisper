"""
Local speech-to-text with faster-whisper.
"""
import math
import os
import sys
from typing import Optional

from faster_whisper import WhisperModel

from config import MODELS_DIR, WHISPER_LANGUAGE
from text_corrections import build_initial_prompt, clean_transcript


class WhisperSTT:
    def __init__(self, model_path: str = "large-v3-turbo"):
        """
        Args:
            model_path: Model size name or path to a local model folder:
                - "large-v3-turbo" (recommended, ~1.6 GB)
                - "large-v3"       (best quality, ~3 GB)
                - "medium"         (~1.5 GB)
                - "small"          (~500 MB)
                - "base"           (~150 MB)
                - "tiny"           (~75 MB)
        """
        self.model_path = model_path
        self.model: Optional[WhisperModel] = None
        self.is_loaded = False
        self.device = ""   # "cuda" or "cpu", set once the model is loaded

    def load_model(self, force_cpu: bool = False):
        """Load the model, downloading it into MODELS_DIR on first use."""
        if self.is_loaded:
            return

        print(f"Loading Whisper model: {self.model_path}...")
        os.makedirs(MODELS_DIR, exist_ok=True)

        # faster-whisper has no GPU backend on macOS; Apple Silicon runs it on the CPU.
        if not force_cpu and sys.platform != "darwin":
            try:
                self.model = WhisperModel(
                    self.model_path,
                    device="cuda",
                    compute_type="float16",
                    download_root=MODELS_DIR,
                )
                self.device = "cuda"
                self.is_loaded = True
                print("Using GPU (CUDA) for inference")
                return
            except Exception as e:
                print(f"GPU unavailable ({e}); falling back to CPU")

        # CPU with int8 quantization
        self.model = WhisperModel(
            self.model_path,
            device="cpu",
            compute_type="int8",
            download_root=MODELS_DIR,
        )
        self.device = "cpu"
        self.is_loaded = True
        print("Using CPU with int8 quantization for inference")

    def ensure_working(self, force_cpu: bool = False) -> str:
        """Load the model AND prove it can actually run, returning the device.

        Loading succeeding is not the same as the model working. CTranslate2
        loads cuBLAS lazily, on the first matrix multiply, so on a machine that
        is low on memory the weights load fine and then the first real
        transcription fails - or worse, hangs. The warm-up doubles as a health
        check: if the GPU path cannot complete one tiny transcription, the
        model is reloaded on the CPU before any real audio is sent to it.
        """
        self.load_model(force_cpu=force_cpu)
        try:
            self.warm_up()
            return self.device
        except Exception as e:
            if self.device == "cpu":
                raise
            print(f"GPU path failed its health check ({e}) - reloading on CPU")

        self.model = None
        self.is_loaded = False
        self.load_model(force_cpu=True)
        self.warm_up()
        return self.device

    def warm_up(self):
        """Run one throwaway pass so the first real transcription is fast.

        The first call also pulls in the VAD session and makes CUDA build its
        kernels. Doing it here moves that cost into the window where the user
        is still talking. The audio is a quiet tone, not silence, so the
        encoder actually runs; VAD is off for this pass. Failures are raised:
        ensure_working() relies on them.
        """
        if not self.is_loaded:
            self.load_model()
        import numpy as np
        n = 16000  # 1 second at Whisper's internal rate
        t = np.arange(n, dtype=np.float32) / 16000.0
        audio = (0.05 * np.sin(2 * math.pi * 220.0 * t)).astype(np.float32)
        segments, _ = self.model.transcribe(
            audio, language="en", task="transcribe", beam_size=5,
            vad_filter=False, without_timestamps=True,
        )
        for _ in segments:  # segments are lazy - iterate to do the work
            pass

    def transcribe(self, audio_path: str) -> str:
        """Transcribe an audio file and return the cleaned text."""
        if not self.is_loaded:
            self.load_model()

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # Anti-hallucination settings:
        #  - vad_filter drops near-silence so Whisper can't invent "you" /
        #    "Thanks for watching" out of quiet gaps.
        #  - condition_on_previous_text=False stops runaway repetition loops.
        #  - the threshold trio suppresses low-confidence / degenerate output.
        #  - initial_prompt carries WHISPER_VOCAB as a spelling hint.
        segments, info = self.model.transcribe(
            audio_path,
            language=WHISPER_LANGUAGE,
            task="transcribe",
            beam_size=5,
            best_of=5,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            condition_on_previous_text=False,
            initial_prompt=build_initial_prompt(),
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )

        transcript = " ".join(segment.text.strip() for segment in segments).strip()
        print(f"Detected language: {info.language}")
        return clean_transcript(transcript)

    def is_ready(self) -> bool:
        return self.is_loaded and self.model is not None
