"""微信 4.x 收到语音的监听器：轮询解密后的 VoiceInfo 表，把新语音交给上层。

为什么不用文件监听：微信 4.x 收到语音不会再落 .silk 文件，语音字节存在
db_storage/message/media_0.db 的 VoiceInfo.voice_data 里（SQLCipher 加密）。
这里只做纯后台只读（解密到临时目录 + 轮询 mtime），不置前窗口、不发按键。
"""
from __future__ import annotations

import json
import os
import tempfile
import time

try:
    from .base import VoiceMessage
    from .wx4_voice_db import VoiceDBReader  # type: ignore
except Exception:  # pragma: no cover - 兼容扁平导入（adapter 目录在 sys.path 上）
    from base import VoiceMessage  # type: ignore
    from wx4_voice_db import VoiceDBReader  # type: ignore

SEEN_PATH = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                         "xfy-wx4-ear-seen.json")


def _load_seen(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(tuple(x) for x in data.get("seen", []))
    except (OSError, ValueError, TypeError):
        return set()


def _save_seen(path, seen):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"seen": [list(x) for x in sorted(seen)][-4000:]}, fh)
    except OSError:
        pass


class VoiceDBWatcher:
    """基线跳过历史语音，只 yield 之后新到的「对方发的」语音。"""

    def __init__(self, db_dir=None, wxid="", state_path=None):
        self.db_dir = db_dir
        self.wxid = wxid
        self.state_path = state_path or SEEN_PATH
        self.last_scan = {}
        self.reader = None
        self._seen = _load_seen(self.state_path)

    def _pick_db_dir(self):
        if self.db_dir:
            return self.db_dir
        roots = []
        env = os.environ.get("WECHAT_FILES_ROOT")
        if env:
            roots.append(env)
        roots += [r"D:" + chr(92) + "xwechat_files",
                  os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files")]
        best, best_mtime = None, -1.0
        for root in roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                db = os.path.join(root, name, "db_storage")
                media = os.path.join(db, "message", "media_0.db")
                if not os.path.isfile(media):
                    continue
                if self.wxid and name != self.wxid:
                    continue
                try:
                    mt = os.path.getmtime(media)
                except OSError:
                    mt = 0.0
                if mt > best_mtime:
                    best, best_mtime = db, mt
        if not best:
            raise RuntimeError("找不到微信 4.x 账号目录（db_storage/message/media_0.db）")
        return best

    def reader_for(self):
        if self.reader and self.db_dir:
            return self.reader
        self.db_dir = self._pick_db_dir()
        self.reader = VoiceDBReader(self.db_dir)
        return self.reader

    def scan_once(self, limit=20):
        rd = self.reader_for()
        rows = rd.voices(limit)
        fresh = []
        for r in rows:
            key = (int(r["ts"]), int(r["local_id"]))
            if key in self._seen:
                continue
            self._seen.add(key)
            if r.get("from_me"):
                continue
            fresh.append(r)
        # 就地改同一个 dict，adapter.last_scan 才能实时反映扫描状态
        self.last_scan.clear()
        self.last_scan.update({"db_dir": self.db_dir, "rows": len(rows),
                               "new": len(fresh), "seen": len(self._seen),
                               "at": time.time()})
        return rd, fresh

    def iter_messages(self, stop_event=None, poll_seconds=0.8):
        rd = self.reader_for()
        # baseline：首次启动只记历史，不把旧语音当新消息念一遍
        for r in rd.voices(50):
            self._seen.add((int(r["ts"]), int(r["local_id"])))
        _save_seen(self.state_path, self._seen)
        self.last_scan.clear()
        self.last_scan.update({"db_dir": self.db_dir, "baseline": len(self._seen), "at": time.time()})
        last_sig = rd.mtime_sig()
        while not (stop_event is not None and stop_event.is_set()):
            time.sleep(poll_seconds)
            try:
                sig = rd.mtime_sig()
            except OSError:
                continue
            if sig == last_sig:
                continue
            last_sig = sig
            try:
                rd, fresh = self.scan_once()
            except Exception as exc:  # noqa: BLE001
                self.last_scan.clear()
                self.last_scan.update({"error": str(exc) + " " + type(exc).__name__, "at": time.time()})
                continue
            if fresh:
                _save_seen(self.state_path, self._seen)
            for r in reversed(fresh):
                path = rd.write_silk(r)
                yield VoiceMessage(
                    path=path,
                    chat=r.get("chat", ""),
                    duration_s=0.0,
                    ts=float(r.get("ts", 0)),
                    size=int(r.get("size", 0)),
                    source="wx4-db:VoiceInfo",
                    head_hex=bytes(r.get("silk", b"")[:12]).hex(),
                )
