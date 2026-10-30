"""微信桥进程控制：起停、状态、日志尾巴。

只做「看一眼 / 拉一下 / 关掉」，不重写桥的日志与自愈逻辑；
桥自己的 bridge.pid 单实例锁保持原样，重复启动会自行退出。
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from pathlib import Path

try:
    import psutil
except Exception:  # pragma: no cover - psutil 缺失时降级
    psutil = None  # type: ignore

DETACHED = 0x00000008
NEW_GROUP = 0x00000200
NO_WINDOW = 0x08000000

# 子进程一律隐藏窗口：CREATE_NO_WINDOW 管控制台，STARTF_USESHOWWINDOW+SW_HIDE 兜住 GUI 子进程
_STARTUPINFO = subprocess.STARTUPINFO()
_STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
_STARTUPINFO.wShowWindow = 0  # SW_HIDE


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil:
        try:
            return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


_WX_CACHE = {"at": 0.0, "ok": False}


def _wechat_window() -> int:
    """枚举顶层窗口，找微信主窗口（4.x 类名 Qt5xxxxQWindowIcon，3.x WeChatMainWndForPC）。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        c = cls.value or ""
        if c == "WeChatMainWndForPC":
            found.append(hwnd)
            return False
        if c.startswith("Qt5") and c.endswith("QWindowIcon"):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                t = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, t, n + 1)
                if "微信" in (t.value or "") or "WeChat" in (t.value or ""):
                    found.append(hwnd)
                    return False
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:
        return 0
    return found[0] if found else 0


class BridgeService:
    def __init__(self, ws: Path):
        self.ws = Path(ws)
        self.dir = self.ws / "wechat-bridge"
        self.script = self.dir / "wechat_bridge.py"
        self.pid_file = self.dir / "bridge.pid"
        self.log_file = self.dir / "wechat-bridge.log"
        self.console_log = self.dir / "console.log"

    # ---------- 状态 ----------
    def read_pid(self) -> int:
        try:
            return int(self.pid_file.read_text(encoding="utf-8-sig").strip().split()[0])
        except Exception:
            return 0

    def is_running(self) -> bool:
        return _pid_alive(self.read_pid())

    def wechat_running(self) -> bool:
        """微信在线判定：4.x 主窗口（Qt 窗口类 + 标题「微信」）或进程存在。

        4.x 的进程叫 Weixin.exe，窗口类 Qt51514QWindowIcon，3.x 才是 WeChat.exe /
        WeChatMainWndForPC，所以老版本只看 WeChat.exe 会永远显示离线。
        """
        now = time.time()
        if now - _WX_CACHE["at"] < 1.0:
            return bool(_WX_CACHE["ok"])
        ok = bool(_wechat_window()) or self._wechat_process()
        _WX_CACHE["at"], _WX_CACHE["ok"] = now, ok
        return ok

    def wechat_window(self) -> int:
        return _wechat_window()

    def _wechat_process(self) -> bool:
        try:
            import psutil as _ps
        except Exception:
            _ps = psutil
        if not _ps:
            return False
        try:
            for p in _ps.process_iter(["name"]):
                if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe"):
                    return True
        except Exception:
            pass
        return False

    def dsh_running(self) -> bool:
        if not psutil:
            return self._tasklist("DeepSeek Harness.exe")
        try:
            for p in psutil.process_iter(["name"]):
                n = (p.info.get("name") or "").lower()
                if "deepseek harness" in n or "deepseek-harness" in n:
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _tasklist(exe: str) -> bool:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                capture_output=True, text=True, timeout=8,
                creationflags=NO_WINDOW,
            ).stdout
            return exe.lower() in out.lower()
        except Exception:
            return False

    def last_message_ts(self) -> float:
        try:
            return (self.dir / "last_msg_ts.txt").stat().st_mtime
        except Exception:
            return 0.0

    def status(self) -> dict:
        pid = self.read_pid()
        return {
            "bridge": _pid_alive(pid),
            "bridge_pid": pid if _pid_alive(pid) else 0,
            "wechat": self.wechat_running(),
            "dsh": self.dsh_running(),
            "last_msg": self.last_message_ts(),
        }

    # ---------- 起停 ----------
    def resolve_python(self, configured: str = "") -> str:
        cands = [configured.strip(), os.environ.get("BRIDGE_PYTHON", "")]
        cands += [
            r"C:\Users\one\.dsh\wxauto\venv\Scripts\python.exe",
            str(Path.home() / ".dsh" / "wxauto" / "venv" / "Scripts" / "python.exe"),
        ]
        for c in cands:
            if c and Path(c).is_file():
                return c
        return ""

    def start(self, python_exe: str = "", sticker_enabled: bool = True, dry_run: bool = False) -> tuple[bool, str]:
        if self.is_running():
            return False, f"桥已在运行（PID {self.read_pid()}），无需重复启动"
        py = self.resolve_python(python_exe)
        if not py:
            return False, "找不到 Python 解释器：请在「高级设置」里填写 wxauto venv 的 python.exe"
        if not self.script.is_file():
            return False, f"找不到桥脚本：{self.script}"
        env = dict(os.environ)
        env["STICKER_ENABLED"] = "1" if sticker_enabled else "0"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        cmd = [py, "-u", str(self.script)]
        if dry_run:
            cmd.append("--dry-run")
        try:
            log = open(self.console_log, "ab")
        except Exception:
            log = subprocess.DEVNULL
        try:
            subprocess.Popen(
                cmd,
                cwd=str(self.ws),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=DETACHED | NEW_GROUP | NO_WINDOW,
                startupinfo=_STARTUPINFO,
                close_fds=True,
            )
        except Exception as exc:
            return False, f"启动失败：{type(exc).__name__}: {exc}"
        for _ in range(30):
            time.sleep(0.2)
            if self.is_running():
                return True, f"桥已启动（PID {self.read_pid()}）"
        return False, "启动命令已发出，但 6 秒内没看到 bridge.pid（看 console.log 排错）"

    def stop(self, force: bool = False) -> tuple[bool, str]:
        pid = self.read_pid()
        if not _pid_alive(pid):
            return True, "桥当前没在运行"
        if not psutil:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                               timeout=8, creationflags=NO_WINDOW)
                return True, f"已强制结束 PID {pid}"
            except Exception as exc:
                return False, f"结束失败：{exc}"
        try:
            proc = psutil.Process(pid)
            kids = proc.children(recursive=True)
            for p in reversed(kids + [proc]):
                try:
                    p.terminate()
                except Exception:
                    pass
            gone, alive = psutil.wait_procs(kids + [proc], timeout=6)
            if alive and force:
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
            return True, f"已停止桥（PID {pid}）"
        except Exception as exc:
            return False, f"停止失败：{type(exc).__name__}: {exc}"

    def tail(self, lines: int = 60) -> list[str]:
        for path in (self.log_file, self.console_log):
            if not path.is_file():
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 64000))
                    raw = f.read().decode("utf-8", "replace")
                rows = [r for r in raw.splitlines() if r.strip()]
                # 脱敏：界面/录屏里不出现真实 wxid 与群号
                rows = [re.sub(r"\b\d{6,}@chatroom\b", "***@chatroom",
                               re.sub(r"wxid_[0-9A-Za-z]{4,}", "wxid_***", r)) for r in rows]
                return rows[-lines:]
            except Exception:
                continue
        return []
