"""控制台页：三条连通性绿灯、起停、实时日志。"""
from __future__ import annotations

import os
import time
import tkinter.messagebox as mb
from pathlib import Path

import customtkinter as ctk

from ..ui import theme
from ..ui.widgets import BasePage, Card, LogBox, StatusPill, ghost_button, primary_button
from .registry import register


def _launch_wechat() -> tuple[bool, str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat") as k:
            pass
    except Exception:
        pass
    try:
        import winreg
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WeChat.exe"),
                          (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WeChat.exe")):
            try:
                with winreg.OpenKey(root, key) as k:
                    path = winreg.QueryValue(k, "")
                    if path and Path(path).is_file():
                        os.startfile(path)
                        return True, "已调起微信"
            except Exception:
                continue
    except Exception:
        pass
    # 4.x 进程叫 Weixin.exe，且不一定写 App Paths；先按运行中进程的可执行路径拉起
    try:
        import psutil
        for p in psutil.process_iter(["name", "exe"]):
            if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe") and p.info.get("exe"):
                os.startfile(p.info["exe"])
                return True, "已调起微信（按运行中的进程路径）"
    except Exception:
        pass
    for exe in ("Weixin.exe", "WeChat.exe"):
        try:
            os.startfile(exe)
            return True, f"已尝试调起 {exe}"
        except Exception:
            continue
    return False, "找不到微信：请手动打开微信后重试"


@register("console", "控制台", order=10, icon="◉", subtitle="连通性 / 起停 / 实时日志")
def build(parent, ctx):
    return ConsolePage(parent, ctx)


class ConsolePage(BasePage):
    key = "console"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        self._build_status()
        self._build_actions()
        self._build_log()

    # ---------- 状态 ----------
    def _build_status(self) -> None:
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", pady=(4, theme.GAP))
        self._row = 1
        for i in range(3):
            row.grid_columnconfigure(i, weight=1, uniform="st")
        self.pill_wechat = StatusPill(row, "微信客户端")
        self.pill_wechat.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.pill_dsh = StatusPill(row, "DSH 桌面端")
        self.pill_dsh.grid(row=0, column=1, sticky="ew", padx=6)
        self.pill_bridge = StatusPill(row, "微信桥进程")
        self.pill_bridge.grid(row=0, column=2, sticky="ew", padx=(6, 0))

    def _build_actions(self) -> None:
        card = self.section("快捷操作", "启动/停止只对桥进程生效，不影响微信与 DSH 本身")
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=card.body_row, column=0, sticky="ew", padx=16, pady=(10, 4))
        primary_button(btns, "启动桥", self._start).pack(side="left", padx=(0, 8))
        ghost_button(btns, "停止桥", self._stop, width=100).pack(side="left", padx=(0, 8))
        ghost_button(btns, "重启桥", self._restart, width=100).pack(side="left", padx=(0, 8))
        ghost_button(btns, "启动微信", self._start_wechat, width=110).pack(side="left", padx=(0, 8))
        ghost_button(btns, "打开日志", self._open_log, width=110).pack(side="left")

        self.info = ctk.CTkLabel(card, text="", font=ctk.CTkFont(theme.FONT, 11),
                                 text_color=theme.FG_MUTED, anchor="w", justify="left")
        self.info.grid(row=card.body_row + 1, column=0, sticky="ew", padx=16, pady=(6, 14))

    def _build_log(self) -> None:
        card = self.section("实时日志", "读取 wechat-bridge/wechat-bridge.log 尾部，1.5 秒刷新")
        ctl = ctk.CTkFrame(card, fg_color="transparent")
        ctl.grid(row=card.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.auto = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(ctl, text="自动刷新", variable=self.auto, width=44,
                      progress_color=theme.ACCENT, font=ctk.CTkFont(theme.FONT, 12)).pack(side="left")
        self.lines_var = ctk.StringVar(value="80")
        ctk.CTkLabel(ctl, text="行数", font=ctk.CTkFont(theme.FONT, 11),
                     text_color=theme.FG_MUTED).pack(side="left", padx=(18, 4))
        ctk.CTkOptionMenu(ctl, values=["40", "80", "160", "300"], variable=self.lines_var, width=80,
                          height=28, font=ctk.CTkFont(theme.FONT, 11),
                          fg_color=theme.CARD_SOFT, button_color=theme.SIDEBAR_ACTIVE,
                          command=lambda v: self._refresh_log()).pack(side="left")
        ghost_button(ctl, "刷新", self._refresh_log, width=80).pack(side="right")
        self.log = LogBox(card, height=300)
        self.log.grid(row=card.body_row + 1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        card.grid_rowconfigure(card.body_row + 1, weight=1)

    # ---------- 刷新 ----------
    def on_show(self) -> None:
        self._refresh_status()
        self._refresh_log()

    def tick(self) -> None:
        self._refresh_status()
        if self.auto.get():
            self._refresh_log()

    def _refresh_status(self) -> None:
        st = self.ctx.bridge.status()
        self.pill_wechat.set(st["wechat"], "在线" if st["wechat"] else "离线")
        self.pill_dsh.set(st["dsh"], "在运行" if st["dsh"] else "未检测到")
        self.pill_bridge.set(st["bridge"], f"运行中 · {st['bridge_pid']}" if st["bridge"] else "已停止")
        py = self.ctx.cfg.get("bridge", {}).get("python", "")
        last = st["last_msg"]
        last_txt = time.strftime("%m-%d %H:%M:%S", time.localtime(last)) if last else "无记录"
        self.info.configure(
            text=f"桥 PID：{st['bridge_pid'] or '—'}    最后一条消息时间：{last_txt}    "
                 f"Python：{py or '未配置'}    配置：{self.ctx.store.path}"
        )

    def _refresh_log(self) -> None:
        n = int(self.lines_var.get() or 80)
        self.log.set_lines(self.ctx.bridge.tail(n))

    # ---------- 动作 ----------
    def _start(self) -> None:
        cfg = self.ctx.cfg
        ok, msg = self.ctx.bridge.start(
            python_exe=cfg.get("bridge", {}).get("python", ""),
            sticker_enabled=bool(cfg.get("strategy", {}).get("sticker_enabled", True)),
            dry_run=bool(cfg.get("advanced", {}).get("dryrun", False)),
        )
        self.ctx.toast(msg)
        if not ok:
            mb.showwarning("启动桥", msg, parent=self)
        self._refresh_status()
        self._refresh_log()

    def _stop(self) -> None:
        if not mb.askyesno("停止桥", "确定停止微信桥进程？", parent=self):
            return
        ok, msg = self.ctx.bridge.stop()
        self.ctx.toast(msg)
        self._refresh_status()
        self._refresh_log()

    def _restart(self) -> None:
        self.ctx.bridge.stop()
        time.sleep(1.0)
        self._start()

    def _start_wechat(self) -> None:
        ok, msg = _launch_wechat()
        self.ctx.toast(msg)

    def _open_log(self) -> None:
        target = self.ctx.bridge.log_file
        if not target.is_file():
            target = self.ctx.bridge.console_log
        try:
            os.startfile(str(target) if target.is_file() else str(self.ctx.bridge.dir))
        except Exception as exc:
            self.ctx.toast(f"打开失败：{exc}")
