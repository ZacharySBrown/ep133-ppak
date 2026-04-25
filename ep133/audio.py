"""
WAV → raw 16-bit LE mono PCM at 46875 Hz.

The EP-133 ingests raw PCM (no WAV wrapper) at its native 46875 Hz sample
rate. Stereo sources are downmixed. Floating-point sources are clipped
then scaled to int16.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

EP133_SAMPLE_RATE = 46875


def wav_to_ep133_pcm(wav_path: Path, channels: int = 1) -> bytes:
    """Read `wav_path`, return raw 16-bit LE PCM bytes at 46875 Hz.

    `channels` is currently always 1 (mono). Kept as a param for when stereo
    is wired up — the metadata JSON already supports it.
    """
    if channels != 1:
        raise NotImplementedError("only mono is supported in v1")

    audio, sr = sf.read(str(wav_path), always_2d=False)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    if sr != EP133_SAMPLE_RATE:
        # librosa is already a project dep; use it for resampling
        import librosa

        audio = librosa.resample(audio.astype(np.float32), orig_sr=sr, target_sr=EP133_SAMPLE_RATE)

    audio = np.clip(audio, -1.0, 1.0)
    audio_i16 = (audio * 32767.0).astype("<i2")  # int16 little-endian
    return audio_i16.tobytes()
