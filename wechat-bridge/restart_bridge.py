"""静默重启桥：pythonw 直跑，全程无控制台窗口、无 UAC。

用法：pythonw restart_bridge.py [delay_sec]
流程：等 delay 秒（让桥把回复发完）→ 按 bridge.pid 杀掉旧桥（纯 ctypes，不调 cmd/powershell）
      → 用 pythonw 无窗口拉起 wechat_bridge.py → 12 秒后确认存活并排一条 outbox 消息。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
LOG = HERE / "restart.log"
PIDF = HERE / "bridge.pid"
CONSOLE = HERE / "console.log"
CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008
NEW_GROUP = 0x00000200
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
k32.OpenProcess.restype = ctypes.c_void_p
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                           ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]


def note(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def image_of(pid: int) -> str:
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def kill_pid(pid: int) -> bool:
    h = k32.OpenProcess(PROCESS_TERMINATE, 0, pid)
    if not h:
        note(f"kill pid={pid} open failed err={ctypes.get_last_error()}")
        return False
    try:
        return bool(k32.TerminateProcess(h, 1))
    finally:
        k32.CloseHandle(h)


def read_pid() -> int:
    try:
        return int((PIDF.read_text(encoding="ascii").strip() or "0"))
    except Exception:
        return 0


def bridge_alive() -> bool:
    pid = read_pid()
    if pid <= 0:
        return False
    img = image_of(pid)
    return bool(img) and img.lower().endswith(("python.exe", "pythonw.exe"))


def queue_note(text: str, to: str = "文件传输助手") -> None:
    import json

    # 重启不该往群里报备（旧版每次重启都往真群发一条，还因 .ps1 被按 GBK 解析变成乱码）。
    # 现在默认不发，需要时显式 BRIDGE_RESTART_NOTE=1。
    if os.environ.get("BRIDGE_RESTART_NOTE", "0") != "1":
        return
    try:
        with open(HERE / "outbox.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"to": to, "text": text}, ensure_ascii=False) + "\n")
        note("outbox queued")
    except Exception as exc:
        note(f"outbox queue failed: {type(exc).__name__}: {exc}")


def relaunch() -> int:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    env = dict(os.environ)
    env["STICKER_ENABLED"] = "1"
    with open(CONSOLE, "ab") as f:
        f.write(f"\n==== restart_bridge {time.strftime('%Y-%m-%d %H:%M:%S')} relaunch ====\n".encode("utf-8"))
        f.flush()
        p = subprocess.Popen([str(pyw), str(HERE / "wechat_bridge.py")], cwd=str(WS), env=env,
                             stdin=subprocess.DEVNULL, stdout=f, stderr=f,
                             creationflags=CREATE_NO_WINDOW | DETACHED | NEW_GROUP, close_fds=True)
    return p.pid


def main() -> int:
    delay = 8
    if len(sys.argv) > 1:
        try:
            delay = max(0, int(sys.argv[1]))
        except Exception:
            pass
    note(f"restart start delay={delay}s")
    time.sleep(delay)
    pid = read_pid()
    killed = 0
    if pid > 0 and image_of(pid).lower().endswith(("python.exe", "pythonw.exe")):
        if kill_pid(pid):
            killed += 1
            note(f"kill pid={pid} ok")
        else:
            note(f"kill pid={pid} failed")
    else:
        note(f"pid={pid} not a live python, skip kill")
    time.sleep(2.0)
    new_pid = relaunch()
    note(f"relaunch spawned pid={new_pid}")
    time.sleep(12.0)
    ok = bridge_alive()
    note(f"bridge_alive={ok} pidfile={read_pid()}")
    if ok and killed > 0:
        queue_note("重启完了，这回应该正常了")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
