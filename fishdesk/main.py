"""小肥鱼控制台入口。开发态：python main.py；打包后：XiaoFeiYuConsole.exe。"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox

from app import bootstrap, paths
from app.config_store import ConfigStore
from app.hooks import HookBus


def _boot_log(tag: str) -> None:
    """硬编码启动日志：双击失败也查得出卡在哪一步（写 exe 同目录，失败退到 %LOCALAPPDATA%）。"""
    try:
        import datetime
        import os
        import tempfile
        from pathlib import Path

        frozen = getattr(sys, "frozen", False)
        base = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent
        cand = [base / "app-launch.log"]
        local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "XiaoFeiYuConsole"
        cand.append(local / "app-launch.log")
        line = (f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} pid={os.getpid()} tag={tag} "
                f"frozen={frozen} cwd={os.getcwd()} exe={sys.executable} "
                f"elevated={bootstrap.is_admin()} argv={sys.argv[1:]} temp={os.environ.get('TEMP')}")
        for p in cand:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                return
            except Exception:
                continue
    except Exception:
        pass


def _fatal(msg: str) -> int:
    _boot_log("fatal " + msg.splitlines()[0][:120])
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(paths.APP_TITLE, msg)
    root.destroy()
    return 1


def main() -> int:
    _boot_log("start")
    try:
        return _run()
    except BaseException:
        import traceback

        _boot_log("crash " + traceback.format_exc().replace("\n", " | ")[:900])
        raise


def _run() -> int:
    bootstrap.enable_dpi_awareness()
    if bootstrap.maybe_elevate(attempted="--elevated" in sys.argv):
        _boot_log("elevate-handoff")
        return 0
    if not bootstrap.acquire_single_instance():
        _boot_log("single-instance: already running")
        return 0

    ws = paths.resolve_workspace(paths.load_settings())
    if not paths.is_workspace(ws):
        return _fatal(f"找不到工作区：{ws}\n\n需要包含 wechat-bridge/wechat_bridge.py 的目录。\n"
                      f"可用环境变量 DSH_WORKSPACE 指定。")

    _boot_log(f"workspace={ws}")
    store = ConfigStore(paths.config_path(ws))
    store.ensure()
    store.load()
    hooks = HookBus()

    from app.ui.window import MainWindow
    win = MainWindow(ws, store, hooks)
    _boot_log("window-ready")
    win.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
