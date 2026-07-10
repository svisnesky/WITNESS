"""Record reference kill sounds from your game audio (WASAPI loopback).

Run this while playing Marathon. When you hear a kill sound, press ENTER.
The tool saves the audio from just before you pressed the key — that's your
reference clip for audio-based detection.

Usage:
    python audio_calibrate.py              # record the default "kill" sound
    python audio_calibrate.py precision    # record a precision-kill sound
    python audio_calibrate.py finisher     # record a finisher sound
    python audio_calibrate.py --list       # list audio devices
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(BASE, "sounds")
SAMPLE_RATE = 44100
LOOKBACK = 1.5   # seconds before keypress to keep
LOOKAHEAD = 0.3  # seconds after keypress to keep
CHUNK_MS = 50


def _auto_trim(audio: np.ndarray, sr: int, pad: float = 0.08) -> np.ndarray:
    """Trim silence from the edges and center on the loudest burst."""
    window = int(sr * 0.15)
    if len(audio) < window * 2:
        return audio

    # sliding RMS energy
    energy = np.convolve(audio ** 2, np.ones(window) / window, mode="same")
    peak_idx = int(np.argmax(energy))
    peak_e = energy[peak_idx]
    if peak_e < 1e-10:
        return audio  # all silence

    # find the region where energy stays above 10% of peak
    thresh = peak_e * 0.10
    above = np.where(energy >= thresh)[0]
    start = max(0, int(above[0]) - int(sr * pad))
    end = min(len(audio), int(above[-1]) + int(sr * pad))
    return audio[start:end]


def record_sound(tag: str = "kill") -> str | None:
    """Interactive: capture system audio, wait for ENTER, save a trimmed clip."""
    try:
        import soundcard as sc
    except ImportError:
        print("ERROR: soundcard is not installed.")
        print("  python -m pip install soundcard")
        return None

    from audio_detector import save_wav_mono

    speaker = sc.default_speaker()
    print(f"\nListening to: {speaker.name}")
    try:
        loopback = sc.get_microphone(speaker.id, include_loopback=True)
    except Exception:
        loopback = speaker.loopback()

    chunk_frames = int(SAMPLE_RATE * CHUNK_MS / 1000)
    max_chunks = int((LOOKBACK + 2) / (CHUNK_MS / 1000))
    chunks: deque[np.ndarray] = deque(maxlen=max_chunks)
    recording = True
    error_msg = None

    def capture():
        nonlocal error_msg
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            with loopback.recorder(samplerate=SAMPLE_RATE, channels=1,
                                   blocksize=chunk_frames) as rec:
                while recording:
                    data = rec.record(numframes=chunk_frames)
                    mono = data[:, 0] if data.ndim > 1 else data.ravel()
                    chunks.append(mono.astype(np.float32))
        except Exception as e:
            error_msg = str(e)
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    t = threading.Thread(target=capture, daemon=True)
    t.start()

    print(f"\nRecording for: {tag.upper()}")
    print("Play the game and get a kill. Press ENTER right after you hear the sound.")
    print("(Type Q + ENTER to cancel)\n")

    try:
        ans = input(">>> ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        recording = False
        return None

    # grab a little more audio after the keypress
    time.sleep(LOOKAHEAD)
    recording = False
    t.join(timeout=2)

    if error_msg:
        print(f"Audio capture error: {error_msg}")
        return None
    if ans == "q":
        print("Cancelled.")
        return None

    if not chunks:
        print("No audio captured. Is your game audio playing through the default speakers?")
        return None

    audio = np.concatenate(list(chunks))
    # keep the last LOOKBACK+LOOKAHEAD seconds
    keep = int(SAMPLE_RATE * (LOOKBACK + LOOKAHEAD))
    audio = audio[-keep:] if len(audio) > keep else audio

    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 1e-4:
        print("WARNING: audio is nearly silent. Make sure game audio is playing.")

    trimmed = _auto_trim(audio, SAMPLE_RATE)
    dur = len(trimmed) / SAMPLE_RATE

    out_path = os.path.join(SOUNDS_DIR, f"{tag}.wav")
    save_wav_mono(out_path, trimmed, SAMPLE_RATE)
    print(f"\nSaved {dur:.2f}s clip to: {out_path}")
    print(f"  (RMS level: {rms:.4f})")

    # hint
    print(f"\nOpen {out_path} in Windows to listen and verify it captured the right sound.")
    print("If it sounds wrong, run this again to re-record.")
    return out_path


def main():
    if "--list" in sys.argv:
        from audio_detector import list_audio_devices
        list_audio_devices()
        return

    tag = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "kill"

    print("=" * 50)
    print("  MARATHON KILL SOUND RECORDER")
    print("=" * 50)
    print()
    print("This records the kill sound from your game so the")
    print("recorder can detect kills by AUDIO instead of reading text.")
    print()
    print("Your game audio must be playing through your default speakers/headphones.")

    path = record_sound(tag)
    if not path:
        return

    print()
    more = input("Record another sound type? (precision / finisher / done): ").strip().lower()
    while more and more != "done" and more not in ("q", "quit", "n", "no"):
        record_sound(more)
        more = input("Record another? (precision / finisher / done): ").strip().lower()

    print("\nDone! Your reference sounds are in the sounds/ folder.")
    print("Set detection_mode: \"audio\" in config.yaml to use them.")


if __name__ == "__main__":
    main()
