# -*- coding: utf-8 -*-
"""窗口级截图（PrintWindow + PW_RENDERFULLCONTENT）。

比屏幕抓图可靠：微信被遮挡、窗口未置前、甚至显示器不刷新时仍能拿到真实内容；
也避免「把面板截进去」和抢焦点的问题。
"""
import ctypes
from ctypes import wintypes
import numpy as np
import os

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

# 句柄必须按 64 位声明，否则 GetWindowDC 的返回值被截成 c_int，回传时
# 抛 OverflowError: int too long to convert（踩过一次，表现是"截图基准失败"）。
HDC = ctypes.c_void_p
HBITMAP = ctypes.c_void_p
u32.GetWindowDC.argtypes = [wintypes.HWND]
u32.GetWindowDC.restype = HDC
u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.GetClientRect.restype = wintypes.BOOL
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.GetWindowRect.restype = wintypes.BOOL
u32.PrintWindow.argtypes = [wintypes.HWND, HDC, wintypes.UINT]
u32.PrintWindow.restype = wintypes.BOOL
u32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
u32.ReleaseDC.restype = ctypes.c_int
u32.IsIconic.argtypes = [wintypes.HWND]
u32.IsIconic.restype = wintypes.BOOL
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.IsWindowVisible.restype = wintypes.BOOL
u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
u32.EnumWindows.restype = wintypes.BOOL
g32.CreateCompatibleDC.argtypes = [HDC]
g32.CreateCompatibleDC.restype = HDC
g32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
g32.CreateCompatibleBitmap.restype = HBITMAP
g32.SelectObject.argtypes = [HDC, HBITMAP]
g32.SelectObject.restype = HBITMAP
g32.DeleteObject.argtypes = [HBITMAP]
g32.DeleteObject.restype = wintypes.BOOL
g32.DeleteDC.argtypes = [HDC]
g32.DeleteDC.restype = wintypes.BOOL
g32.GetDIBits.argtypes = [HDC, HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
g32.GetDIBits.restype = ctypes.c_int


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT_(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT), ("rcNormalPosition", RECT_)]


u32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
u32.GetWindowPlacement.restype = wintypes.BOOL


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def find_wechat_window():
    """返回 (hwnd, (l, t, r, b))；找不到抛 RuntimeError。"""
    hits = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        b = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, b, 256)
        if b.value != "Qt51514QWindowIcon":
            return True
        # 最小化（IsIconic）时 IsWindowVisible 可能为 0、GetWindowRect 是 -32000 的假坐标，
        # 这里用 GetWindowPlacement 的正常位置，否则"微信收进了任务栏"会被误判成窗口不存在。
        if not (u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd)):
            return True
        rect = wintypes.RECT()
        if u32.IsIconic(hwnd):
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(WINDOWPLACEMENT)
            if u32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
                n = wp.rcNormalPosition
                rect = wintypes.RECT(n.left, n.top, n.right, n.bottom)
        if rect.right - rect.left <= 0:
            u32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left > 400 and rect.bottom - rect.top > 400:
            hits.append((hwnd, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    u32.EnumWindows(CB(cb), 0)
    if not hits:
        raise RuntimeError("找不到微信主窗口")
    hits.sort(key=lambda h: (h[1][2] - h[1][0]) * (h[1][3] - h[1][1]), reverse=True)
    return hits[0]


def grab(hwnd=None, rect=None):
    """抓窗口位图，返回 HxWx3 的 int16 numpy 数组（RGB）。"""
    if hwnd is None or rect is None:
        hwnd, rect = find_wechat_window()
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    cr = wintypes.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(cr))
    w, h = max(w, cr.right), max(h, cr.bottom)
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    return np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].astype(np.int16)


def save_png(arr, path):
    from PIL import Image
    Image.fromarray(arr.astype(np.uint8)).save(path)
    return os.path.abspath(path)


def new_block(before, after, right_frac=0.45, px=800):
    """比对两张同尺寸窗口图，右侧是否出现新内容块（= 自己这边多了条消息/表情）。"""
    h = min(before.shape[0], after.shape[0])
    w = min(before.shape[1], after.shape[1])
    b = before[:h, :w]
    a = after[:h, :w]
    x0 = int(w * right_frac)
    band = slice(40, max(41, h - 150))
    d = np.abs(a[band, x0:w] - b[band, x0:w]).sum(axis=2)
    ys, xs = np.where(d > 30)
    if len(xs) < px:
        return False, {"changed": int(len(xs))}
    bw = int(xs.max() - xs.min())
    bh = int(ys.max() - ys.min())
    ok = bw > 40 and bh > 25
    return ok, {"changed": int(len(xs)), "w": bw, "h": bh,
                "box": (int(xs.min()) + x0, int(ys.min()) + 40, int(xs.max()) + x0, int(ys.max()) + 40)}


if __name__ == "__main__":
    hwnd, rect = find_wechat_window()
    arr = grab(hwnd, rect)
    print("hwnd", hwnd, "rect", rect, "shape", arr.shape, "mean", round(float(arr.mean()), 1))
