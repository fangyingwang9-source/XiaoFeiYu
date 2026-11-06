"""语音接收（耳朵）：适配层拦截微信语音缓存 -> SILK 转 WAV -> ASR -> 日志/回调。

分工（架构约束）：
- 文件监听只在 wechat-adapter（WeChatAdapter.iter_voice_messages），业务层不碰微信目录；
- 转码 / 识别 / 落盘日志在本模块（bridge-core/voice）；
- 全程纯后台只读：不置前窗口、不发按键、不点鼠标。
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "out")
LOG_PATH = os.path.join(OUT_DIR, "voice-in.jsonl")

try:  # 包内 / 直跑兼容
    from .asr import SenseVoiceASR
    from .audio import decode_voice_file, load_wav_16k
except ImportError:  # pragma: no cover
    from asr import SenseVoiceASR  # type: ignore
    from audio import decode_voice_file, load_wav_16k  # type: ignore

KEEP = 200


class VoiceListener:
    """常驻后台线程：等适配层交出语音文件，转 WAV、识别、记日志。"""

    def __init__(self, adapter=None, asr=None, log_path: str = LOG_PATH,
                 poll_seconds: float = 0.8, on_record=None):
        self.adapter = adapter
        self.asr = asr or SenseVoiceASR()
        self.log_path = log_path
        self.poll_seconds = poll_seconds
        self.on_record = on_record
        self.records = []
        self.count = 0
        self.last = None
        self.error = ""
        self.started_at = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # -- 生命周期 --
    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="voice-ear", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        return True

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self):
        try:
            if self.adapter is None:
                from factory import get_adapter  # 适配层工厂，保证不直连微信底层

                self.adapter = get_adapter()
            for msg in self.adapter.iter_voice_messages(stop_event=self._stop,
                                                        poll_seconds=self.poll_seconds):
                if self._stop.is_set():
                    break
                self.handle(msg)
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self._log({"ts": time.time(), "ok": False, "stage": "watch",
                       "error": self.error, "trace": traceback.format_exc().splitlines()[-4:]})

    # -- 单条处理：转码 -> 识别 -> 记日志 --
    def handle(self, msg) -> dict:
        t0 = time.time()
        rec = {"ts": t0, "path": msg.path, "size": getattr(msg, "size", 0),
               "chat": getattr(msg, "chat", ""), "head": getattr(msg, "head_hex", ""),
               "source": getattr(msg, "source", "")}
        try:
            wav = decode_voice_file(msg.path)
            rec["wav"] = wav
            rec["transcode_ms"] = int((time.time() - t0) * 1000)
            samples = load_wav_16k(wav)
            rec["audio_ms"] = int(len(samples) / 16000 * 1000)
            rec["audio_s"] = round(len(samples) / 16000, 2)
            t1 = time.time()
            text = self.asr.transcribe_samples(samples)
            rec["asr_ms"] = int((time.time() - t1) * 1000)
            rec["text"] = text or ""
            rec["ok"] = True
            rec["total_ms"] = int((time.time() - t0) * 1000)
            # 详细过程打一行到桥日志：silk 字节/文件头 -> wav -> 转码耗时 -> 音频时长 -> ASR 耗时 -> 文字
            print("[ear] silk=%dB head=%s chat=%s -> wav(%s) %sms -> %.2fs -> ASR %sms -> %r"
                  % (rec["size"], rec["head"], rec["chat"], os.path.basename(wav),
                     rec["transcode_ms"], rec["audio_s"], rec["asr_ms"], rec["text"]), flush=True)
        except Exception as exc:  # noqa: BLE001
            rec.update({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc().splitlines()[-4:]})
            print("[ear] FAIL silk=%dB head=%s %s: %s"
                  % (rec["size"], rec["head"], type(exc).__name__, exc), flush=True)
        with self._lock:
            self.count += 1
            self.last = rec
            self.records.append(rec)
            del self.records[:-KEEP]
        self._log(rec)
        if self.on_record:
            try:
                self.on_record(rec)
            except Exception:  # noqa: BLE001
                pass
        return rec

    def _log(self, rec: dict):
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def status(self) -> dict:
        return {"running": self.running(), "count": self.count, "error": self.error,
                "uptime_s": int(time.time() - self.started_at) if self.started_at else 0,
                "log_path": self.log_path, "last": self.last,
                "adapter_scan": getattr(getattr(self.adapter, "last_scan", None), "copy", lambda: None)()
                if hasattr(getattr(self.adapter, "last_scan", None), "copy") else None}


if __name__ == "__main__":
    import sys

    from factory import get_adapter

    listener = VoiceListener(adapter=get_adapter())
    listener.start()
    print(f"[ear] listening, log={listener.log_path}", flush=True)
    deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 120)
    seen = 0
    try:
        while time.time() < deadline:
            time.sleep(0.5)
            while seen < len(listener.records):
                rec = listener.records[seen]
                seen += 1
                print(json.dumps(rec, ensure_ascii=False), flush=True)
    finally:
        listener.stop()
        print("[ear] stopped", flush=True)
