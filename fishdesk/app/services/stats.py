"""统计：token 用量、人设字数、日志体积、目录位置。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _walk_sum(obj, total: float) -> float:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (int, float)) and ("token" in kl or "cache" in kl):
                total += float(v)
            else:
                total = _walk_sum(v, total)
    elif isinstance(obj, list):
        for v in obj:
            total = _walk_sum(v, total)
    return total


class StatsService:
    def __init__(self, ws: Path):
        self.ws = Path(ws)
        self.dir = self.ws / "wechat-bridge"
        self.usage = self.dir / "usage.jsonl"

    def token_today(self) -> dict:
        out = {"date": datetime.now().strftime("%Y-%m-%d"), "calls": 0, "tokens": 0, "bytes": 0}
        if not self.usage.is_file():
            return out
        today = out["date"]
        try:
            with open(self.usage, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or today not in line[:40]:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    out["calls"] += 1
                    out["tokens"] += int(_walk_sum(d, 0))
        except Exception:
            pass
        try:
            out["bytes"] = self.usage.stat().st_size
        except Exception:
            pass
        return out

    def persona_chars(self) -> int:
        p = self.dir / "persona.md"
        try:
            return len(p.read_text(encoding="utf-8").strip())
        except Exception:
            return 0

    def file_size(self, name: str) -> int:
        try:
            return (self.dir / name).stat().st_size
        except Exception:
            return 0
