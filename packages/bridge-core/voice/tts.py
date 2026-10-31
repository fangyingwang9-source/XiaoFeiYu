"""Abstract TTS provider; Edge TTS implemented, CosyVoice2 slot reserved."""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSResult:
    path: str
    duration_ms: int
    provider: str
    voice: str


class TTSProvider:
    name = "base"

    def synthesize(self, text: str, out_path: str) -> TTSResult:
        raise NotImplementedError

    def voices(self) -> list[str]:
        return []


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge TTS: free, no key, returns mp3."""

    name = "edge"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%", pitch: str = "+0Hz"):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    def voices(self) -> list[str]:
        return ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural", "zh-CN-YunyangNeural", "zh-CN-liaoning-XiaobeiNeural"]

    def synthesize(self, text: str, out_path: str) -> TTSResult:
        import edge_tts

        async def _run() -> int:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            last_end = 0
            with open(out_path, "wb") as fh:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        end = int(chunk["offset"]) + int(chunk["duration"])
                        last_end = max(last_end, end)
            # WordBoundary offsets are in 100ns ticks.
            return int(last_end / 10000)

        dur = asyncio.run(_run())
        if dur <= 0:
            from .audio import wav_duration_ms

            try:
                dur = wav_duration_ms(out_path)
            except Exception:
                dur = 0
        return TTSResult(path=out_path, duration_ms=dur, provider=self.name, voice=self.voice)


class CosyVoice2Provider(TTSProvider):
    """Reserved: local CosyVoice2 clone. Wire when the model is deployed."""

    name = "cosyvoice2"

    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint

    def synthesize(self, text: str, out_path: str) -> TTSResult:
        if not self.endpoint:
            raise NotImplementedError("CosyVoice2 is not deployed yet: set voice.cosyvoice2.endpoint in config.json")
        raise NotImplementedError("CosyVoice2 HTTP adapter is not implemented in this milestone")


_PROVIDERS = {"edge": EdgeTTSProvider, "cosyvoice2": CosyVoice2Provider}


def get_provider(name: str = "edge", **kwargs) -> TTSProvider:
    key = (name or "edge").lower()
    if key not in _PROVIDERS:
        raise ValueError(f"unknown TTS provider: {name}")
    return _PROVIDERS[key](**kwargs)


def default_out_path(out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"tts-{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.mp3")
