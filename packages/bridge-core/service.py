"""bridge-core local control service.

A single-file stdlib HTTP server bound to 127.0.0.1. The DSH host plugin
(packages/console-web) proxies /api/dsh/xfy/* to this service, and the room
plugin will consume the same contract later.

Contract (all JSON):
  GET  /status          -> {ok, service, bridge_pid, asr, tts, wechat, ...}
  POST /tts             -> {ok, path, duration_ms, provider, voice}
  POST /asr             -> {ok, text}                      body {"path": "..."}
  POST /voice/loopback  -> {ok, asr_text, tts_path, ...}    synthesize -> decode -> recognize
  POST /voice/send      -> {ok, sent, hold_s, steps}         text/audio -> 注入 -> 按住&松开右 Alt 发语音
  GET  /config          -> persona/strategy snapshot
  POST /config          -> merge + persist to wechat-bridge/config.json (with backup)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # workspace root
BRIDGE_DIR = os.path.join(ROOT, "wechat-bridge")
CONFIG_PATH = os.path.join(BRIDGE_DIR, "config.json")
OUT_DIR = os.path.join(HERE, "out")
VENDOR = os.path.join(HERE, "vendor")
PORT = int(os.environ.get("BRIDGE_CORE_PORT", "8787"))

ADAPTER_DIR = os.path.join(ROOT, "packages", "wechat-adapter")
for _extra in (VENDOR, ADAPTER_DIR):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

from voice.tts import default_out_path, get_provider  # noqa: E402

_asr = None
_asr_lock = threading.Lock()


def _get_asr():
    global _asr
    with _asr_lock:
        if _asr is None:
            from voice.asr import SenseVoiceASR

            _asr = SenseVoiceASR()
        return _asr


_ear = None
_ear_lock = threading.Lock()


def _get_ear():
    """语音接收（耳朵）单例：适配层负责监听，这里只做转码/识别/日志。"""
    global _ear
    with _ear_lock:
        if _ear is None:
            from factory import get_adapter  # wechat-adapter 工厂
            from voice.listener import VoiceListener

            _ear = VoiceListener(adapter=get_adapter(), asr=_get_asr())
        return _ear


def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _pid_alive(pid: int) -> bool:
    """Windows 上 os.kill(pid, 0) 会 TerminateProcess，改用 OpenProcess 探活。"""
    if not pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def _bridge_status() -> dict:
    pid = None
    alive = False
    pid_file = os.path.join(BRIDGE_DIR, "bridge.pid")
    try:
        pid = int(open(pid_file, "r", encoding="utf-8").read().strip())
        alive = _pid_alive(pid)
    except Exception:
        alive = False
    state = _read_json(os.path.join(BRIDGE_DIR, "wechat-bridge-state.json"), {}) or {}
    return {"alive": alive, "pid": pid, "state": {k: state.get(k) for k in ("since", "wechat", "last_error") if k in state}}


def _tts_config():
    cfg = _read_json(CONFIG_PATH, {}) or {}
    voice = cfg.get("voice") or {}
    return {
        "provider": voice.get("tts_provider") or "edge",
        "voice": voice.get("tts_voice") or "zh-CN-XiaoxiaoNeural",
        "rate": voice.get("tts_rate") or "+0%",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "bridge-core/0.1"
    def log_message(self, fmt, *args):  # noqa: D401 - quiet
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/status":
            cfg = _tts_config()
            return self._send(200, {
                "ok": True, "service": "bridge-core", "ts": int(time.time()),
                "bridge": _bridge_status(),
                "asr": {"provider": "sensevoice-int8", "ready": _get_asr().ready(), "model_dir": _get_asr().model_dir},
                "tts": {"provider": cfg["provider"], "voice": cfg["voice"], "voices": get_provider("edge").voices()},
                "voice": _voice_status(cfg),
            })
        if self.path.split("?")[0] == "/config":
            return self._send(200, {"ok": True, "config": _public_config()})
        return self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        try:
            if path == "/tts":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send(400, {"ok": False, "error": "empty_text"})
                cfg = _tts_config()
                provider = get_provider(cfg["provider"], voice=cfg["voice"], rate=cfg["rate"])
                res = provider.synthesize(text, default_out_path(OUT_DIR))
                return self._send(200, {"ok": True, "path": res.path, "duration_ms": res.duration_ms,
                                        "provider": res.provider, "voice": res.voice})
            if path == "/asr":
                src = body.get("path") or ""
                if not os.path.isfile(src):
                    return self._send(400, {"ok": False, "error": "file_not_found", "path": src})
                t0 = time.time()
                text = _get_asr().transcribe_file(src)
                return self._send(200, {"ok": True, "text": text, "elapsed_ms": int((time.time() - t0) * 1000)})
            if path == "/voice/loopback":
                text = (body.get("text") or "小肥鱼语音链路自检").strip()
                cfg = _tts_config()
                provider = get_provider(cfg["provider"], voice=cfg["voice"], rate=cfg["rate"])
                res = provider.synthesize(text, default_out_path(OUT_DIR))
                t0 = time.time()
                asr_text = _get_asr().transcribe_file(res.path)
                return self._send(200, {"ok": True, "input": text, "asr_text": asr_text,
                                        "tts_path": res.path, "duration_ms": res.duration_ms,
                                        "asr_ms": int((time.time() - t0) * 1000)})
            if path == "/voice/send":
                from voice.sender import send_voice

                who = (body.get("to") or "文件传输助手").strip()
                text = (body.get("text") or "").strip()
                audio = (body.get("path") or "").strip()
                if not text and not (audio and os.path.isfile(audio)):
                    return self._send(400, {"ok": False, "error": "need_text_or_path"})
                return self._send(200, send_voice(who, text=text or None, audio_path=audio or None,
                                                  rate=body.get("rate") or None))
            if path == "/voice/in/status":
                return self._send(200, {"ok": True, **_get_ear().status()})
            if path == "/voice/in/start":
                ear = _get_ear()
                started = ear.start()
                return self._send(200, {"ok": True, "started": started, **ear.status()})
            if path == "/voice/in/stop":
                ear = _get_ear()
                ear.stop()
                return self._send(200, {"ok": True, **ear.status()})
            if path == "/voice/in/log":
                n = int(body.get("n") or 20)
                ear = _get_ear()
                return self._send(200, {"ok": True, "records": ear.records[-n:], "log_path": ear.log_path})
            if path == "/config":
                return self._send(200, _save_config(body))
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"ok": False, "error": type(exc).__name__, "detail": str(exc),
                                    "trace": traceback.format_exc().splitlines()[-3:]})
        return self._send(404, {"ok": False, "error": "not_found"})


def _feeder_name(configured) -> str:
    """报实际会用的声道（和 sender 的选择逻辑一致），只做轻量可用性判断。"""
    if configured:
        return str(configured)
    try:
        from voice.feeders import VirtualCableFeeder

        return "virtual-cable" if VirtualCableFeeder().available() else "speaker-loopback"
    except Exception:  # noqa: BLE001
        return "speaker-loopback"


def _voice_status(cfg: dict) -> dict:
    """只回配置层面的状态，不做 UIA 扫描（/status 会被控制台高频轮询）。"""
    c = _read_json(CONFIG_PATH, {}) or {}
    voice = c.get("voice") or {}
    return {"adapter": os.environ.get("WECHAT_ADAPTER").strip().lower() if os.environ.get("WECHAT_ADAPTER") else "wx4",
            "feeder": _feeder_name(voice.get("feeder")),
            "tts": cfg["provider"]}


def _public_config() -> dict:
    cfg = _read_json(CONFIG_PATH, {}) or {}
    return {"persona": cfg.get("persona", {}), "strategy": cfg.get("strategy", {}),
            "voice": cfg.get("voice", {}), "preset": cfg.get("preset")}


def _save_config(patch: dict) -> dict:
    if not isinstance(patch, dict) or not patch:
        return {"ok": False, "error": "empty_patch"}
    cfg = _read_json(CONFIG_PATH, {}) or {}
    backup = CONFIG_PATH + time.strftime(".bak-%Y%m%d-%H%M%S")
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = fh.read()
        with open(backup, "w", encoding="utf-8") as fh:
            fh.write(raw)
    except Exception:
        pass
    for section in ("persona", "strategy", "voice"):
        if isinstance(patch.get(section), dict):
            merged = dict(cfg.get(section) or {})
            merged.update(patch[section])
            cfg[section] = merged
    if isinstance(patch.get("preset"), str):
        cfg["preset"] = patch["preset"]
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return {"ok": True, "backup": backup, "config": _public_config()}


def serve(host: str = "127.0.0.1", port: int = 8787):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    cfg = _read_json(CONFIG_PATH, {}) or {}
    if os.environ.get("XFY_EAR") == "1" or (cfg.get("voice") or {}).get("voice_input"):
        try:
            _get_ear().start()
        except Exception as exc:  # noqa: BLE001
            print(f"[ear] autostart failed: {type(exc).__name__}: {exc}", flush=True)
    return httpd


def main():
    port = int(os.environ.get("BRIDGE_CORE_PORT", "8787"))
    httpd = serve(port=port)
    print(f"bridge-core service on http://127.0.0.1:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
