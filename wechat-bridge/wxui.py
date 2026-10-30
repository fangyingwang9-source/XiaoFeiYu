"""微信 4.x 极简界面驱动（替代 wechatauto 的 UIA 发送路径）。

设计红线（这三条是硬约束，别再退回去）：
  1. 绝不最小化 / 移动 / 还原任何第三方窗口。拉不起微信就抛 WxUiError 等下一轮。
  2. 找群不用鼠标点搜索框：Ctrl+F 唤醒搜索 → 剪贴板粘贴群名 → Enter。
  3. 每次操作前先 health_check()：句柄在不在、有没有无响应、是否前台；
     不在前台的只允许用 API 拉起来，等 1 秒确认渲染；连拉 3 次失败就抛错挂起。

发送同样只走键盘 + 剪贴板（和人手动复制粘贴完全一致）：
  文字：Ctrl+A / Delete 清空输入框 → Ctrl+V 粘贴 → Enter。
  图片：剪贴板塞 CF_HDROP 文件列表 → Ctrl+V → 等缩略图 → Enter。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import time
from pathlib import Path
from typing import Optional

from win_shot import find_wechat_window

log = logging.getLogger("wxui")

# 同一条回复的第 2 条气泡起，界面还是刚才那个会话，没必要再 Ctrl+F 搜一遍（省 ~2.4s）
# 只给「自己刚打开、紧接着还要发」的场景用（do_reply 显式传 reuse=True）；
# 手动发/切会话一律走完整流程，避免把字打进别人窗口。WX_OPEN_REUSE=0 彻底关掉。
OPEN_REUSE_SECONDS = float(os.environ.get("WX_OPEN_REUSE", "20"))
_LAST_OPEN = {"name": "", "at": 0.0}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_HWND = wt.HWND
for _name, _args in (
        ("IsWindow", [_HWND]), ("IsWindowVisible", [_HWND]), ("IsIconic", [_HWND]),
        ("IsHungAppWindow", [_HWND]), ("ShowWindow", [_HWND, ctypes.c_int]),
        ("BringWindowToTop", [_HWND]), ("SetForegroundWindow", [_HWND]),
        ("SetActiveWindow", [_HWND]), ("SetFocus", [_HWND]),
        ("SetWindowPos", [_HWND, _HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_uint]),
        ("GetWindowThreadProcessId", [_HWND, ctypes.c_void_p]),
        ("AttachThreadInput", [wt.DWORD, wt.DWORD, wt.BOOL]),
        ("GetWindowTextW", [_HWND, ctypes.c_wchar_p, ctypes.c_int]),
        ("OpenClipboard", [_HWND]), ("EmptyClipboard", []),
        ("SetClipboardData", [ctypes.c_uint, ctypes.c_void_p]), ("CloseClipboard", []),
):
    _fn = getattr(user32, _name)
    _fn.argtypes = _args
    if _name in ("IsWindow", "IsWindowVisible", "IsIconic", "IsHungAppWindow",
                 "BringWindowToTop", "SetForegroundWindow", "SetActiveWindow",
                 "OpenClipboard", "EmptyClipboard", "CloseClipboard"):
        _fn.restype = wt.BOOL
user32.GetForegroundWindow.restype = _HWND
user32.GetForegroundWindow.argtypes = []
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

SW_RESTORE = 9
SW_SHOW = 5
HWND_TOP = 0
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_DELETE = 0x2E
VK_F = 0x46
VK_A = 0x41
VK_V = 0x56
CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002


class WxUiError(RuntimeError):
    """界面层不可用：调用方应记日志、跳过本轮、5 秒后重试，绝不自作主张。"""


# ---------------------------------------------------------------- 键盘
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    # 必须把三种结构都列进来：sizeof(INPUT) 要是 40，只放 KEYBDINPUT 会被
    # SendInput 判成 ERROR_INVALID_PARAMETER(87) 并静默丢键（踩过一次）。
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def _send(inp) -> int:
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _key_down(vk: int) -> None:
    inp = _INPUT(type=1)
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = 0
    if not _send(inp):
        raise WxUiError(f"SendInput 按下 vk=0x{vk:02X} 失败（err={ctypes.get_last_error()}）")


def _key_up(vk: int) -> None:
    inp = _INPUT(type=1)
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = KEYEVENTF_KEYUP
    if not _send(inp):
        raise WxUiError(f"SendInput 松开 vk=0x{vk:02X} 失败（err={ctypes.get_last_error()}）")


def _tap(vk: int, pause: float = 0.03) -> None:
    _key_down(vk)
    time.sleep(0.02)
    _key_up(vk)
    time.sleep(pause)


def _combo(vk: int, pause: float = 0.10) -> None:
    _key_down(VK_CONTROL)
    time.sleep(0.03)
    _tap(vk, 0.02)
    _key_up(VK_CONTROL)
    time.sleep(pause)


# ---------------------------------------------------------------- 剪贴板
def _open_clipboard(retries: int = 12) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.08)
    return False


def _alloc_global(data: bytes):
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return None
    ptr = kernel32.GlobalLock(h)
    if not ptr:
        return None
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(h)
    return h


def set_clip_text(text: str) -> bool:
    data = (text + "\0").encode("utf-16-le")
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        h = _alloc_global(data)
        if not h:
            return False
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


def set_clip_files(paths) -> bool:
    """把文件列表塞进剪贴板（CF_HDROP），微信粘贴后是图片气泡而不是文件卡片。"""
    files = [str(Path(p)) for p in paths]
    if not files:
        return False
    names = ("\0".join(files) + "\0\0").encode("utf-16-le")

    class _DROPFILES(ctypes.Structure):
        _fields_ = [("pFiles", wt.DWORD), ("pt", wt.POINT), ("fNC", wt.BOOL), ("fWide", wt.BOOL)]

    head = _DROPFILES()
    head.pFiles = ctypes.sizeof(_DROPFILES)
    head.fWide = True
    data = bytes(head) + names
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        h = _alloc_global(data)
        if not h:
            return False
        return bool(user32.SetClipboardData(CF_HDROP, h))
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- 剪贴板备份
# 发消息要借剪贴板（Ctrl+V），用完必须把用户原来复制的东西放回去，
# 否则用户复制一段代码/一条链接，桥发完消息就变成我们发的内容，非常烦。
_SKIP_FORMATS = {2, 3, 9, 14}      # CF_BITMAP / CF_METAFILEPICT / CF_PALETTE / CF_ENHMETAFILE（非 HGLOBAL）
_MAX_FORMAT_BYTES = 4 * 1024 * 1024


def snapshot_clipboard():
    """把剪贴板里所有 HGLOBAL 格式原样拷一份；拿不到就返回 None（静默降级）。"""
    if not _open_clipboard():
        return None
    snap = []
    try:
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _SKIP_FORMATS:
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                continue
            try:
                size = int(kernel32.GlobalSize(h))
                if size <= 0 or size > _MAX_FORMAT_BYTES:
                    continue
                snap.append((int(fmt), ctypes.string_at(ptr, size)))
            finally:
                kernel32.GlobalUnlock(h)
    except Exception:  # noqa: BLE001
        log.debug("剪贴板快照失败", exc_info=True)
        return None
    finally:
        user32.CloseClipboard()
    return snap or None


def restore_clipboard(snap) -> bool:
    """把 snapshot_clipboard() 的结果放回剪贴板。失败只记日志，不影响发送结果。"""
    if not snap:
        return False
    if not _open_clipboard():
        log.warning("剪贴板被别的程序占着，没能恢复用户原来的内容")
        return False
    try:
        user32.EmptyClipboard()
        ok = 0
        for fmt, raw in snap:
            h = _alloc_global(raw)
            if h and user32.SetClipboardData(fmt, h):
                ok += 1
        return ok > 0
    except Exception:  # noqa: BLE001
        log.warning("恢复剪贴板失败", exc_info=True)
        return False
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- 窗口
def _win_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _force_foreground(hwnd: int) -> None:
    """只用窗口 API 把微信拉到前台，不动其它窗口的层级。"""
    fg = user32.GetForegroundWindow()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.25)
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.20)
    user32.BringWindowToTop(hwnd)
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    wxp_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = []
    for tid in (fg_tid, wxp_tid):
        if tid and tid != cur_tid and user32.AttachThreadInput(cur_tid, tid, True):
            attached.append(tid)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        for tid in attached:
            user32.AttachThreadInput(cur_tid, tid, False)


def _ensure_fg(hwnd: int, tries: int = 8, gap: float = 0.15) -> None:
    """发送前死磕前台：反复 SetForegroundWindow，拉不上来就抛错，绝不把字打进黑框。"""
    for _ in range(max(1, tries)):
        if user32.GetForegroundWindow() == hwnd:
            return
        _force_foreground(hwnd)
        time.sleep(gap)
    fg = user32.GetForegroundWindow()
    raise WxUiError(
        f"发送前无法把微信置前 hwnd={hwnd}（当前前台 hwnd={fg} title={_win_title(fg)!r}）；"
        "这轮不发送，免得字符打进别的窗口")


def _release_focus(prev: int, hwnd: int) -> None:
    """发完立刻把前台还回去，不长期霸占焦点（PrintWindow 截图不需要前台）。"""
    try:
        if prev and prev != hwnd and user32.IsWindow(prev):
            show = SW_RESTORE if user32.IsIconic(prev) else SW_SHOW
            user32.ShowWindow(prev, show)
            user32.SetForegroundWindow(prev)
    except Exception:
        pass


def _send_guard():
    """记录发送前的前台窗口 / 微信是否最小化 / 剪贴板内容，发送后原样还回去。"""
    st = {"prev": user32.GetForegroundWindow(), "wechat_min": False,
          "hwnd": 0, "clip": snapshot_clipboard()}
    try:
        h, _rect = find_wechat_window()
        st["hwnd"] = int(h)
        st["wechat_min"] = bool(user32.IsIconic(h))
    except Exception:  # noqa: BLE001
        pass
    return st


def _send_restore(st, hwnd: int = 0) -> None:
    """顺序：先还剪贴板，再还前台，最后把本来最小化的微信再最小化回去。"""
    try:
        restore_clipboard(st.get("clip"))
    finally:
        _release_focus(int(st.get("prev") or 0), int(hwnd or st.get("hwnd") or 0))
        h = int(hwnd or st.get("hwnd") or 0)
        try:
            if st.get("wechat_min") and h and user32.IsWindow(h) and not user32.IsIconic(h):
                user32.ShowWindow(h, SW_MINIMIZE)
        except Exception:  # noqa: BLE001
            pass


def health_check(attempts: int = 3, wait: float = 1.0, quiet: bool = False,
                 need_foreground: bool = False) -> int:
    """轮询前的前置检查：窗口存在、没挂起就放行；只有真要打字时（need_foreground）才抢前台。"""
    try:
        hwnd, _rect = find_wechat_window()
    except Exception as exc:  # noqa: BLE001
        raise WxUiError(f"找不到微信主窗口（进程退出或停在登录页）：{exc}") from exc
    if not user32.IsWindow(hwnd):
        raise WxUiError(f"微信窗口句柄 {hwnd} 已失效（窗口被销毁）")
    if user32.IsHungAppWindow(hwnd):
        raise WxUiError(f"微信窗口 {hwnd} 无响应（IsHungAppWindow=1），这轮不碰它")
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        if not quiet:
            log.warning("微信窗口被最小化或隐藏，用 API 还原（不动任何其它窗口）")
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(wait)
    if not need_foreground:
        if not quiet:
            log.debug("微信窗口可用（hwnd=%s，在后台也算可用）", hwnd)
        return hwnd
    for _ in range(max(1, attempts)):
        if user32.GetForegroundWindow() == hwnd:
            if not quiet:
                log.debug("微信已在前台（hwnd=%s title=%r）", hwnd, _win_title(hwnd))
            return hwnd
        _force_foreground(hwnd)
        time.sleep(min(wait, 0.4))  # 只短暂等渲染，不当常驻前台
    fg = user32.GetForegroundWindow()
    raise WxUiError(
        f"发送前临时置前失败：微信 hwnd={hwnd}（当前前台 hwnd={fg} title={_win_title(fg)!r}）；"
        "这轮不发送，5 秒后重试")


def open_chat(name: str, hwnd: Optional[int] = None, timeout: float = 6.0,
              reuse: bool = False) -> int:
    """Ctrl+F → 粘贴群名 → Enter。全程键盘，不碰搜索框坐标。"""
    h = hwnd or health_check(need_foreground=True)
    if (reuse and OPEN_REUSE_SECONDS > 0 and _LAST_OPEN.get("name") == name
            and time.time() - float(_LAST_OPEN.get("at") or 0) <= OPEN_REUSE_SECONDS):
        _LAST_OPEN["at"] = time.time()
        log.info("复用已打开的会话 %r（跳过 Ctrl+F）", name)
        return h
    if not set_clip_text(name):
        raise WxUiError("写剪贴板失败，无法输入群名")
    _combo(VK_F)          # 微信 4.x：Ctrl+F 唤醒搜索
    time.sleep(0.35)
    _combo(VK_A)
    _tap(VK_DELETE, 0.05)
    _combo(VK_V)
    time.sleep(0.55)      # 等结果列表出来
    _tap(VK_RETURN, 0.1)
    time.sleep(0.55)      # 等会话页渲染
    _LAST_OPEN["name"] = name
    _LAST_OPEN["at"] = time.time()
    log.info("已按 Ctrl+F 打开会话 %r（hwnd=%s）", name, h)
    return h


def send_text(name: str, text: str, clear: bool = True, reuse: bool = False) -> int:
    """在指定会话里发一条文字。返回 hwnd。剪贴板与前台窗口发完即还。"""
    st = _send_guard()
    h = 0
    try:
        h = open_chat(name, reuse=reuse)
        _ensure_fg(h)  # 黑框/别的窗口可能刚抢走焦点，粘贴前再拉一次
        if not set_clip_text(text):
            raise WxUiError("写剪贴板失败，文字没发出去")
        if clear:
            _combo(VK_A)
            _tap(VK_DELETE, 0.05)
        _combo(VK_V)
        time.sleep(0.25)
        _ensure_fg(h)  # 回车前确认还在微信输入框，绝不把回车送进控制台
        _tap(VK_RETURN, 0.20)
        time.sleep(0.2)
    finally:
        _send_restore(st, h)
    log.info("文字已投递到 %r（%d 字）", name, len(text))
    return h


def send_image_now(path, wait_draft: float = 1.6) -> int:
    """往【当前已打开】的会话里贴一张图（不切会话，方便发前/发后各抓一张截图核验）。"""
    st = _send_guard()
    h = 0
    try:
        h = health_check(need_foreground=True)
        _ensure_fg(h)
        if not set_clip_files([path]):
            raise WxUiError("写剪贴板失败，图片没发出去")
        _combo(VK_A)
        _tap(VK_DELETE, 0.05)
        _combo(VK_V)
        time.sleep(wait_draft)   # 等输入框把缩略图草稿画出来
        _ensure_fg(h)
        _tap(VK_RETURN, 0.20)
        time.sleep(0.3)
    finally:
        _send_restore(st, h)
    log.info("图片已投递到当前会话（%s）", Path(str(path)).name)
    return h


def send_image(name: str, path, wait_draft: float = 1.6) -> int:
    """切到指定会话再发一张图片（CF_HDROP 剪贴板 → Ctrl+V → Enter）。"""
    h = open_chat(name)
    send_image_now(path, wait_draft)
    log.info("图片已投递到 %r（%s）", name, Path(str(path)).name)
    return h


class WxSender:
    """和 wechatauto.WeChat 的 SendMsg 签名兼容，桥里直接换掉就行。"""

    class _Resp:
        def __init__(self, ok: bool, msg: str = "") -> None:
            self.is_success = ok
            self.success = ok
            self.message = msg
            self.msg = msg

    def SendMsg(self, text: str, who: Optional[str] = None, reuse: bool = False):
        try:
            send_text(who or "", text, reuse=reuse)
            return self._Resp(True)
        except WxUiError:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._Resp(False, f"{type(exc).__name__}: {exc}")

    def SendImage(self, path, who: Optional[str] = None):
        try:
            send_image(who or "", path)
            return self._Resp(True)
        except WxUiError:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._Resp(False, f"{type(exc).__name__}: {exc}")




# ---- 与 wechatauto 兼容的模块级入口（桥里把 WeChat() 换成 wxui 即可） ----
def SendMsg(text: str, who: Optional[str] = None, reuse: bool = False):
    """wechatauto.WeChat.SendMsg 的替代：切会话 → 粘文字 → Enter。"""
    try:
        send_text(who or "", text, reuse=reuse)
        return WxSender._Resp(True)
    except WxUiError:
        raise
    except Exception as exc:  # noqa: BLE001
        return WxSender._Resp(False, f"{type(exc).__name__}: {exc}")


def SendImage(path, who: Optional[str] = None):
    try:
        send_image(who or "", path)
        return WxSender._Resp(True)
    except WxUiError:
        raise
    except Exception as exc:  # noqa: BLE001
        return WxSender._Resp(False, f"{type(exc).__name__}: {exc}")


def quick_send_image(path, who: Optional[str] = None, verify: bool = False):
    """wechatauto.quick_send_image(path, who, verify=...) 的替代（verify 参数仅为签名兼容）。"""
    return SendImage(path, who)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("hwnd:", health_check())
