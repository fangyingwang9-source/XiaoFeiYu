"""把歌词真唱出来：整句一次性交给 GPT-SoVITS 合成，再用 DSP 按曲谱改音高与时长。

旧做法是「逐字合成」：每个字一次 TTS 请求，上下文断裂、音色跳动、拼接痕迹重，
听感就是「一个字一个字念」。现在只发一次请求（整句连续发音，音色/咬字自然），
再在音频上做切分 -> 变速 -> 变调 -> 颤音，最后 ffmpeg 转 mp3。

在 GPT-SoVITS 的 venv 里跑（那里有 numpy / librosa / soundfile）：
    python sing.py --text "小鱼小鱼游呀游" --out out/sing-xxx.mp3 --core http://127.0.0.1:8787 --ffmpeg out/gsv/bin/ffmpeg.exe

成功时最后一行打印 JSON：{"ok": true, "chars": n, "duration": s, "mode": "sentence", "notes": [hz...]}
失败时非 0 退出码 + stderr 写明原因。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
import librosa

SR = 32000
MAX_CHARS = 20

TWINKLE = [(261.63, 0.42), (261.63, 0.42), (392.00, 0.42), (392.00, 0.42),
           (440.00, 0.42), (440.00, 0.42), (392.00, 0.62),
           (349.23, 0.42), (349.23, 0.42), (329.63, 0.42), (329.63, 0.42),
           (293.66, 0.42), (293.66, 0.42), (261.63, 0.90)]
PENTA = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25, 587.33, 659.25, 783.99, 880.00]


def notes_for(n):
    if n <= len(TWINKLE):
        notes = list(TWINKLE[:n])
    else:
        notes = list(TWINKLE)
        k = 0
        while len(notes) < n:
            notes.append((PENTA[(k * 3) % len(PENTA)], 0.42 if k % 3 else 0.58))
            k += 1
        notes = notes[:n]
    if notes:
        f, d = notes[-1]
        notes[-1] = (f, max(d, 0.85))
    return notes


def clean_lyrics(text):
    s = "".join(re.findall(r"[\u4e00-\u9fff0-9a-zA-Z]", str(text or "")))
    return s[:MAX_CHARS]


def tts_sentence(text, core, tmp, ffmpeg):
    """整句一次合成：POST {core}/tts 只发一段完整歌词，拿回 wav。"""
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(core.rstrip("/") + "/tts", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    raw = tmp / "sentence.wav"
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    # 桥的 /tts 返回 JSON（{"ok":true,"path":"...mp3"}），只有兜底 provider 才直接回音频字节
    if data[:1] == b"{":
        try:
            meta = json.loads(data.decode("utf-8", "replace"))
        except Exception:
            meta = {}
        if not meta.get("ok") or not meta.get("path"):
            raise RuntimeError("TTS 失败：%s" % str(meta)[:200])
        src = Path(meta["path"])
        if not src.exists() or src.stat().st_size < 1024:
            raise RuntimeError("TTS 文件不存在或过小：%s" % src)
        data = src.read_bytes()
    if len(data) < 1024:
        raise RuntimeError("TTS 返回过小（%d 字节）" % len(data))
    raw.write_bytes(data)
    wav = tmp / "sentence32k.wav"
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(raw),
           "-ac", "1", "-ar", str(SR), "-acodec", "pcm_s16le", str(wav)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not wav.exists():
        raise RuntimeError("重采样失败")
    y, _ = sf.read(str(wav), dtype="float32", always_2d=False)
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    y = np.asarray(y, dtype=np.float32)
    if y.size < int(SR * 0.2):
        raise RuntimeError("合成音频过短（%.2fs）" % (y.size / float(SR)))
    return y


def _trim(y):
    if y.size == 0:
        return y
    env = np.abs(y)
    thr = max(float(np.max(env)) * 0.02, 1e-4)
    idx = np.where(env > thr)[0]
    if idx.size == 0:
        return y
    return y[int(idx[0]): int(idx[-1]) + 1]


def split_syllables(y, n):
    """按能量低谷把整句切成 n 段（中文一字一音节，谷点≈字与字交界）。"""
    if n <= 1:
        return [(0, y.size)]
    hop = 256
    env = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
    env = librosa.util.normalize(env)
    order = np.argsort(env)
    min_gap = max(2, int((len(env) / float(n)) * 0.45))
    picked = []
    for f in order:
        if env[f] > 0.45:
            break
        if all(abs(int(f) - p) >= min_gap for p in picked):
            picked.append(int(f))
        if len(picked) >= n - 1:
            break
    cuts = sorted(int(p * hop) for p in picked)
    if len(cuts) != n - 1:
        step = y.size / float(n)
        return [(int(step * i), int(step * (i + 1))) for i in range(n)]
    edges = [0] + [c for c in cuts if 0 < c < y.size] + [y.size]
    segs = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)
            if edges[i + 1] - edges[i] >= int(SR * 0.03)]
    if len(segs) != n:
        step = y.size / float(n)
        return [(int(step * i), int(step * (i + 1))) for i in range(n)]
    return segs


def _f0(y):
    if y.size < int(SR * 0.06):
        return 0.0
    try:
        f0 = librosa.yin(y, fmin=80.0, fmax=600.0, sr=SR, frame_length=2048)
    except Exception:
        return 0.0
    f0 = f0[np.isfinite(f0)]
    if f0.size == 0:
        return 0.0
    return float(np.median(f0))


def _vibrato(z, rate=5.2, depth=0.012, delay=0.18):
    n = z.size
    if n < int(SR * 0.25):
        return z
    t = np.arange(n) / float(SR)
    ramp = np.clip((t - delay) / max(1e-3, (t[-1] - delay)), 0.0, 1.0)
    idx = np.arange(n) + (np.sin(2 * np.pi * rate * t) * depth * SR * ramp).astype(np.int32)
    return z[np.clip(idx, 0, n - 1)]


def sing_syllable(y, dur, target_hz):
    """把一个音节变速到 dur 秒、变调到 target_hz，再补颤音与淡入淡出。"""
    y = _trim(np.asarray(y, dtype=np.float32))
    want = max(int(SR * 0.06), int(round(SR * float(dur))))
    if y.size < int(SR * 0.05):
        return np.zeros(want, dtype=np.float32)
    cur = _f0(y)
    steps = 0.0
    if cur > 0 and target_hz > 0:
        steps = float(np.clip(12.0 * np.log2(target_hz / cur), -7.0, 7.0))
    rate = y.size / float(want)
    z = y
    if abs(rate - 1.0) > 0.04:
        z = librosa.effects.time_stretch(z, rate=float(np.clip(rate, 0.5, 2.0)))
    if abs(steps) > 0.2:
        z = librosa.effects.pitch_shift(z, sr=SR, n_steps=steps)
    if z.size >= want:
        z = z[:want]
    else:
        z = np.pad(z, (0, want - z.size))
    z = _vibrato(z)
    fade = min(int(SR * 0.015), z.size // 4)
    if fade > 0:
        z[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        z[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return np.asarray(z, dtype=np.float32)


def _rvc_convert(a, src, dst) -> None:
    """把唱好的干声过一遍 RVC，换成目标音色（子进程调 infer/cli.py）。"""
    py = a.rvc_python or sys.executable
    cmd = [py, "-m", "infer.cli", "--model", a.rvc_model, "--input", str(src),
           "--output", str(dst), "--pitch", str(a.rvc_pitch),
           "--index-rate", str(a.rvc_index_rate), "--overwrite"]
    if a.rvc_index:
        cmd += ["--index", a.rvc_index]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(cmd, cwd=(a.rvc_dir or None), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=a.rvc_timeout)
    if proc.returncode != 0 or not dst.exists():
        tail = (((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines())
        raise RuntimeError((tail[-1] if tail else "rvc exit %s" % proc.returncode)[:300])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--core", default="http://127.0.0.1:8787")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--rvc-dir", default="")
    ap.add_argument("--rvc-model", default="")
    ap.add_argument("--rvc-index", default="")
    ap.add_argument("--rvc-python", default="")
    ap.add_argument("--rvc-pitch", type=int, default=0)
    ap.add_argument("--rvc-index-rate", type=float, default=0.0)
    ap.add_argument("--rvc-timeout", type=float, default=240.0)
    ap.add_argument("--keep-wav", default="")
    a = ap.parse_args()
    lyrics = clean_lyrics(a.text)[: max(1, a.max_chars)]
    if not lyrics:
        print("no lyrics", file=sys.stderr)
        return 2
    notes = notes_for(len(lyrics))
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            whole = tts_sentence(lyrics, a.core, tmp, a.ffmpeg)
        except Exception as exc:
            print("sentence tts failed: %s" % exc, file=sys.stderr)
            return 3
        segs = split_syllables(whole, len(lyrics))
        pieces = []
        for (aaa, bbb), (hz, dur) in zip(segs, notes):
            pieces.append(sing_syllable(whole[aaa:bbb], dur, hz))
            pieces.append(np.zeros(int(SR * 0.012), dtype=np.float32))
        song = np.concatenate(pieces) if pieces else np.zeros(SR, dtype=np.float32)
    peak = float(np.max(np.abs(song))) if song.size else 0.0
    if peak > 1e-6:
        song = song / peak * 0.88
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wav_tmp = out.with_suffix(".work.wav")
    rvc_tmp = out.with_suffix(".rvc.wav")
    sf.write(str(wav_tmp), song.astype(np.float32), SR)
    source = wav_tmp
    rvc_used = False
    if a.rvc_model:
        try:
            _rvc_convert(a, wav_tmp, rvc_tmp)
            source = rvc_tmp
            rvc_used = True
        except Exception as exc:  # noqa: BLE001  RVC 挂了也要把 DSP 版发出去
            print("rvc failed: %s" % exc, file=sys.stderr)
    if a.keep_wav:
        try:
            keep = Path(a.keep_wav)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(source), str(keep))
        except OSError:
            pass
    cmd = [a.ffmpeg, "-y", "-loglevel", "error", "-i", str(source),
           "-codec:a", "libmp3lame", "-b:a", "96k", str(out)]
    proc = subprocess.run(cmd, capture_output=True)
    for leftover in (wav_tmp, rvc_tmp):
        try:
            leftover.unlink()
        except OSError:
            pass
    if proc.returncode != 0 or not out.exists():
        print("encode failed", file=sys.stderr)
        return 4
    print(json.dumps({"ok": True, "chars": len(lyrics),
                      "duration": round(float(song.size) / float(SR), 2),
                      "mode": "sentence", "rvc": rvc_used,
                      "synth_s": round(time.time() - t0, 2),
                      "notes": [round(h, 1) for h, _ in notes]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
