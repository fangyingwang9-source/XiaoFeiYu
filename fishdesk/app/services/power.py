"""电源脚本服务：立即休眠 / 计划任务同步 / 状态。

只调用现成的 power/*.ps1，不改睡眠逻辑本身。
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from .elevate import is_admin, run_script_elevated

NO_WINDOW = 0x08000000
DETACHED = 0x00000008

_STARTUPINFO = subprocess.STARTUPINFO()
_STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
_STARTUPINFO.wShowWindow = 0  # SW_HIDE


def _ps_exe() -> str:
    for name in ("powershell.exe", "pwsh.exe"):
        try:
            subprocess.run([name, "-NoProfile", "-Command", "exit 0"], capture_output=True,
                           timeout=8, creationflags=NO_WINDOW)
            return name
        except Exception:
            continue
    return "powershell.exe"


class PowerService:
    def __init__(self, ws: Path):
        self.ws = Path(ws)
        self.dir = self.ws / "wechat-bridge" / "power"
        self.ps = _ps_exe()

    # ---------- 基础 ----------
    def run(self, script: str, args: list[str], timeout: int = 90, hidden: bool = True) -> tuple[int, str]:
        path = self.dir / script
        if not path.is_file():
            return 1, f"脚本不存在：{path}"
        cmd = [self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path), *args]
        flags = NO_WINDOW if hidden else 0
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags,
                               encoding="utf-8", errors="replace")
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return 1, f"脚本超时（{timeout}s）：{script}"
        except Exception as exc:
            return 1, f"{type(exc).__name__}: {exc}"

    # ---------- 动作 ----------
    def sleep_now(self, reason: str = "ui", delay: int = 3, hibernate: bool = False) -> tuple[bool, str]:
        path = self.dir / "power.ps1"
        if not path.is_file():
            return False, f"脚本不存在：{path}"
        action = "hibernate" if hibernate else "sleep"
        # 只有在真需要管理员时才弹 UAC（默认 asInvoker 启动，见 fishdesk.spec）
        if not is_admin():
            ok, msg = run_script_elevated(
                path, ["-Action", action, "-Reason", reason, "-DelaySec", str(delay)], self.dir)
            if ok:
                return True, f"已弹管理员授权，授权后 {delay} 秒进入休眠"
            if "已取消" not in msg:
                # 用户拒绝授权就退回到免提权调用（SetSuspendState 通常不需要管理员）
                pass
            else:
                return False, msg
        cmd = [self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path),
               "-Action", action, "-Reason", reason, "-DelaySec", str(delay)]
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=NO_WINDOW | DETACHED,
                             startupinfo=_STARTUPINFO, close_fds=True)
            return True, f"已发出休眠指令（{delay} 秒后执行）"
        except Exception as exc:
            return False, f"发射失败：{type(exc).__name__}: {exc}"

    def apply_schedule(self) -> tuple[bool, str]:
        """按 config.json 的 wake_slots 重注册计划任务（写计划任务必须要管理员，这里才提权）。"""
        if not is_admin():
            ok, msg = run_script_elevated(self.dir / "install.ps1", [], self.dir)
            if not ok:
                return False, msg
            return True, "已弹管理员授权：请在 UAC 窗口点「是」，完成后再点「刷新状态」"
        rc, out = self.run("install.ps1", [], timeout=120)
        ok = rc == 0
        msg = out.strip().splitlines()[-6:]
        return ok, ("应用成功：\n" + "\n".join(msg) if ok else "应用失败：\n" + "\n".join(msg))

    def status(self) -> dict:
        info = {"last_sleep": "", "last_wake": "", "watcher_pid": 0, "watcher_alive": False}
        for key, name in (("last_sleep", "last_sleep.txt"), ("last_wake", "last_wake.txt")):
            p = self.dir / name
            try:
                info[key] = p.read_text(encoding="utf-8-sig").strip()[:40]
            except Exception:
                pass
        try:
            pid = int((self.dir / "idle-watcher.pid").read_text(encoding="utf-8-sig").strip())
            info["watcher_pid"] = pid
            try:
                import psutil
                info["watcher_alive"] = psutil.pid_exists(pid)
            except Exception:
                info["watcher_alive"] = False
        except Exception:
            pass
        return info

    def stop_watcher(self) -> str:
        pid = self.status().get("watcher_pid") or 0
        if not pid:
            return "没有看门狗在跑"
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                           timeout=8, creationflags=NO_WINDOW)
            return f"已结束看门狗 PID {pid}"
        except Exception as exc:
            return f"结束失败：{exc}"

    def next_wake_text(self, slots: list[str]) -> str:
        if not slots:
            return "未设置"
        now = datetime.now()
        today = now.date()
        points = []
        for s in slots:
            try:
                hh, mm = s.split(":")
                t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                points.append(t)
            except Exception:
                continue
        future = [t for t in points if t > now]
        nxt = min(future) if future else min(points) + timedelta(days=1)
        return nxt.strftime("%m-%d %H:%M")
