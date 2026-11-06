"""Abstract TTS provider: Edge TTS (cloud) and local CosyVoice2 clone."""
from __future__ import annotations

import asyncio
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSResult:
    path: str
    duration_ms: int
    provider: str
    voice: str
    note: str = ""


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
    """Local CosyVoice2 zero-shot clone, driven through the HTTP sidecar.

    The sidecar (packages/bridge-core/vendor/cosyvoice2/server.py) owns the GPU
    and the reference clips; this class only speaks HTTP so the bridge process
    stays free of torch.
    """

    name = "cosyvoice2"

    def __init__(self, voice: str = "noelle", rate: Optional[str] = None, speed: float = 1.0,
                 ref_wav: Optional[str] = None, ref_text: Optional[str] = None,
                 endpoint: Optional[str] = None, timeout: float = 90.0, seed: int = 1234,
                 mode: str = "zero", **kw):
        self.voice = voice or "noelle"
        self.seed = int(seed)
        self.mode = mode
        self.endpoint = (endpoint or "http://127.0.0.1:8790").rstrip("/")
        self.speed = float(speed or 1.0)
        self.ref_wav = ref_wav
        self.ref_text = ref_text
        self.timeout = float(timeout)
        # Localhost must never go through a system proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def voices(self) -> list[str]:
        return ["noelle"]

    def _request(self, path: str, payload: Optional[dict] = None) -> dict:
        import json

        url = self.endpoint + path
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
        with self._opener.open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def status(self) -> dict:
        return self._request("/status")

    def synthesize(self, text: str, out_path: str) -> TTSResult:
        import shutil

        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        out_path = os.path.abspath(out_path)
        wav_path = out_path if out_path.lower().endswith(".wav") else os.path.splitext(out_path)[0] + ".wav"
        os.makedirs(os.path.dirname(wav_path), exist_ok=True)
        payload = {"text": text, "speed": self.speed, "seed": self.seed, "mode": self.mode}
        ref = self.ref_wav or self.voice
        if ref:
            payload["ref_wav"] = ref
        if self.ref_text:
            payload["ref_text"] = self.ref_text
        res = self._request("/tts", payload)
        if not res.get("ok"):
            raise RuntimeError("cosyvoice2 tts failed: %s" % (res.get("error") or res))
        src = res.get("path") or ""
        if not src or not os.path.exists(src):
            raise RuntimeError("cosyvoice2 returned no audio file")
        if os.path.abspath(src) != wav_path:
            shutil.copyfile(src, wav_path)
        dur = int(res.get("duration_ms") or 0)
        if dur <= 0:
            from .audio import wav_duration_ms

            try:
                dur = wav_duration_ms(wav_path)
            except Exception:
                dur = 0
        return TTSResult(path=wav_path, duration_ms=dur, provider=self.name, voice=self.voice)


class FallbackTTSProvider(TTSProvider):
    """Run the local clone, but never lose a message.

    Any failure of the primary (sidecar down, HTTP error, timeout) or a
    speech-rate outside [min_cps, max_cps] characters/second (the "fast
    forward / slow motion" symptom) reruns the text through Edge TTS.
    """

    name = "cosyvoice2+edge"

    def __init__(self, voice: str = "noelle", rate: str = "+0%",
                 primary: str = "cosyvoice2", fallback: str = "edge",
                 fallback_voice: str = "zh-CN-XiaoxiaoNeural",
                 min_cps: float = 2.2, max_cps: float = 8.5,
                 retries: int = 0, **kw):
        self.voice = voice or "noelle"
        self.min_cps = float(min_cps)
        self.max_cps = float(max_cps)
        self.retries = max(0, int(retries))
        self._primary_kw = {k: v for k, v in kw.items() if k not in ("fallback_voice",)}
        self.primary_name = primary
        self.primary = get_provider(primary, voice=voice, rate=rate, **self._primary_kw)
        self.fallback_name = fallback
        self.fallback = get_provider(fallback, voice=fallback_voice or voice, rate=rate)

    def voices(self) -> list[str]:
        return self.primary.voices()

    def status(self) -> dict:
        getter = getattr(self.primary, "status", None)
        return getter() if getter else {}

    def _chars(self, text: str) -> int:
        import re

        return len(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]", text or ""))

    def _check(self, text: str, res: TTSResult) -> None:
        n = self._chars(text)
        dur = max(0.0, res.duration_ms / 1000.0)
        cps = (n / dur) if (n and dur > 0) else 0.0
        if cps and not (self.min_cps <= cps <= self.max_cps):
            raise RuntimeError("cps_out_of_range %.2f (%.0f chars / %.2fs)" % (cps, n, dur))

    def synthesize(self, text: str, out_path: str) -> TTSResult:
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                res = self.primary.synthesize(text, out_path)
                self._check(text, res)
                return res
            except Exception as exc:  # noqa: BLE001
                last = exc
        res = self.fallback.synthesize(text, out_path)
        res.note = "fallback:%s after %s" % (self.fallback_name, type(last).__name__ if last else "?")
        return res


def _pause_ms() -> int:
    """子句之间的静音长度（毫秒），可用 XF_VOICE_PAUSE_MS 调；0 = 不插。"""
    try:
        return max(0, int(os.environ.get("XF_VOICE_PAUSE_MS", "220") or 0))
    except (TypeError, ValueError):
        return 220


def _pause_parts(text: str) -> list[str]:
    """按逗号/顿号/分号切子句，保留标点。

    GPT-SoVITS 的 cut5 会把切出来的子句直接首尾相接，逗号处的呼吸被抹平，
    听起来就是「两句连在一起念」。这里把子句各自合成，再在拼接时插固定静音
    （默认 220ms）。超过 60 字的长文不切，免得调用次数和时延失控。
    """
    import re as _re

    t = (text or "").strip()
    if not t or len(t) > 60:
        return [t]
    parts = [x.strip() for x in _re.findall(r"[^，,、；;。！？!?…]+[，,、；;。！？!?…]*", t) if x.strip()]
    if len(parts) <= 1:
        return [t]
    # 太短的分片（「啊？」「嗯，」）单独合成容易糊成一声，合并到下一段一起念；
    # 每段至少 4 个实字，既保住停顿又不把字咬丢。
    merged, buf = [], ""
    for x in parts:
        buf += x
        if len(_re.sub(r"[^\w\u4e00-\u9fff]", "", buf)) >= 4:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] += buf
        else:
            merged.append(buf)
    return merged if len(merged) > 1 else [t]


def _trim_edges(frames: bytes, params) -> bytes:
    """掐掉每段首尾的静音，但开头要留足余量。

    「嗯」「啊」「哦」这类语气词起音很轻（能量远低于句子峰值），阈值一高就会被当成
    静音切掉，听起来就是首字糊掉/像半声——所以门限压到峰值的 0.5%，开头多留 80ms，
    结尾仍只留 20ms（停顿靠拼接时补的静音控制）。
    """
    if params.sampwidth != 2:
        return frames
    import array

    a = array.array("h")
    a.frombytes(frames[: len(frames) - len(frames) % 2])
    if len(a) % params.nchannels:
        a = a[: len(a) - len(a) % params.nchannels]
    if not a:
        return frames
    peak = max(abs(v) for v in a)
    if peak <= 0:
        return frames
    thr = max(60, int(peak * 0.005))
    lo, hi = 0, len(a) - 1
    while lo < len(a) and abs(a[lo]) < thr:
        lo += 1
    while hi > lo and abs(a[hi]) < thr:
        hi -= 1
    if hi <= lo:
        return frames
    lead = int(params.framerate * 0.08) * params.nchannels
    tail = int(params.framerate * 0.02) * params.nchannels
    lo = max(0, lo - lead)
    hi = min(len(a) - 1, hi + tail)
    lo -= lo % params.nchannels
    hi -= (hi + 1) % params.nchannels
    return a[lo:hi + 1].tobytes()


def _normalize_level(frames: bytes, params, target: float = 0.1) -> bytes:
    """把每段响度拉到同一水平（约 -20 dBFS），段与段之间不会一段清楚一段发闷。"""
    if params.sampwidth != 2:
        return frames
    import array
    import math

    a = array.array("h")
    a.frombytes(frames[: len(frames) - len(frames) % 2])
    if not a:
        return frames
    rms = math.sqrt(sum(v * v for v in a) / len(a))
    if rms <= 1:
        return frames
    gain = min(3.0, max(0.3, target * 32768 / rms))
    if abs(gain - 1.0) < 0.05:
        return frames
    for i, v in enumerate(a):
        x = int(v * gain)
        a[i] = 32767 if x > 32767 else (-32768 if x < -32768 else x)
    return a.tobytes()


def _merge_wavs(paths: list[str], out_path: str, pause_ms: int) -> None:
    """把多段 PCM wav 首尾接起来，掐干净边界静音后补 pause_ms 的停顿。"""
    import wave

    frames = []
    params = None
    for p in paths:
        with wave.open(p, "rb") as wf:
            if params is None:
                params = wf.getparams()
            frames.append(wf.readframes(wf.getnframes()))
    if params is None:
        raise RuntimeError("no wav frames to merge")
    frames = [_normalize_level(_trim_edges(fr, params), params) for fr in frames]
    pad = b"\x00" * (params.sampwidth * params.nchannels * int(params.framerate * pause_ms / 1000))
    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        for i, fr in enumerate(frames):
            out.writeframes(fr)
            if i < len(frames) - 1:
                out.writeframes(pad)


class GptSoVitsProvider(TTSProvider):
    """Local GPT-SoVITS v2 clone driven through api_v2.py's HTTP API.

    api_v2.py owns the GPU and the fine-tuned weights; this class only speaks
    HTTP so the bridge process stays free of torch.  Failures are loud on
    purpose: the caller must degrade to a text reply, never to another voice.
    """

    name = "gptsovits"

    def __init__(self, voice: str = "noelle", endpoint: Optional[str] = None,
                 ref_wav: Optional[str] = None, prompt_text: Optional[str] = None,
                 prompt_lang: Optional[str] = None, text_lang: Optional[str] = None,
                 speed_factor: float = 1.0, top_k: int = 15, top_p: float = 1.0,
                 temperature: float = 1.0, split_method: str = "cut5",
                 timeout: float = 180.0, rate: Optional[str] = None, seed: int = -1, **_kw):
        ref = VOICE_REFS.get((voice or "noelle").lower(), {})
        self.voice = voice or "noelle"
        self.endpoint = (endpoint or ref.get("endpoint") or "http://127.0.0.1:9880").rstrip("/")
        self.ref_wav = ref_wav or ref.get("ref_wav") or ""
        self.prompt_text = prompt_text or ref.get("prompt_text") or ""
        self.prompt_lang = prompt_lang or ref.get("prompt_lang") or "zh"
        self.text_lang = text_lang or ref.get("text_lang") or "zh"
        self.speed_factor = float(speed_factor or 1.0)
        self.top_k = int(top_k)
        self.top_p = float(top_p)
        self.temperature = float(temperature)
        self.split_method = split_method or "cut5"
        self.timeout = float(timeout)
        # 同一个 seed 出同一版音频；-1 = 每次随机。质检不过时改 seed 重抽一版。
        self.seed = int(seed)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def voices(self) -> list[str]:
        return ["noelle"]

    def alive(self, timeout: float = 1.5) -> bool:
        """True when something is accepting TCP on the api_v2 port."""
        import socket
        from urllib.parse import urlsplit

        parts = urlsplit(self.endpoint)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 9880
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _synth_once(self, text: str, out_path: str) -> TTSResult:
        """单段合成（原来的一次性调用）。"""
        import json

        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        if not self.ref_wav or not os.path.isfile(self.ref_wav):
            raise FileNotFoundError("gpt-sovits reference wav missing: %s" % (self.ref_wav or "<unset>"))
        payload = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_wav,
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_lang,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "text_split_method": self.split_method,
            "speed_factor": self.speed_factor,
            "seed": self.seed,
            "media_type": "wav",
        }
        req = urllib.request.Request(
            self.endpoint + "/tts",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Accept": "audio/wav"},
            method="POST",
        )
        t0 = time.time()
        with self._opener.open(req, timeout=self.timeout) as resp:
            data = resp.read()
        if not data or len(data) < 1024:
            raise RuntimeError("gpt-sovits returned %d bytes (%s)" % (len(data or b""), self.endpoint))
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(data)
        dur = 0
        try:
            from .audio import wav_duration_ms

            dur = int(wav_duration_ms(out_path))
        except Exception:  # noqa: BLE001
            dur = 0
        return TTSResult(path=out_path, duration_ms=dur, provider=self.name,
                         voice=self.voice, note="http %.1fs" % (time.time() - t0))

    def synthesize(self, text: str, out_path: str) -> TTSResult:
        """逗号/顿号/分号处切子句分别合成，再插入静音拼起来。

        引擎的 cut5 是把子句硬接在一起的，短句（像「烦不烦啊，听吧。」）里逗号处的
        停顿只剩 ~40ms，听起来就是连着念。这里为每个子句单独合成，段间补
        XF_VOICE_PAUSE_MS（默认 220ms）静音；任何一段失败就退回整段合成。
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        parts = _pause_parts(text)
        pause_ms = _pause_ms()
        if len(parts) > 1 and pause_ms > 0:
            import shutil
            import tempfile

            tmpdir = tempfile.mkdtemp(prefix="gsv-pause-")
            try:
                outs = []
                t0 = time.time()
                for i, part in enumerate(parts):
                    tmp = os.path.join(tmpdir, "p%02d.wav" % i)
                    self._synth_once(part, tmp)
                    outs.append(tmp)
                os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
                _merge_wavs(outs, out_path, pause_ms)
            except Exception:  # noqa: BLE001 —— 子句合成失败就别丢消息，退回整段
                pass
            else:
                dur = 0
                try:
                    from .audio import wav_duration_ms

                    dur = int(wav_duration_ms(out_path))
                except Exception:  # noqa: BLE001
                    dur = 0
                return TTSResult(
                    path=out_path, duration_ms=dur, provider=self.name, voice=self.voice,
                    note="http %.1fs, %d 段 + %dms 停顿" % (time.time() - t0, len(parts), pause_ms),
                )
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        return self._synth_once(text, out_path)


#: 每个音色固定一个参考片段；api_v2 的零样本/微调推理都要它。
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
VOICE_REFS = {
    "noelle": {
        "endpoint": "http://127.0.0.1:9880",
        "ref_wav": os.path.join(ROOT_DIR, "out", "gsv", "noelle", "100180_audio.wav"),
        "prompt_text": "光是听着就觉得好辣！我真的能吃得下吗…",
        "prompt_lang": "zh",
        "text_lang": "zh",
    },
}


_PROVIDERS = {"edge": EdgeTTSProvider, "cosyvoice2": CosyVoice2Provider,
              "cosyvoice2+edge": FallbackTTSProvider,
              "gptsovits": GptSoVitsProvider}


def get_provider(name: str = "edge", **kwargs) -> TTSProvider:
    key = (name or "edge").lower()
    if key not in _PROVIDERS:
        raise ValueError(f"unknown TTS provider: {name}")
    return _PROVIDERS[key](**kwargs)


def default_out_path(out_dir: str, ext: str = ".mp3") -> str:
    os.makedirs(out_dir, exist_ok=True)
    if not ext.startswith("."):
        ext = "." + ext
    return os.path.join(out_dir, f"tts-{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}{ext}")
