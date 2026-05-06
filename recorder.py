"""
Audio capture from microphone using sounddevice.
Records into an in-memory numpy array at 16kHz (Whisper's expected sample rate).
"""

import threading
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000  # Hz — Whisper expects 16kHz mono
CHANNELS = 1
DTYPE = "float32"


class AudioRecorder:
    def __init__(self, device=None, sample_rate=SAMPLE_RATE):
        """
        device: sounddevice device index or name (None = system default input).
                Run `python3 -c "import sounddevice as sd; print(sd.query_devices())"` to list.
        """
        self.device = device
        self.sample_rate = sample_rate
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.is_recording = False
        self.current_level: float = 0.0  # RMS level 0.0–1.0, updated each chunk

    def start(self):
        if self.is_recording:
            return
        self._chunks = []
        self.is_recording = True
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured audio as a flat float32 array."""
        if not self.is_recording:
            return np.array([], dtype=DTYPE)
        self.is_recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass  # device may have disconnected mid-recording
            self._stream = None
        with self._lock:
            if not self._chunks:
                return np.array([], dtype=DTYPE)
            audio = np.concatenate(self._chunks, axis=0).flatten()
        return audio

    def _callback(self, indata: np.ndarray, frames: int, time, status):
        if status:
            import logging
            logging.getLogger("talktalk").warning("Audio stream status: %s", status)
        with self._lock:
            self._chunks.append(indata.copy())
        # RMS level for HUD visualization — no lock needed (single float write)
        rms = float(np.sqrt(np.mean(indata ** 2)))
        self.current_level = min(1.0, rms * 20)
