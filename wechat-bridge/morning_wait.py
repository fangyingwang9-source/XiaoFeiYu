"""每天 9:20 给主人发一句早安（独立后台常驻进程）。

为什么不用计划任务：这台机器上沙箱不允许创建 schtasks 任务，只能起一个常驻进程自己看表。
系统睡眠时进程被挂起，醒来后立刻比对时间：过点不久就补发，过太久就跳过当天。
"""

from __future__ import annotations

import io
import json
import random
import sys
import time
from datetime import date, datetime, timedelta
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
LOG = HERE / "morning_wait.log"
DONE = HERE / "morning_greeting.last"
HOUR, MINUTE = 9, 20
CATCH_UP_HOURS = 3      # 醒来时已经过了点，但没超这么久 → 还是补一句
MAX_TRIES = 3

LINES = [
    "早呀，九点二十啦，起来没",
    "早，闹钟响了没，别赖着哦",
    "早上好呀，今天也先吃口热的",
    "九点二十咯，睁开眼睛啦",
    "早呀，我先叫你一声，快起吧",
]


ALIVE = HERE / "morning_wait.alive"


def log(msg: str) -> None:
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def beat() -> None:
    """心跳：外部靠这个文件的时间戳判断进程是否还活着。"""
    try:
        ALIVE.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass


def sent_today() -> bool:
    try:
        return DONE.exists() and DONE.read_text(encoding="utf-8").strip() == date.today().isoformat()
    except Exception:
        return False


def send_once() -> bool:
    try:
        import wxui
        from wechatauto.db import WeChatDB

        db = WeChatDB()
        name = (db.get_nickname(OWNER) or "").strip()
        if not name:
            log("拿不到主人备注名，放弃")
            return False
        line = random.choice(LINES)
        wxui.send_text(name, line)
        DONE.write_text(date.today().isoformat(), encoding="utf-8")
        log(f"已发早安 → {name}：{line}")
        return True
    except Exception as exc:
        log(f"发送失败：{type(exc).__name__}: {exc}")
        return False


def main() -> int:
    log("早安进程启动")
    while True:
        now = datetime.now()
        due = now.replace(hour=HOUR, minute=MINUTE, second=0, microsecond=0)
        if now < due:
            log(f"等 {due:%Y-%m-%d %H:%M}（还有 {(due-now).total_seconds()/60:.0f} 分钟）")
            while datetime.now() < due:
                beat()
                time.sleep(30)
            continue
        # 已经过点
        beat()
        late = (now - due).total_seconds() / 3600
        if not sent_today() and late <= CATCH_UP_HOURS:
            for i in range(MAX_TRIES):
                if send_once():
                    break
                time.sleep(60)
        elif not sent_today():
            log("过点太久，今天不发了")
        # 睡到明天 9:20 之前（分段检查，跨睡眠也能醒）
        tomorrow = (now + timedelta(days=1)).replace(hour=HOUR, minute=MINUTE, second=0, microsecond=0)
        while datetime.now() < tomorrow:
            beat()
            time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
