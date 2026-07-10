"""Audio-based kill detection — listens to game audio via WASAPI loopback and
matches kill sound effects using normalized cross-correlation.

Completely immune to resolution, font style, background complexity, and video
compression. Requires a short reference .wav of each kill sound (record one
with audio_calibrate.py).
"""

from __future__ import annotations

import os
import queue
import threading
import time
import wave
from collections import deque

import numpy as np

from detector import KillEvent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_wav_mono(path: str, target_sr: int = 44100) -> np.ndarray:
    """Load a .wav as mono float32 at the target sample rate."""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())

    if sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sw == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"Unsupported sample width: {sw}")

    if ch > 1:
        samples = samples.reshape(-1, ch).mean(axis=1)

    if sr != target_sr:
        from scipy.signal import resample
        n = int(len(samples) * target_sr / sr)
        samples = resample(samples, n).astype(np.float32)

    return samples


def save_wav_mono(path: str, data: np.ndarray, sr: int = 44100) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pcm = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _bandpass(data: np.ndarray, sr: int, lo: float = 1500, hi: float = 8000,
              order: int = 4) -> np.ndarray:
    """Butterworth bandpass filter. Isolates kill-cue frequencies (mid/high)
    and strips gunfire rumble + low-frequency gameplay noise."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(order, [lo, hi], btype="band", fs=sr, output="sos")
    return sosfiltfilt(sos, data).astype(np.float32)


def _ncc(buf: np.ndarray, tmpl: np.ndarray) -> np.ndarray:
    """Sliding normalized cross-correlation. Returns scores in roughly [-1, 1]."""
    from scipy.signal import fftconvolve

    buf_z = buf - buf.mean()
    tmpl_z = tmpl - tmpl.mean()

    corr = fftconvolve(buf_z, tmpl_z[::-1], mode="valid")

    buf_energy = fftconvolve(buf_z ** 2, np.ones(len(tmpl_z)), mode="valid")
    tmpl_energy = float(np.sum(tmpl_z ** 2))

    denom = np.sqrt(np.maximum(buf_energy * tmpl_energy, 1e-12))
    return corr / denom


def _get_loopback(device_name: str = ""):
    """Return a soundcard loopback microphone for the default (or named) speaker."""
    import soundcard as sc

    if device_name:
        speakers = [s for s in sc.all_speakers()
                    if device_name.lower() in s.name.lower()]
        speaker = speakers[0] if speakers else sc.default_speaker()
    else:
        speaker = sc.default_speaker()

    try:
        return speaker, sc.get_microphone(speaker.id, include_loopback=True)
    except Exception:
        return speaker, speaker.loopback()


def list_audio_devices() -> None:
    """Print available audio output devices (for troubleshooting)."""
    import soundcard as sc
    default = sc.default_speaker()
    print("Audio output devices:")
    for s in sc.all_speakers():
        tag = " [DEFAULT]" if s.id == default.id else ""
        print(f"  {s.name}{tag}")


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------

class AudioDetector:
    """Listens to system audio via WASAPI loopback and fires KillEvents when
    a known kill sound is detected."""

    def __init__(
        self,
        references: dict[str, str],
        sample_rate: int = 44100,
        threshold: float = 0.40,
        cooldown: float = 2.0,
        buffer_seconds: float = 3.0,
        check_interval: float = 0.15,
        device_name: str = "",
        debug: bool = False,
    ):
        self.sr = sample_rate
        self.threshold = threshold
        self.cooldown = cooldown
        self.buffer_seconds = buffer_seconds
        self.check_interval = check_interval
        self.device_name = device_name
        self.debug = debug

        self.templates: dict[str, np.ndarray] = {}
        for tag, path in references.items():
            if path and os.path.isfile(path):
                raw = load_wav_mono(path, self.sr)
                self.templates[tag] = _bandpass(raw, self.sr)
                if self.debug:
                    dur = len(raw) / self.sr
                    print(f"  [audio] loaded {tag!r} reference: {path} ({dur:.2f}s)")
        if not self.templates:
            raise FileNotFoundError(
                "No reference sound files found. Run audio_calibrate.py first "
                "to record the kill sound from your game."
            )

        max_chunks = int(self.buffer_seconds / 0.05) + 4
        self._chunks: deque[np.ndarray] = deque(maxlen=max_chunks)
        self._events: queue.Queue[KillEvent] = queue.Queue()
        self._stop = threading.Event()
        self._last_fire: float = 0.0  # global cooldown (all types)
        self._capture_thread: threading.Thread | None = None
        self._match_thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="audio-capture")
        self._match_thread = threading.Thread(
            target=self._match_loop, daemon=True, name="audio-match")
        self._capture_thread.start()
        self._match_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> list[KillEvent]:
        """Return all kill events detected since the last poll."""
        out: list[KillEvent] = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    # --- threads --------------------------------------------------------------

    def _capture_loop(self) -> None:
        import comtypes
        comtypes.CoInitialize()
        try:
            self._capture_loop_inner()
        finally:
            comtypes.CoUninitialize()

    def _capture_loop_inner(self) -> None:
        speaker, loopback = _get_loopback(self.device_name)
        print(f"Audio capture: listening to {speaker.name!r} (loopback)")
        chunk_frames = int(self.sr * 0.05)  # 50 ms

        with loopback.recorder(samplerate=self.sr, channels=1,
                               blocksize=chunk_frames) as rec:
            while not self._stop.is_set():
                try:
                    data = rec.record(numframes=chunk_frames)
                except Exception:
                    if self._stop.is_set():
                        break
                    time.sleep(0.1)
                    continue
                mono = data[:, 0] if data.ndim > 1 else data.ravel()
                self._chunks.append(mono.astype(np.float32))

    def _match_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.check_interval)
            now = time.monotonic()

            if len(self._chunks) < 4:
                continue
            raw_buf = np.concatenate(list(self._chunks))

            # skip if mostly silent (game not running / muted)
            rms = float(np.sqrt(np.mean(raw_buf[-self.sr:] ** 2))) if len(raw_buf) >= self.sr else 0
            if rms < 1e-5:
                continue

            # global cooldown: skip all matching if we fired recently
            if now - self._last_fire < self.cooldown:
                continue

            buf = _bandpass(raw_buf, self.sr)

            best_tag: str | None = None
            best_score: float = 0.0

            for tag, tmpl in self.templates.items():
                if len(buf) <= len(tmpl):
                    continue
                scores = _ncc(buf, tmpl)
                peak = float(np.max(scores))
                if self.debug:
                    print(f"  [audio] {tag}: peak={peak:.3f}  (threshold={self.threshold})")
                if peak > best_score:
                    best_score = peak
                    best_tag = tag

            if best_tag is not None and best_score >= self.threshold:
                self._last_fire = now
                self._chunks.clear()  # flush buffer so the same sound can't re-trigger
                ev = KillEvent(
                    timestamp=now,
                    raw_line=f"[audio:{best_tag} score={best_score:.2f}]",
                    killer="",
                    victim="",
                    is_self_kill=True,
                )
                self._events.put(ev)
