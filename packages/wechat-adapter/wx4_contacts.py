# -*- coding: utf-8 -*-
"""微信 4.x 联系人名册：只读解密 contact.db，把 wxid 换成昵称/备注。

用途：群里每条消息都能标上「张三说」，而不是所有人共用一个「[访客]」。
只读、纯后台：不碰前台窗口、不点鼠标。解密结果缓存到 %TEMP%，contact.db 没更新就不重解。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

import wx4_voice_db as V

_LOCK = threading.Lock()
_CACHE: dict = {"sig": None, "ts": 0.0, "names": {}, "rooms": {}}
_REFRESH_S = 300.0
_REL = "contact/contact.db"


def _db_dir() -> str:
    from wx4_voice_watch import VoiceDBWatcher

    return VoiceDBWatcher()._pick_db_dir()


def _decrypted_copy(db_dir: str) -> str:
    src = os.path.join(db_dir, _REL)
    tmp_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".", "xfy-wx4-contact")
    os.makedirs(tmp_dir, exist_ok=True)
    dst = os.path.join(tmp_dir, "contact.db")
    try:
        sig = (int(os.path.getmtime(src)), os.path.getsize(src),
               int(os.path.getmtime(src + "-wal")) if os.path.exists(src + "-wal") else 0)
    except OSError:
        return ""
    stamp = os.path.join(tmp_dir, "contact.sig")
    try:
        if open(stamp, "r", encoding="utf-8").read() == repr(sig) and os.path.isfile(dst):
            return dst
    except OSError:
        pass
    keys = V.resolve_keys(db_dir, [_REL])
    key = keys.get(_REL) or next(iter(keys.values()), b"")
    if not key:
        return ""
    V.decrypt_database(src, dst, key, apply_wal=True)
    try:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(repr(sig))
    except OSError:
        pass
    return dst


def _load(db_dir: str) -> None:
    path = _decrypted_copy(db_dir)
    if not path:
        return
    con = sqlite3.connect(path)
    try:
        names: dict = {}
        rooms: dict = {}
        rows = con.execute(
            "select username, coalesce(nullif(remark, ''), ''), coalesce(nick_name, '') from contact"
        )
        for username, remark, nick in rows:
            who = str(username or "")
            if not who:
                continue
            label = (str(remark or "").strip() or str(nick or "").strip())
            if label:
                if who.endswith("@chatroom"):
                    rooms[who] = label
                else:
                    names[who] = label
    finally:
        con.close()
    _CACHE["names"] = names
    _CACHE["rooms"] = rooms
    _CACHE["ts"] = time.time()


def refresh(force: bool = False) -> None:
    """重新载入名册（默认 5 分钟最多一次）。失败静默，调用方自己退回 wxid。"""
    with _LOCK:
        if not force and _CACHE["ts"] and time.time() - _CACHE["ts"] < _REFRESH_S:
            return
        try:
            _load(_db_dir())
        except Exception:  # noqa: BLE001
            _CACHE["ts"] = time.time()


def name_of(wxid: str) -> str:
    who = str(wxid or "").strip()
    if not who:
        return ""
    refresh()
    label = _CACHE["names"].get(who) or _CACHE["rooms"].get(who) or ""
    if label:
        return label
    if who.endswith("@chatroom"):
        return ""
    return who if not who.startswith("wxid_") else who[-4:]


def room_name_of(wxid: str) -> str:
    who = str(wxid or "").strip()
    if not who:
        return ""
    refresh()
    return _CACHE["rooms"].get(who) or ""


def names_of(wxids) -> dict:
    refresh()
    return {w: name_of(w) for w in wxids}
