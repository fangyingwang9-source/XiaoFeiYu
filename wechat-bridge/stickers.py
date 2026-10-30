# -*- coding: utf-8 -*-
"""表情包库：本地 nekowhale 知识库 → 按情绪/标签挑一张，按需下载 gif 到 files/ 缓存。"""
from __future__ import annotations

import json
import random
import re
import urllib.parse
import urllib.request
from pathlib import Path

SITE = "https://kiriyuki.com"
GIF_BASE = "/media/gif"
UNSAFE = re.compile(r"[\\/:*?\"<>|\s]+")


class StickerLib:
    def __init__(self, root, timeout: float = 25.0):
        self.root = Path(root)
        self.files = self.root / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.items = []
        meta = self.root / "meta.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                self.items = [s for s in (data.get("stickers") or []) if s.get("id")]
            except Exception:
                self.items = []
        self.by_emotion = {}
        for s in self.items:
            self.by_emotion.setdefault(s.get("emotion") or "其他", []).append(s)
        self.recent = []

    def __bool__(self):
        return bool(self.items)

    def count(self) -> int:
        return len(self.items)

    def emotions(self) -> list:
        return sorted(self.by_emotion)

    def pick(self, ref: str):
        """按情绪词/标签/名字挑一张；挑不到返回 None。"""
        ref = (ref or "").strip().strip("：:。.,，")
        if not ref or not self.items:
            return None
        cands = list(self.by_emotion.get(ref, []))
        if not cands and len(ref) >= 2:
            for s in self.items:
                hay = " ".join([s.get("id") or "", s.get("text") or "", s.get("use") or "",
                                s.get("emotion") or "", " ".join(s.get("tags") or [])])
                if ref in hay:
                    cands.append(s)
        if not cands:
            return None
        fresh = [s for s in cands if s.get("id") not in self.recent] or cands
        s = random.choice(fresh)
        self.recent.append(s.get("id"))
        del self.recent[:-6]
        return s

    def _name(self, item: dict) -> str:
        raw = (item.get("id") or "sticker").replace("/", "_")
        return (UNSAFE.sub("_", raw) or "sticker") + ".gif"

    def url(self, item: dict) -> str:
        pack, _, stem = (item.get("id") or "").partition("/")
        q = urllib.parse.quote
        return "%s%s/%s/%s.gif" % (SITE, GIF_BASE, q(pack or "大肥鱼", safe=""), q(stem or item["id"], safe=""))

    def local_path(self, item: dict) -> str:
        p = self.files / self._name(item)
        if p.exists() and p.stat().st_size > 1024:
            return str(p)
        try:
            req = urllib.request.Request(self.url(item), headers={
                "User-Agent": "Mozilla/5.0", "Referer": SITE + "/meme/"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                blob = r.read()
            if blob and len(blob) > 1024:
                p.write_bytes(blob)
                return str(p)
        except Exception:
            return ""
        return ""
