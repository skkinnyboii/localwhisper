"""Microphone recording via sounddevice (PortAudio, bundled in the wheel)."""

import threading
from collections import deque

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # what Whisper expects


class Recorder:
    """Records mono 16 kHz float32 audio between start() and stop().

    Exposes `levels`, a rolling deque of per-block RMS values, so a UI can
    draw a live waveform while recording.
    """

    def __init__(self, max_seconds: int = 300):
        self._max_frames = int(max_seconds * SAMPLE_RATE)
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.truncated = False
        self.levels: deque[float] = deque(maxlen=64)

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            self._frames = 0
            self.truncated = False
            self.levels.clear()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=1024,  # ~64 ms blocks: smooth waveform updates
                callback=self._callback,
            )
            self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        if self._frames >= self._max_frames:
            self.truncated = True
            return
        self._chunks.append(indata.copy())
        self._frames += frames
        self.levels.append(float(np.sqrt(np.mean(indata**2))))

    def stop(self) -> np.ndarray | None:
        with self._lock:
            if self._stream is None:
                return None
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks, axis=0).flatten()
        self._chunks = []
        return audio

    @property
    def recording(self) -> bool:
        return self._stream is not None


def preflight_microphone() -> tuple[bool, str]:
    """Open and close a stream so macOS shows the mic permission prompt early.

    Note: macOS lets the stream open even before permission is granted (you
    just record silence), so True here means the device works, not that the
    TCC grant exists.
    """
    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        stream.start()
        stream.stop()
        stream.close()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)
