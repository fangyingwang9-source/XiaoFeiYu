"""无窗口冒烟测试：建窗口 -> 逐页渲染 -> 打印结果 -> 销毁（不进入 mainloop）。"""
from __future__ import annotations

import sys
import traceback

sys.argv = [a for a in sys.argv if a != "--elevated"]

from app import bootstrap, paths  # noqa: E402
from app.config_store import ConfigStore  # noqa: E402
from app.hooks import HookBus  # noqa: E402

bootstrap.enable_dpi_awareness()
ws = paths.resolve_workspace(paths.load_settings())
print("workspace:", ws, "valid:", paths.is_workspace(ws))
store = ConfigStore(paths.config_path(ws))
store.ensure()
store.load()
print("config keys:", sorted(store.data.keys()))

from app.ui.window import MainWindow  # noqa: E402

win = MainWindow(ws, store, HookBus())
win.attributes("-alpha", 0.0)
win.update_idletasks()
fails = []
for spec in win.specs:
    try:
        win.show(spec.key)
        win.update()
        frame_cls = type(win.pages.get(spec.key) or win._current).__name__ if hasattr(win, "pages") else "?"
        print(f"page {spec.key:10s} {spec.title:6s} -> {frame_cls}")
    except Exception:
        fails.append(spec.key)
        print(f"page {spec.key} FAILED")
        traceback.print_exc()
win.update_idletasks()
win.destroy()
print("FAILS:", fails or "none")
