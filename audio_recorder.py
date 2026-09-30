"""
Audio Recorder Module - Handles audio recording from microphone

Two deliberate choices keep this cheap:

  * Audio is streamed straight into the WAV file as it arrives instead of
    being accumulated in a list of chunks. The old version held the whole
    recording in RAM and then doubled it for the b"".join() at save time —
    ~58 MB peak for a five-minute dictation. Now it is a fixed ~64 KB.
  * The level meter uses the stdlib `array` module on every 16th sample
    rather than numpy. Importing numpy costs ~415 MB of commit charge, and
    this was the only thing in the tray process that needed it.
"""
import array
import os
import pyaudio
import wave
import threading
from typing import Callable, Optional

# The level meter drives a 19-bar waveform, not an analysis pipeline — one
# sample in every 16 is far more resolution than the animation can show.
_AMP_STRIDE = 16


class AudioRecorder:
    def __init__(self, sample_rate=16000, channels=1, chunk_size=1024):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.format = pyaudio.paInt16
        self.audio = pyaudio.PyAudio()
        self.is_recording = False
        self.stream = None
        self.recording_thread = None
        self.on_amplitude_update: Optional[Callable[[float], None]] = None
        self._wav: Optional[wave.Wave_write] = None
        self._wav_path = ""
        self._frame_count = 0

    def start_recording(self, output_path: str = "temp_recording.wav"):
        """Start recording audio from microphone straight into output_path."""
        if self.is_recording:
            return

        self.is_recording = True
        self._frame_count = 0
        self._wav_path = output_path

        # Open audio stream. If the device rejects the configured rate,
        # fall back to 16kHz so recording never breaks.
        try:
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
        except Exception as e:
            print(f"⚠️  Could not open mic at {self.sample_rate}Hz ({e}), falling back to 16kHz")
            self.sample_rate = 16000
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
        
        # The WAV header is written now; frames are appended as they arrive.
        self._wav = wave.open(output_path, 'wb')
        self._wav.setnchannels(self.channels)
        self._wav.setsampwidth(self.audio.get_sample_size(self.format))
        self._wav.setframerate(self.sample_rate)

        # Start recording in a separate thread
        self.recording_thread = threading.Thread(target=self._record)
        self.recording_thread.start()

    def _record(self):
        """Internal recording loop"""
        samples = array.array('h')
        while self.is_recording:
            try:
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                self._wav.writeframes(data)
                self._frame_count += 1

                # Level for the waveform overlay
                if self.on_amplitude_update:
                    del samples[:]
                    samples.frombytes(data)
                    window = samples[::_AMP_STRIDE]
                    if window:
                        amplitude = (sum(map(abs, window)) / len(window)) / 32768.0
                        self.on_amplitude_update(amplitude)

            except Exception as e:
                print(f"Recording error: {e}")
                break

    def stop_recording(self, output_path: Optional[str] = None) -> str:
        """Stop recording and finalize the WAV file."""
        if not self.is_recording:
            return ""

        self.is_recording = False

        # Wait for recording thread to finish
        if self.recording_thread:
            self.recording_thread.join(timeout=1.0)

        # Close stream
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        # Finalize the WAV (this patches the RIFF sizes in the header)
        wav, self._wav = self._wav, None
        if wav is not None:
            try:
                wav.close()
            except Exception as e:
                print(f"Could not finalize WAV: {e}")
                return ""

        path = self._wav_path
        if not self._frame_count or not path or not os.path.exists(path):
            return ""

        # Callers may name a different destination than the one recording
        # streamed into; honour it.
        if output_path and os.path.abspath(output_path) != os.path.abspath(path):
            try:
                os.replace(path, output_path)
                path = output_path
            except Exception as e:
                print(f"Could not move recording to {output_path}: {e}")

        return path

    def get_recording_duration(self) -> float:
        """Get the duration of the current recording in seconds"""
        if not self._frame_count:
            return 0.0
        return self._frame_count * self.chunk_size / self.sample_rate

    def cleanup(self):
        """Clean up resources"""
        self.is_recording = False
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=1.0)
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        self.audio.terminate()
