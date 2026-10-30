"""按需提权：默认 asInvoker 启动，只有写计划任务/休眠这类真需要管理员的操作才弹 UAC。"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ERROR_CANCELLED = 1223


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_elevated(exe: str, params: list[str] | str, cwd: str | None = None,
                 wait: bool = False) -> tuple[bool, str]:
    """ShellExecuteW(runas) 拉起一个提权进程。用户点「否」返回 (False, 已取消)。"""
    p = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in params) if isinstance(params, list) else str(params)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(exe), p, str(cwd) if cwd else None, 1 if wait else 0)
    except Exception as exc:  # noqa: BLE001
        return False, f"提权失败：{type(exc).__name__}: {exc}"
    if rc <= 32:
        if rc == ERROR_CANCELLED or rc == 5:
            return False, "已取消管理员授权"
        return False, f"提权失败（ShellExecuteW={rc}）"
    return True, "已弹管理员授权"


def run_script_elevated(script: Path, args: list[str], cwd: Path, wait: bool = False) -> tuple[bool, str]:
    """用提权 powershell 跑一个 .ps1（脚本本身不需要签名）。"""
    exe = Path(sys.executable)
    ps = "powershell.exe"
    params = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
    if exe.name.lower().startswith("python"):
        # 提权后仍用同一套环境更稳：让 powershell 直接跑脚本即可
        pass
    return run_elevated(ps, params, str(cwd), wait=wait)
