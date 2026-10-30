"""主窗口：左侧导航 + 右侧内容，页面全部来自插件注册表。"""
from __future__ import annotations

import time
from pathlib import Path

import customtkinter as ctk

from .. import paths
from ..services.bridge import BridgeService
from ..services.power import PowerService
from ..services.stats import StatsService
from ..plugins import load_plugins, pages_for
from ..persona_profile import sync_persona
from . import theme
from .widgets import ghost_button


class AppContext:
    """页面能用到的一切：路径、配置、钩子、服务。"""

    def __init__(self, ws: Path, store, hooks):
        self.ws = ws
        self.store = store
        self.hooks = hooks
        self.cfg = store.data
        self.bridge = BridgeService(ws)
        self.power = PowerService(ws)
        self.stats = StatsService(ws)
        self.window = None

    def toast(self, msg: str) -> None:
        if self.window is not None:
            self.window.toast(msg)


class MainWindow(ctk.CTk):
    def __init__(self, ws: Path, store, hooks):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.ws = Path(ws)
        self.store = store
        self.hooks = hooks
        self.ctx = AppContext(ws, store, hooks)
        self.ctx.window = self
        self._instances: dict[str, object] = {}
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._current = ""
        self._toast_job = None
        self._toast_label = None

        self.title(paths.APP_TITLE)
        self.geometry("1180x812")
        self.minsize(1024, 700)
        self.configure(fg_color=theme.BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_content()

        errors = load_plugins()
        self._plugin_errors = errors
        self.specs = pages_for(self.store.data)
        self._build_nav_buttons()
        if self.specs:
            self.show(self.specs[0].key)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            sync_persona(self.ws, self.store.data, force=False)
        except Exception:
            pass
        self.after(1200, self._poll)

    # ---------- 侧栏 ----------
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=theme.SIDEBAR)
        bar.grid(row=0, column=0, sticky="ns")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(2, weight=1)
        bar.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(bar, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 6))
        ctk.CTkLabel(head, text="小肥鱼", font=ctk.CTkFont(theme.FONT, 20, "bold"),
                     text_color=theme.FG, anchor="w").pack(anchor="w")
        ctk.CTkLabel(head, text=f"桌面控制台 v{paths.APP_VERSION}", font=ctk.CTkFont(theme.FONT, 11),
                     text_color=theme.FG_MUTED, anchor="w").pack(anchor="w", pady=(2, 0))

        self.nav = ctk.CTkFrame(bar, fg_color="transparent")
        self.nav.grid(row=2, column=0, sticky="new", padx=10, pady=(12, 0))
        self.nav.grid_columnconfigure(0, weight=1)

        foot = ctk.CTkFrame(bar, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=14, pady=14)
        self.dot_wechat = ctk.CTkLabel(foot, text="● 微信", font=ctk.CTkFont(theme.FONT, 11),
                                       text_color=theme.FG_MUTED, anchor="w")
        self.dot_wechat.pack(anchor="w")
        self.dot_bridge = ctk.CTkLabel(foot, text="● 微信桥", font=ctk.CTkFont(theme.FONT, 11),
                                       text_color=theme.FG_MUTED, anchor="w")
        self.dot_bridge.pack(anchor="w", pady=(2, 0))
        self.admin_label = ctk.CTkLabel(foot, text="", font=ctk.CTkFont(theme.FONT, 10),
                                        text_color=theme.FG_MUTED, anchor="w")
        self.admin_label.pack(anchor="w", pady=(6, 0))
        self._refresh_admin_label()
        ghost_button(foot, "打开配置目录", self._open_config_dir, width=150).pack(anchor="w", pady=(8, 0))

    def _refresh_admin_label(self) -> None:
        from ..bootstrap import is_admin
        admin = is_admin()
        self.admin_label.configure(
            text=("● 管理员权限" if admin else "● 非管理员（休眠/计划任务会失败）"),
            text_color=(theme.FG_MUTED if admin else theme.WARN),
        )

    # ---------- 内容 ----------
    def _build_content(self) -> None:
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=0, column=1, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(wrap, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(20, 4))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(header, text="", font=ctk.CTkFont(theme.FONT, 22, "bold"),
                                       text_color=theme.FG, anchor="w")
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_sub = ctk.CTkLabel(header, text="", font=ctk.CTkFont(theme.FONT, 11),
                                     text_color=theme.FG_MUTED, anchor="w")
        self.page_sub.grid(row=1, column=0, sticky="w", pady=(3, 0))

        self.container = ctk.CTkFrame(wrap, fg_color="transparent")
        self.container.grid(row=1, column=0, sticky="nsew")

    # ---------- 导航 ----------
    def show(self, key: str) -> None:
        spec = next((s for s in self.specs if s.key == key), None)
        if spec is None:
            return
        self._current = key
        self.page_title.configure(text=spec.title)
        self.page_sub.configure(text=spec.subtitle or "")
        for k, btn in self._buttons.items():
            active = k == key
            btn.configure(
                fg_color=(theme.SIDEBAR_ACTIVE if active else "transparent"),
                text_color=(theme.FG if active else theme.FG_MUTED),
            )
        page = self._instances.get(key)
        if page is None:
            try:
                page = spec.factory(self.container, self.ctx)
            except Exception as exc:
                page = self._error_page(spec.key, exc)
            self._instances[key] = page
        for p in self._instances.values():
            try:
                p.pack_forget()
            except Exception:
                pass
        page.pack(fill="both", expand=True)
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception:
                pass

    def _error_page(self, key: str, exc: Exception) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self.container, fg_color=theme.CARD, corner_radius=12)
        ctk.CTkLabel(f, text=f"页面 {key} 加载失败", font=ctk.CTkFont(theme.FONT, 14, "bold"),
                     text_color=theme.ERR).pack(anchor="w", padx=20, pady=(20, 4))
        ctk.CTkLabel(f, text=f"{type(exc).__name__}: {exc}", font=ctk.CTkFont(theme.FONT, 12),
                     text_color=theme.FG_MUTED, wraplength=700, justify="left").pack(anchor="w", padx=20, pady=(0, 20))
        return f

    def _build_nav_buttons(self) -> None:
        for i, spec in enumerate(self.specs):
            btn = ctk.CTkButton(
                self.nav, text=f"  {spec.icon}   {spec.title}", anchor="w", height=40,
                corner_radius=8, font=ctk.CTkFont(theme.FONT, 13),
                fg_color="transparent", hover_color=theme.SIDEBAR_ACTIVE,
                text_color=theme.FG_MUTED, command=lambda k=spec.key: self.show(k),
            )
            btn.grid(row=i, column=0, sticky="ew", pady=2)
            self._buttons[spec.key] = btn

    # ---------- 轮询 ----------
    def _poll(self) -> None:
        try:
            if self.store.poll():
                self.ctx.cfg = self.store.data
                try:
                    sync_persona(self.ws, self.store.data, force=False)
                except Exception:
                    pass
                for p in self._instances.values():
                    fn = getattr(p, "on_config_reload", None)
                    if fn:
                        try:
                            fn(self.store.data)
                        except Exception:
                            pass
            st = self.ctx.bridge.status()
            self.dot_wechat.configure(
                text="● 微信 " + ("在线" if st["wechat"] else "离线"),
                text_color=(theme.OK if st["wechat"] else theme.ERR))
            self.dot_bridge.configure(
                text="● 微信桥 " + ("运行中" if st["bridge"] else "已停止"),
                text_color=(theme.OK if st["bridge"] else theme.FG_MUTED))
            for p in self._instances.values():
                if getattr(p, "winfo_ismapped", lambda: False)():
                    tick = getattr(p, "tick", None)
                    if tick:
                        try:
                            tick()
                        except Exception:
                            pass
        except Exception:
            pass
        self.after(1500, self._poll)

    # ---------- 杂项 ----------
    def toast(self, msg: str) -> None:
        if self._toast_label is None:
            self._toast_label = ctk.CTkLabel(
                self, text="", fg_color=theme.CARD_SOFT, corner_radius=8,
                font=ctk.CTkFont(theme.FONT, 12), text_color=theme.FG, padx=14, pady=8)
        self._toast_label.configure(text=msg)
        self._toast_label.place(relx=0.985, rely=0.965, anchor="se")
        if self._toast_job:
            try:
                self.after_cancel(self._toast_job)
            except Exception:
                pass
        self._toast_job = self.after(2400, lambda: self._toast_label.place_forget())

    def _open_config_dir(self) -> None:
        try:
            import os
            os.startfile(str(self.ws / "wechat-bridge"))
        except Exception as exc:
            self.toast(f"打开失败：{exc}")

    def _on_close(self) -> None:
        try:
            self.store.save()
        except Exception:
            pass
        self.destroy()
