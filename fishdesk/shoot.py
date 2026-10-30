"""渲染窗口并自截屏到 screenshots/，用于交付截图。"""
from __future__ import annotations

import os
import sys
import time

from PIL import ImageGrab

sys.argv = [a for a in sys.argv if a != "--elevated"]
os.environ["FISHDESK_NO_ELEVATE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import bootstrap, paths  # noqa: E402
from app.config_store import ConfigStore  # noqa: E402
from app.hooks import HookBus  # noqa: E402

bootstrap.enable_dpi_awareness()
ws = paths.resolve_workspace(paths.load_settings())
store = ConfigStore(paths.config_path(ws))
store.ensure()
store.load()

from app.ui.window import MainWindow  # noqa: E402

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(out, exist_ok=True)

win = MainWindow(ws, store, HookBus())
win.attributes("-topmost", True)
win.update()

for spec in win.specs:
    win.show(spec.key)
    win.update()
    time.sleep(0.6)
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    name = f"{spec.key}.png"
    img.save(os.path.join(out, name))
    print("saved", name, img.size)

win.destroy()
print("done")
