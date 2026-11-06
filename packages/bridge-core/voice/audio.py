"""Audio helpers: SILK -> WAV, load any supported file to 16k mono float32.

No ffmpeg dependency: SILK goes through pilk (pure python), everything else
through soundfile. Resampling is linear interpolation (good enough for ASR).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import wave

import numpy as np

TARGET_RATE = 16000


def silk_to_pcm(path: str, sample_rate: int = 24000) -> tuple[bytes, int]:
    """Decode a WeChat .silk file to raw 16-bit PCM. Returns (pcm, rate).

    pilk 0.2.4 only accepts file paths (its decode() signature is
    decode(silk_path, pcm_path, pcm_rate)), so bytes always go through a
    throwaway temp dir.
    """
    import pilk

    tmpdir = tempfile.mkdtemp(prefix="xfy-silk-")
    pcm_path = os.path.join(tmpdir, "out.pcm")
    try:
        pilk.decode(path, pcm_path, pcm_rate=sample_rate)
        with open(pcm_path, "rb") as fh:
            return fh.read(), sample_rate
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def silk_bytes_to_pcm(raw: bytes, sample_rate: int = 24000) -> tuple[bytes, int]:
    """Same as silk_to_pcm but for the SILK bytes stored inside WeChat DBs."""
    tmpdir = tempfile.mkdtemp(prefix="xfy-silk-")
    try:
        silk_path = os.path.join(tmpdir, "in.silk")
        with open(silk_path, "wb") as fh:
            fh.write(raw)
        return silk_to_pcm(silk_path, sample_rate)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def pcm_to_wav(pcm: bytes, rate: int, out_path: str) -> str:
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return out_path


def decode_voice_file(path: str) -> str:
    """Return a 16k mono WAV path for a voice message of any supported kind."""
    if path.lower().endswith(".silk"):
        pcm, rate = silk_to_pcm(path)
        tmp = os.path.join(tempfile.mkdtemp(prefix="xfy-voice-"), "decoded.wav")
        return pcm_to_wav(pcm, rate, tmp)
    return path


def load_wav_16k(path: str) -> np.ndarray:
    import soundfile as sf

    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != TARGET_RATE:
        n = int(round(len(mono) * TARGET_RATE / rate))
        x_old = np.linspace(0.0, 1.0, len(mono), endpoint=False)
        x_new = np.linspace(0.0, 1.0, n, endpoint=False)
        mono = np.interp(x_new, x_old, mono).astype("float32")
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0:
        mono = (mono / peak * 0.9).astype("float32")
    return mono


def wav_duration_ms(path: str) -> int:
    import soundfile as sf

    info = sf.info(path)
    return int(round(info.frames / info.samplerate * 1000))
