"""小肥鱼控制台入口。开发态：python main.py；打包后：XiaoFeiYuConsole.exe。"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox

from app import bootstrap, paths
from app.config_store import ConfigStore
from app.hooks import HookBus


def _fatal(msg: str) -> int:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(paths.APP_TITLE, msg)
    root.destroy()
    return 1


def main() -> int:
    bootstrap.enable_dpi_awareness()
    if bootstrap.maybe_elevate(attempted="--elevated" in sys.argv):
        return 0
    if not bootstrap.acquire_single_instance():
        return 0

    ws = paths.resolve_workspace(paths.load_settings())
    if not paths.is_workspace(ws):
        return _fatal(f"找不到工作区：{ws}\n\n需要包含 wechat-bridge/wechat_bridge.py 的目录。\n"
                      f"可用环境变量 DSH_WORKSPACE 指定。")

    store = ConfigStore(paths.config_path(ws))
    store.ensure()
    store.load()
    hooks = HookBus()

    from app.ui.window import MainWindow
    win = MainWindow(ws, store, hooks)
    win.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
