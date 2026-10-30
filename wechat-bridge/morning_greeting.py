"""早上 9:20 给主人发一句早安（由 Windows 计划任务调用，跟桥进程互不相干）。

独立进程的意义：桥改代码要重启才生效，而这个脚本只读配置、不碰桥状态。
失败只写日志，绝不弹窗、不重试到把界面搅乱。
"""

from __future__ import annotations

import io
import json
import random
import sys
import time
from datetime import datetime, date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

def _owner_from_config() -> str:
    try:
        cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8-sig"))
        return str((cfg.get("identity") or {}).get("owner_wxid") or "").strip()
    except Exception:
        return ""


OWNER = _owner_from_config()
if not OWNER:
    log("config.json 里没有 identity.owner_wxid，跳过早安")
    sys.exit(0)
LOG = HERE / "morning_greeting.log"
DONE = HERE / "morning_greeting.last"

LINES = [
    "早呀，九点二十啦，起来没",
    "早，闹钟响了没，别赖着哦",
    "早上好呀，今天也先吃口热的",
    "九点二十咯，睁开眼睛啦",
    "早呀，我先叫你一声，快起吧",
]


def log(msg: str) -> None:
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def main() -> int:
    today = date.today().isoformat()
    try:
        if DONE.exists() and DONE.read_text(encoding="utf-8").strip() == today:
            log("今天已经发过了，跳过")
            return 0
    except Exception:
        pass

    try:
        import wxui
        from wechatauto.db import WeChatDB

        db = WeChatDB()
        name = (db.get_nickname(OWNER) or "").strip()
        if not name:
            log("拿不到主人的备注名，放弃")
            return 1
        line = random.choice(LINES)
        wxui.send_text(name, line)
        DONE.write_text(today, encoding="utf-8")
        log(f"已发早安 → {name}：{line}")
        return 0
    except Exception as exc:
        log(f"发送失败：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
