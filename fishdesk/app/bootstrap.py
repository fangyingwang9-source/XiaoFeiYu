"""启动前处理：单实例锁、管理员自提权、DPI 感知。"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys

from . import paths

MUTEX_NAME = "Global\\XiaoFeiYuConsole_9F3A1"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def acquire_single_instance() -> bool:
    """同名互斥体；已有实例时抢焦点并返回 False。"""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _focus_existing()
            return False
        return True
    except Exception:
        # 退化方案：本地端口占位
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", 47731))
            s.listen(1)
            globals()["_SOCK"] = s
            return True
        except OSError:
            return False


def _focus_existing() -> None:
    try:
        u32 = ctypes.windll.user32
        hwnd = u32.FindWindowW(None, paths.APP_TITLE)
        if hwnd:
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def maybe_elevate(attempted: bool = False) -> bool:
    """默认不自动提权（asInvoker）：只有显式设置 FISHDESK_ELEVATE=1 的开发态才自提权。"""
    if is_admin() or paths.is_frozen() or attempted:
        return False
    if os.environ.get("FISHDESK_ELEVATE") != "1":
        return False
    try:
        args = list(sys.argv[1:])
        if "--elevated" not in args:
            args.append("--elevated")
        params = subprocess.list2cmdline(args)
        target = sys.executable
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, str(paths.app_dir()), 1)
        return rc > 32
    except Exception:
        return False
