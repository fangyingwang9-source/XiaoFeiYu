# -*- coding: utf-8 -*-
"""启动冻结包，按标题等窗口（不依赖父进程 pid），截窗口区域后关闭。"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

exe, out = sys.argv[1], sys.argv[2]
key = sys.argv[3] if len(sys.argv) > 3 else "小肥鱼"
env = dict(os.environ, FISHDESK_NO_ELEVATE="1")
proc = subprocess.Popen([exe], env=env)

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
found = []


def cb(hwnd, _):
    if not user32.IsWindowVisible(hwnd):
        return True
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return True
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    if key in buf.value and cls.value == "TkTopLevel":
        found.append(hwnd)
    return True


for _ in range(120):
    user32.EnumWindows(EnumProc(cb), 0)
    if found:
        break
    time.sleep(0.5)

if not found:
    print("NO_WINDOW pid", proc.pid, "alive", proc.poll() is None)
    proc.kill()
    sys.exit(2)

hwnd = found[0]
user32.ShowWindow(hwnd, 9)
user32.SetForegroundWindow(hwnd)
time.sleep(3)
rect = wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(out)
print("SHOT_OK", (rect.right - rect.left, rect.bottom - rect.top), out)
proc.kill()
