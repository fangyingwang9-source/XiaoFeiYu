"""通用控件与页面基类。"""
from __future__ import annotations

import customtkinter as ctk

from . import theme


class Card(ctk.CTkFrame):
    def __init__(self, master, title: str = "", subtitle: str = "", **kw):
        kw.setdefault("fg_color", theme.CARD)
        kw.setdefault("corner_radius", 12)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", theme.BORDER)
        super().__init__(master, **kw)
        self.grid_columnconfigure(0, weight=1)
        self._row = 0
        if title:
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.grid(row=self._row, column=0, sticky="ew", padx=16, pady=(14, 0))
            head.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(head, text=title, font=ctk.CTkFont(theme.FONT, 14, "bold"),
                         text_color=theme.FG, anchor="w").grid(row=0, column=0, sticky="w")
            if subtitle:
                ctk.CTkLabel(head, text=subtitle, font=ctk.CTkFont(theme.FONT, 11),
                             text_color=theme.FG_MUTED, anchor="w").grid(row=1, column=0, sticky="w", pady=(2, 0))
            self._row += 1
        self.body_row = self._row
        self._row += 1

    def mount(self, widget, **grid):
        grid.setdefault("sticky", "ew")
        grid.setdefault("padx", 16)
        grid.setdefault("pady", (10, 14))
        widget.grid(row=self.body_row, column=0, **grid)
        return widget


class BasePage(ctk.CTkFrame):
    """所有页面的父类：滚动容器 + 防抖自动保存（保存即生效）。"""

    key = ""
    title = ""

    def __init__(self, parent, ctx):
        super().__init__(parent, fg_color="transparent")
        self.ctx = ctx
        self._save_job = None
        self._saving_guard = False
        self.body = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=theme.BORDER, scrollbar_button_hover_color=theme.ACCENT_DARK,
        )
        self.body.pack(fill="both", expand=True, padx=(24, 10), pady=(8, 16))
        self.body.grid_columnconfigure(0, weight=1)
        self._row = 0

    # ---- 布局 ----
    def section(self, title: str, subtitle: str = "") -> Card:
        card = Card(self.body, title=title, subtitle=subtitle)
        card.grid(row=self._row, column=0, sticky="ew", pady=(0, theme.GAP))
        self._row += 1
        return card

    def note(self, text: str, color: str = theme.FG_MUTED) -> ctk.CTkLabel:
        lb = ctk.CTkLabel(self.body, text=text, font=ctk.CTkFont(theme.FONT, 11),
                          text_color=color, anchor="w", justify="left", wraplength=760)
        lb.grid(row=self._row, column=0, sticky="ew", pady=(0, theme.GAP))
        self._row += 1
        return lb

    # ---- 自动保存 ----
    def autosave(self, dotted: str, value, delay: int = 400) -> None:
        if self._saving_guard:
            return
        self.ctx.store.set(dotted, value)
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(delay, self.flush)

    def flush(self) -> None:
        self._save_job = None
        ok = self.ctx.store.save()
        self.ctx.store.sync_power_config()
        self.ctx.hooks.fire("on_config_reload", data=self.ctx.store.data)
        if ok:
            self.ctx.toast("已保存并生效")
        else:
            self.ctx.toast(f"保存失败：{self.ctx.store.error}")

    # ---- 生命周期 ----
    def on_show(self) -> None:
        pass

    def on_config_reload(self, cfg: dict) -> None:
        pass


class StatusPill(ctk.CTkFrame):
    """一个带圆点的状态条：● 微信 在线。"""

    def __init__(self, master, label: str, **kw):
        kw.setdefault("fg_color", theme.CARD_SOFT)
        kw.setdefault("corner_radius", 10)
        super().__init__(master, **kw)
        self.dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(theme.FONT, 16),
                               text_color=theme.FG_MUTED, width=18)
        self.dot.grid(row=0, column=0, padx=(12, 4), pady=10)
        self.name = ctk.CTkLabel(self, text=label, font=ctk.CTkFont(theme.FONT, 12),
                                 text_color=theme.FG_MUTED, anchor="w")
        self.name.grid(row=0, column=1, padx=(0, 6), pady=10, sticky="w")
        self.state = ctk.CTkLabel(self, text="检测中", font=ctk.CTkFont(theme.FONT, 12, "bold"),
                                  text_color=theme.FG_MUTED, anchor="w")
        self.state.grid(row=0, column=2, padx=(0, 12), pady=10, sticky="w")

    def set(self, ok: bool | None, text: str) -> None:
        color = theme.OK if ok else (theme.ERR if ok is False else theme.WARN)
        self.dot.configure(text_color=color)
        self.state.configure(text=text, text_color=color)


class LogBox(ctk.CTkFrame):
    def __init__(self, master, height: int = 220, **kw):
        kw.setdefault("fg_color", "#0b0f13")
        kw.setdefault("corner_radius", 10)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", theme.BORDER)
        super().__init__(master, **kw)
        self.box = ctk.CTkTextbox(
            self, height=height, fg_color="transparent", border_width=0,
            font=ctk.CTkFont(theme.FONT_MONO, 11), text_color="#b9c7d3",
            wrap="none", activate_scrollbars=True,
        )
        self.box.pack(fill="both", expand=True, padx=6, pady=6)
        self.box.configure(state="disabled")

    def set_lines(self, lines: list[str]) -> None:
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("end", "\n".join(lines) if lines else "（暂无日志）")
        self.box.see("end")
        self.box.configure(state="disabled")


class SwitchRow(ctk.CTkFrame):
    def __init__(self, master, label: str, value: bool, desc: str = "", command=None):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.var = ctk.BooleanVar(value=bool(value))
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(theme.FONT, 13), text_color=theme.FG,
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.sw = ctk.CTkSwitch(self, text="", variable=self.var, width=44,
                                progress_color=theme.ACCENT, command=self._fire)
        self.sw.grid(row=0, column=1, sticky="e")
        self._command = command
        if desc:
            ctk.CTkLabel(self, text=desc, font=ctk.CTkFont(theme.FONT, 11), text_color=theme.FG_MUTED,
                         anchor="w", justify="left", wraplength=620).grid(row=1, column=0, columnspan=2, sticky="w")

    def _fire(self):
        if self._command:
            self._command(self.var.get())

    def set(self, value: bool) -> None:
        self.var.set(bool(value))


class SliderRow(ctk.CTkFrame):
    def __init__(self, master, label: str, value: float, from_: float, to: float, steps: int,
                 fmt: str = "{:.1f}", unit: str = "", command=None, desc: str = ""):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self._fmt = fmt
        self._command = command
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=label, font=ctk.CTkFont(theme.FONT, 13), text_color=theme.FG,
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.value_label = ctk.CTkLabel(head, text=fmt.format(value) + unit,
                                        font=ctk.CTkFont(theme.FONT, 12, "bold"),
                                        text_color=theme.ACCENT, anchor="e")
        self.value_label.grid(row=0, column=1, sticky="e")
        self.slider = ctk.CTkSlider(self, from_=from_, to=to, number_of_steps=max(1, int(steps)),
                                    progress_color=theme.ACCENT, button_color=theme.ACCENT,
                                    button_hover_color=theme.ACCENT_DARK, command=self._moved)
        self.slider.set(value)
        self.slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        if desc:
            ctk.CTkLabel(self, text=desc, font=ctk.CTkFont(theme.FONT, 11), text_color=theme.FG_MUTED,
                         anchor="w", wraplength=620).grid(row=2, column=0, columnspan=2, sticky="w")

    def _moved(self, v):
        self.value_label.configure(text=self._fmt.format(float(v)) + getattr(self, "_unit", ""))
        if self._command:
            self._command(round(float(v), 2))

    def set(self, value: float) -> None:
        self.slider.set(value)
        self.value_label.configure(text=self._fmt.format(value))


class EntryRow(ctk.CTkFrame):
    def __init__(self, master, label: str, value: str, desc: str = "", width: int = 260,
                 command=None, placeholder: str = ""):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(theme.FONT, 13), text_color=theme.FG,
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.entry = ctk.CTkEntry(self, width=width, height=32, font=ctk.CTkFont(theme.FONT, 12),
                                  fg_color=theme.CARD_SOFT, border_color=theme.BORDER,
                                  placeholder_text=placeholder)
        self.entry.insert(0, value)
        self.entry.grid(row=0, column=1, sticky="e")
        if command:
            self.entry.bind("<FocusOut>", lambda e: command(self.entry.get().strip()))
            self.entry.bind("<Return>", lambda e: command(self.entry.get().strip()))
        if desc:
            ctk.CTkLabel(self, text=desc, font=ctk.CTkFont(theme.FONT, 11), text_color=theme.FG_MUTED,
                         anchor="w", wraplength=620).grid(row=1, column=0, columnspan=2, sticky="w")

    def get(self) -> str:
        return self.entry.get().strip()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, value)


def primary_button(master, text: str, command, width: int = 120, danger: bool = False) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=34,
        font=ctk.CTkFont(theme.FONT, 12, "bold"), corner_radius=8,
        fg_color=(theme.ERR if danger else theme.ACCENT),
        hover_color=("#c0392b" if danger else theme.ACCENT_DARK),
    )


def ghost_button(master, text: str, command, width: int = 110) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=34,
        font=ctk.CTkFont(theme.FONT, 12), corner_radius=8,
        fg_color=theme.CARD_SOFT, hover_color=theme.SIDEBAR_ACTIVE,
        border_width=1, border_color=theme.BORDER,
    )
