"""微信 4.x 适配实现（UI Automation 定位 + SendInput 注入）。

关键结论（本仓库实测）：
1. 微信 4.x 只有一个 Qt 主窗口，没有独立的输入框 HWND，PostMessage/SendMessage
   这类后台注入全部无效，只能"前置窗口 + SendInput"。
2. 语音按钮是 UIA Button「发语音 ( 按住右 Alt )」；真正的录音靠按住右 Alt，
   鼠标长按按钮只会把输入模式切到"长按鼠标 语音输入"，不会录音。
3. 右 Alt 必须带 KEYEVENTF_EXTENDEDKEY(0x0001)，否则会被当成左 Alt，微信无反应。
4. 微信的"Esc 收起 / 托盘隐藏"会让窗口 IsWindowVisible=False 且非最小化，
   ShowWindow 拉不回来；可用的恢复手段是它自己的全局热键 Ctrl+Alt+W。
5. 录音浮层（取消 / 发送语音 两个按钮）出现有 0.5~1.5s 延迟，判定录音态要轮询。
"""
from __future__ import annotations

import os
import sys
import threading
import time

try:  # 允许 python -c "import wx4"（sys.path 指到包目录）和包内相对导入两种用法
    from .base import AdapterError, ProbeResult, WeChatAdapter
except ImportError:  # pragma: no cover
    from base import AdapterError, ProbeResult, WeChatAdapter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BRIDGE_DIR = os.path.join(ROOT, "wechat-bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

VK_RMENU = 0xA5
VK_ALT = 0x12
VK_W = 0x57
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
# 录音浮层的真实控件名（实测）："松开 发送" TextControl、"按下 退格键 (  ) 取消" TextControl
REC_HINTS = ("松开 发送", "松开即可发送", "退格键")

# uiautomation 需要线程级 COM 初始化：桥进程里这套代码跑在 daemon 线程上，
# 不初始化就直接报 OSError [WinError -2147221008] 尚未调用 CoInitialize。
_UIA_READY = set()


def _ensure_uia():
    import uiautomation as auto

    ident = threading.get_ident()
    if ident not in _UIA_READY:
        init = getattr(auto, "InitializeUIAutomationInCurrentThread", None)
        if init is not None:
            init()
        else:  # pragma: no cover - 老版本兜底
            import comtypes

            comtypes.CoInitialize()
        _UIA_READY.add(ident)
    return auto


class Wx4Adapter(WeChatAdapter):
    name = "wx4"

    def __init__(self) -> None:
        import wxui

        self._wxui = wxui
        self.last_error = ""

    # ------------------------------------------------------------------ 窗口
    def _hwnd(self) -> int:
        return int(self._wxui.health_check(quiet=True))

    def _restore_window(self, tries: int = 3) -> bool:
        """窗口被 Esc / 托盘收起时用微信自己的全局热键 Ctrl+Alt+W 切回来。"""
        for _ in range(max(1, tries)):
            try:
                self._wxui._key_down(self._wxui.VK_CONTROL)
                self._wxui._key_down(VK_ALT)
                time.sleep(0.05)
                self._wxui._tap(VK_W, 0.05)
                self._wxui._key_up(VK_ALT)
                self._wxui._key_up(self._wxui.VK_CONTROL)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.9)
            try:
                self._hwnd()
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def probe(self) -> ProbeResult:
        try:
            hwnd = self._hwnd()
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(self.name, False, detail=f"找不到微信主窗口: {exc}")
        rect = ()
        try:
            from win_shot import find_wechat_window

            _h, rect = find_wechat_window()
        except Exception:  # noqa: BLE001
            rect = ()
        return ProbeResult(self.name, True, hwnd=hwnd, rect=tuple(rect), recording=self.recording())

    def ensure_ready(self) -> dict:
        try:
            hwnd = self._hwnd()
        except Exception:  # noqa: BLE001
            if not self._restore_window():
                raise AdapterError("微信主窗口不在，且 Ctrl+Alt+W 拉不回来")
            hwnd = self._hwnd()
        self._wxui._force_foreground(hwnd)
        self._wxui._ensure_fg(hwnd)
        return {"hwnd": hwnd}

    def _window_rect(self) -> tuple:
        try:
            from win_shot import find_wechat_window

            return tuple(find_wechat_window()[1])
        except Exception:  # noqa: BLE001
            auto = _ensure_uia()
            r = auto.ControlFromHandle(self._hwnd()).BoundingRectangle
            return (r.left, r.top, r.right, r.bottom)

    def foreground(self) -> bool:
        import ctypes

        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        try:
            return int(u.GetForegroundWindow() or 0) == int(self._hwnd())
        except Exception:  # noqa: BLE001
            return False

    def ensure_foreground(self, tries: int = 3) -> bool:
        """硬置前：ShowWindow(SW_RESTORE) + SetForegroundWindow，轮询校验。

        实测置前会失败（DSH/控制台窗口占着前台），这时点输入区和右 Alt 全部被吞，
        录音浮层永远不出现，表现为「未检测到录音浮层(无异常)」。
        """
        import ctypes

        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        for _ in range(max(1, tries)):
            if self.foreground():
                return True
            try:
                hwnd = ctypes.c_void_p(int(self._hwnd()))
                u.ShowWindow(hwnd, 9)  # SW_RESTORE
                u.SetForegroundWindow(hwnd)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"置前失败: {exc}"
            time.sleep(0.35)
        return self.foreground()

    def focus_input(self, settle: float = 0.3) -> list:
        """点一下会话输入区，给输入框键盘焦点。

        实测：右 Alt 只有前台窗口才有意义；置前失败时点输入区和按键全被吞，
        录音浮层根本不出来。所以先硬置前，再点输入区，点完再确认一次。
        """
        auto = _ensure_uia()

        self.ensure_foreground()
        left, top, right, bottom = self._window_rect()
        w, h = right - left, bottom - top
        x = int(left + w * 0.42)
        y = int(bottom - max(48, int(h * 0.058)))
        auto.Click(x, y)
        time.sleep(settle)
        if not self.foreground():
            self.ensure_foreground(tries=2)
        return [x, y]

    def open_chat(self, who: str) -> None:
        self._wxui.open_chat(who)

    def send_text(self, who: str, text: str) -> None:
        self._wxui.send_text(who, text)

    # ---------------------------------------------------------------- 录音态
    def _rec_buttons(self) -> dict:
        """录音浮层里的按钮 {name: rect}；只扫名称，命中即停。"""
        auto = _ensure_uia()

        found = {}
        root = auto.ControlFromHandle(self._hwnd())
        for ctrl, _depth in auto.WalkControl(root, maxDepth=30):
            try:
                name = (ctrl.Name or "").strip()
                if any(h in name for h in REC_HINTS):
                    found[name] = ctrl.BoundingRectangle
                    if len(found) >= 2:
                        break
            except Exception:  # noqa: BLE001
                continue
        return found

    def recording(self) -> bool:
        try:
            ok = bool(self._rec_buttons())
            self.last_error = ""
            return ok
        except Exception as exc:  # noqa: BLE001
            import traceback

            self.last_error = "%s: %s" % (type(exc).__name__, traceback.format_exc()[-400:])
            return False

    def _bubble_items(self) -> list:
        """消息区语音气泡 [{name, top}]，按 top 排序；左侧会话预览项含换行，排除。"""
        auto = _ensure_uia()

        items = []
        try:
            root = auto.ControlFromHandle(self._hwnd())
            for ctrl, _depth in auto.WalkControl(root, maxDepth=30):
                try:
                    name = (ctrl.Name or "").strip()
                    if not name or "\n" in name:
                        continue
                    if name.startswith("语音") and "秒" in name:
                        items.append({"name": name, "top": int(ctrl.BoundingRectangle.top)})
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            return items
        items.sort(key=lambda i: i["top"])
        return items

    def voice_bubbles(self) -> list:
        """消息区里已有的语音气泡名，形如 语音9"秒。"""
        return [i["name"] for i in self._bubble_items()]

    @staticmethod
    def _sig_changed(before: list, now: list) -> bool:
        """可见条数会被虚拟列表顶成固定值（实测 14 条封顶），不能只比数量：
        新旧最后一条气泡（名字 + top）不同，就说明新气泡把老气泡顶了上去。"""
        if len(now) != len(before):
            return True
        if not before or not now:
            return False
        return (now[-1]["name"] != before[-1]["name"]
                or abs(now[-1]["top"] - before[-1]["top"]) > 3)

    def _ralt(self, flags: int) -> None:
        inp = self._wxui._INPUT(type=1)  # INPUT_KEYBOARD
        inp.u.ki.wVk = VK_RMENU
        inp.u.ki.dwFlags = flags
        self._wxui._send(inp)

    def _ralt_down(self) -> None:
        self._ralt(KEYEVENTF_EXTENDEDKEY)

    def _ralt_up(self) -> None:
        self._ralt(KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY)

    def voice_start(self, settle: float = 0.3, wait: float = 2.5, tries: int = 2) -> bool:
        self.ensure_ready()
        self._sig_before = self._bubble_items()
        self.last_send = {}
        for attempt in range(max(1, tries)):
            try:
                self.focus_input()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(settle)
            self._ralt_down()
            deadline = time.time() + wait
            while time.time() < deadline:
                if self.recording():
                    return True
                time.sleep(0.12)
            # 没进入录音态就松开，避免右 Alt 卡在按下状态；再点一次输入区重试
            self._ralt_up()
            time.sleep(0.5)
        return False

    def voice_stop(self) -> None:
        self._ralt_up()

    def voice_cancel(self) -> None:
        try:
            btns = self._rec_buttons()
            rect = next((r for n, r in btns.items() if "取消" in n or "退格键" in n), None)
            if rect is not None:
                auto = _ensure_uia()
                auto.Click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                time.sleep(0.4)
            if self.recording():  # 点不掉就松开右 Alt 收尾
                self._ralt_up()
                time.sleep(0.3)
        except Exception:  # noqa: BLE001
            try:
                self._ralt_up()
            except Exception:  # noqa: BLE001
                pass

    def wait_voice_sent(self, timeout: float = 8.0) -> bool:
        """松开后等两件事：离开录音态（released）+ 消息区冒出新的语音气泡（new_bubble）。"""
        before = list(getattr(self, "_sig_before", []) or [])
        deadline = time.time() + timeout
        released = False
        while time.time() < deadline:
            if not self.recording():
                released = True
                break
            time.sleep(0.15)
        deadline = time.time() + max(2.0, timeout / 2)
        while time.time() < deadline:
            now = self._bubble_items()
            if self._sig_changed(before, now):
                self.last_send = {"released": True, "new_bubble": True,
                                  "bubble": now[-1]["name"] if now else "",
                                  "total_before": len(before), "total_after": len(now)}
                return True
            if not released and not self.recording():
                released = True
            time.sleep(0.25)
        self.last_send = {"released": released, "new_bubble": False,
                          "total_before": len(before), "total_after": len(self._bubble_items())}
        return False

    # ---------------- 语音接收（耳朵）：只读文件系统，绝不碰前台 ----------------

    def iter_voice_messages(self, stop_event=None, poll_seconds: float = 0.8):
        """后台轮询微信语音缓存，把新出现的语音文件当作 VoiceMessage 交出去。"""
        # 4.x 收到语音不再落 .silk 文件：语音字节在 db_storage 的加密库里，
        # 走数据库读取（见 wx4_voice_watch / wx4_voice_db），仍是纯后台只读。
        try:
            from wx4_voice_watch import VoiceDBWatcher  # 扁平导入：adapter 目录在 sys.path
        except ImportError:  # pragma: no cover
            from .wx4_voice_watch import VoiceDBWatcher  # type: ignore
        watcher = VoiceDBWatcher()

        # watcher 就地更新自己那个 last_scan dict，这里直接引用即可实时反映扫描状态
        self.last_scan = watcher.last_scan
        return watcher.iter_messages(stop_event=stop_event, poll_seconds=poll_seconds)

    def display_name(self, wxid: str) -> str:
        """联系人名册里的昵称/备注；名册打不开就退回 wxid（只读 contact.db）。"""
        try:
            from wx4_contacts import name_of  # 扁平导入：adapter 目录在 sys.path
        except ImportError:  # pragma: no cover
            from .wx4_contacts import name_of  # type: ignore
        try:
            return name_of(wxid) or str(wxid or "")
        except Exception:  # noqa: BLE001
            return str(wxid or "")

    def newest_voice_clip(self, chat: str, since_ts: float, wait_s: float = 5.0):
        """回读刚发出的语音气泡（只读 media_0.db），取不到返回 None。"""
        try:
            from wx4_voice_watch import VoiceDBWatcher  # 扁平导入：adapter 目录在 sys.path
        except ImportError:  # pragma: no cover
            from .wx4_voice_watch import VoiceDBWatcher  # type: ignore
        try:
            reader = VoiceDBWatcher().reader_for()
        except Exception:  # noqa: BLE001
            return None
        deadline = time.time() + max(1.0, wait_s)
        while True:
            try:
                recs = reader.voices(limit=12)
            except Exception:  # noqa: BLE001
                recs = []
            for rec in recs:
                if rec.get("chat") != chat:
                    # 桥传进来的是昵称/备注（「柒骁」），库里存的是 wxid；不比对名字
                    # 就永远 mismatch -> 回读质检直接跳过，空语音也就漏过去了
                    try:
                        aliases = {rec.get("chat_id"), rec.get("sender"), self.display_name(rec.get("chat"))}
                    except Exception:  # noqa: BLE001
                        aliases = {rec.get("chat_id"), rec.get("sender")}
                    if chat not in {a for a in aliases if a}:
                        continue
                ts = float(rec.get("ts") or 0)
                if ts + 1.0 < since_ts:
                    continue
                try:
                    path = reader.write_silk(rec)
                except Exception:  # noqa: BLE001
                    return None
                return {"path": path, "ts": ts, "size": int(rec.get("size") or 0)}
            if time.time() >= deadline:
                return None
            time.sleep(0.6)


# 语音缓存文件名/路径的判定关键词（目录名、扩展名、文件头三重判定）
_VOICE_DIR_HINTS = ("voicetemp", "voice", "audio", "silk", "ptt", "sound")
_VOICE_EXTS = (".silk", ".slk", ".amr", ".aud", ".sln", ".m4a", ".opus")
_SILK_MAGICS = (b"#!SILK_V3", b"#!SILK", b"\x02#!", b"\x02\x23\x21")
_PRUNE_HINTS = ("\\img\\", "\\video\\", "\\file\\", "\\favorite\\", "\\emoji\\",
                "hardlink", "backup")


def _wechat_data_roots() -> list:
    """找微信 4.x 的数据根（xwechat_files）。支持环境变量覆盖，找不到返回空。"""
    env = os.environ.get("WECHAT_FILES_ROOT", "").strip()
    cands = []
    if env:
        cands.append(env)
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    cands.append(os.path.join(docs, "xwechat_files"))
    cands.append(os.path.join(os.environ.get("APPDATA", ""), "Tencent", "xwechat", "xwechat_files"))
    for letter in "CDEFGH":
        cands.append(letter + ":\\xwechat_files")
    return [p for p in cands if os.path.isdir(p)]


class VoiceCacheWatcher:
    r"""只读扫描微信 4.x 语音缓存目录，发现新语音文件就 yield VoiceMessage。

    实测目录（本机 \\xwechat_files 在 D 盘）：
      <账号>\cache\<YYYY-MM>\Message\<会话hash>\VoiceTemp\…    播放时解码出的临时音频
      <账号>\msg\attach\<会话hash>\<YYYY-MM>\Voice\…           收到的语音附件
    4.x 各版本落盘位置不完全一致，所以按「路径关键词 + 扩展名 + 文件头 magic」判定，
    不写死路径；也不做任何置前/点击，全程纯后台只读。
    """

    def __init__(self, roots=None, min_bytes: int = 200, max_bytes: int = 8 * 1024 * 1024):
        self.roots = list(roots or _wechat_data_roots())
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes
        self._seen = {}
        self._primed = False
        self.last_scan = {"files": 0, "candidates": 0, "roots": list(self.roots), "ts": 0.0}

    # -- 单次扫描：返回 [(mtime, VoiceMessage)] --
    def scan_once(self, min_mtime: float = 0.0):
        from base import VoiceMessage  # 兼容 python -c / 包内两种导入

        out = []
        total = 0
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                low = dirpath.lower()
                if any(h in low + "\\" for h in _PRUNE_HINTS):
                    dirnames[:] = []
                    continue
                depth = dirpath.count(os.sep) - root.count(os.sep)
                if depth > 9:
                    dirnames[:] = []
                    continue
                low_dir = low
                for fn in filenames:
                    total += 1
                    p = os.path.join(dirpath, fn)
                    ext = os.path.splitext(fn)[1].lower()
                    hinted = (ext in _VOICE_EXTS) or any(h in low_dir for h in _VOICE_DIR_HINTS)
                    if not hinted and "silk" not in fn.lower():
                        continue
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if not (self.min_bytes <= st.st_size <= self.max_bytes):
                        continue
                    if st.st_mtime < min_mtime - 1:
                        continue
                    key = (p, st.st_size, int(st.st_mtime))
                    if key in self._seen:
                        continue
                    try:
                        with open(p, "rb") as fh:
                            head = fh.read(16)
                    except OSError:
                        continue
                    self._seen[key] = st.st_mtime
                    out.append((st.st_mtime, VoiceMessage(
                        path=p, ts=st.st_mtime, size=st.st_size,
                        source="cache", head_hex=head.hex())))
        self.last_scan = {"files": total, "candidates": len(out), "roots": list(self.roots),
                          "ts": time.time()}
        out.sort(key=lambda x: x[0])
        return out

    # -- 常驻轮询 --
    def iter_messages(self, stop_event=None, poll_seconds: float = 0.8, prime_seconds: float = 0.0):
        import threading

        stop = stop_event or threading.Event()
        if not self.roots:
            raise AdapterError("找不到微信 4.x 数据目录（xwechat_files），可设置 WECHAT_FILES_ROOT")
        if not self._primed:
            start = time.time()
            while time.time() - start < prime_seconds and not stop.is_set():
                self.scan_once(min_mtime=start)
                time.sleep(min(poll_seconds, 0.5))
            self.scan_once(min_mtime=start)
            self._primed = True
        while not stop.is_set():
            for _mt, msg in self.scan_once(min_mtime=time.time() - 2.0):
                yield msg
            stop.wait(poll_seconds)
