"""常驻轻量看护：桥没了就隐藏拉起（pythonw.exe 运行，没有控制台窗口）。

这轮把「每 2 分钟起一个 powershell 隐藏窗」换成一个常驻进程：
  * FAST=60s 只看 bridge.pid 的进程还在不在（几乎零开销，等于「进程退出事件」触发）；
  * FULL=15 分钟做一次轻量健康检查（控 token/开销，进程退出仍由 FAST=60s 立刻发现）：微信窗口能不能找到、最近 30 分钟有没有 confirmed，
    只写日志，绝不真发消息、不动窗口层级；
  * 发现退出立刻用 wscript 隐藏拉起（同一入口，不会弹窗）；
  * 1 小时最多拉 MAX_HOUR=5 次，防止死循环刷进程。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAST = 60
FULL = 15 * 60
MAX_HOUR = 5
PIDFILE = HERE / "bridge.pid"
DELIVERIES = HERE / "text-deliveries.jsonl"
LOGFILE = HERE / "watchdog.log"

log = logging.getLogger("watchdog")
log.setLevel(logging.INFO)
_h = RotatingFileHandler(LOGFILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.GetExitCodeProcess.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
STILL_ACTIVE = 259


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wt.DWORD(0)
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def bridge_pid() -> int:
    try:
        return int((PIDFILE.read_text(encoding="utf-8").strip() or "0").split()[0])
    except Exception:
        return 0


def launch() -> None:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    subprocess.Popen(["wscript.exe", str(HERE / "start-bridge-hidden.vbs")],
                     cwd=str(HERE), creationflags=0x08000000, startupinfo=si)
    log.warning("已隐藏拉起桥（wscript → start-bridge-hidden.vbs）")


def window_ok() -> str:
    try:
        from win_shot import find_wechat_window
        hwnd = find_wechat_window()
        return f"微信窗口 ok（hwnd={hwnd}）" if hwnd else "微信窗口找不到"
    except Exception as exc:
        return f"窗口检查失败：{type(exc).__name__}"


def last_confirmed() -> str:
    try:
        raw = DELIVERIES.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        for line in reversed(raw):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "confirmed":
                ts = rec.get("at") or rec.get("ts") or rec.get("time") or 0
                stamp = None
                try:
                    stamp = float(ts)
                except (TypeError, ValueError):
                    try:
                        stamp = time.mktime(time.strptime(str(ts), "%Y-%m-%d %H:%M:%S"))
                    except (TypeError, ValueError, OverflowError):
                        stamp = None
                if stamp is None:
                    return "最近 confirmed（时间字段异常：%s）" % (list(rec)[:6],)
                age = max(0.0, time.time() - stamp)
                return f"最近 confirmed {age/60:.0f} 分钟前（{str(rec.get('text'))[:20]}）"
        return "最近没有 confirmed"
    except Exception as exc:
        return f"投递记录读取失败：{type(exc).__name__}"


def main() -> None:
    log.info("watchdog 启动：FAST=%ss FULL=%ss pid=%s", FAST, FULL, int(bridge_pid()))
    launches: list[float] = []
    was_alive = False
    nxt_full = 0.0
    while True:
        now = time.time()
        pid = bridge_pid()
        alive = pid_alive(pid)
        if alive and not was_alive:
            log.info("桥在跑（pid=%s）", pid)
        if not alive and (was_alive or now >= nxt_full):
            recent = [t for t in launches if now - t < 3600]
            if len(recent) >= MAX_HOUR:
                log.error("1 小时内已拉起 %d 次，不再自动拉，等人工处理（pid=%s）", len(recent), pid)
            else:
                log.warning("桥不在了（pid=%s，曾活着=%s），隐藏拉起", pid, was_alive)
                try:
                    launch()
                    launches.append(now)
                except Exception as exc:
                    log.error("拉起失败：%s: %s", type(exc).__name__, exc)
                nxt_full = now + 180   # 给桥 3 分钟写 pid，期间不重复判死
        was_alive = alive
        if now >= nxt_full:
            log.info("巡检：%s；%s", window_ok(), last_confirmed())
            nxt_full = now + FULL
        time.sleep(FAST)


if __name__ == "__main__":
    main()
