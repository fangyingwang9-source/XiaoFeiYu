"""控制台自定义控件：Tooltip、带 DIY 开关的数值行、提示条。

为什么要单独一个模块：widgets.py 里的 Card/BasePage 被所有页面共用，改它有回归风险；
新交互（问号提示、微调输入、开关联动）集中放这里，页面只管布局。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from . import theme


class Tooltip:
    """鼠标悬停 0.25 秒后弹出的小提示，鼠标移开就消失。"""

    def __init__(self, widget, text: str, width: int = 300):
        self.widget = widget
        self.text = text
        self.width = width
        self._win = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _evt=None):
        self._cancel()
        self._job = self.widget.after(250, self._show)

    def _cancel(self):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self):
        if self._win is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x}+{y}")
        win.attributes("-topmost", True)
        frame = tk.Frame(win, background=theme.BORDER, padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=self.text, justify="left", wraplength=self.width,
                 background=theme.CARD_SOFT, foreground=theme.FG,
                 font=(theme.FONT, 10), padx=10, pady=8).pack()
        self._win = win

    def _hide(self, _evt=None):
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


def help_dot(master, text: str) -> ctk.CTkLabel:
    """一个灰色问号，悬停显示说明。"""
    lb = ctk.CTkLabel(master, text="?", width=16, height=16, corner_radius=8,
                      fg_color=theme.CARD_SOFT, text_color=theme.FG_MUTED,
                      font=ctk.CTkFont(theme.FONT, 10, "bold"))
    Tooltip(lb, text)
    return lb


class Banner(ctk.CTkFrame):
    """红/黄/灰提示条，可反复改写文字。"""

    def __init__(self, master, manager: str = "pack", grid_kw: dict | None = None, **kw):
        super().__init__(master, fg_color=theme.CARD_SOFT, corner_radius=8, **kw)
        self.manager = manager
        self.grid_kw = grid_kw or {}
        self.label = ctk.CTkLabel(self, text="", anchor="w", justify="left",
                                  font=ctk.CTkFont(theme.FONT, 11),
                                  text_color=theme.FG_MUTED, wraplength=980)
        self.label.pack(fill="x", padx=12, pady=8)

    def show(self, text: str, level: str = "muted") -> None:
        color = {"err": theme.ERR, "warn": theme.WARN, "ok": theme.OK}.get(level, theme.FG_MUTED)
        bg = {"err": "#2a1a1c", "warn": "#2a2416", "ok": "#16251a"}.get(level, theme.CARD_SOFT)
        self.configure(fg_color=bg)
        self.label.configure(text=text, text_color=color)
        if text:
            if self.manager == "grid":
                self.grid(**self.grid_kw)
            else:
                self.pack(fill="x", padx=4, pady=(0, theme.GAP))
        else:
            if self.manager == "grid":
                self.grid_remove()
            else:
                self.pack_forget()


class SpinRow(ctk.CTkFrame):
    """一行参数：问号 + 名称 + DIY 开关 + [−] [数值] [+] + 单位 + 备注。

    - 开关关 = 用内置默认值，输入框与加减都置灰不可点；
    - 开关开 = 用户自定义，数值立刻写回配置；
    - 数值越界自动夹到硬边界，非法输入回退到上一次合法值。
    """

    def __init__(self, master, label: str, value: float, lo: float, hi: float,
                 step: float = 1, unit: str = "", tip: str = "",
                 is_int: bool = True, custom: bool = True, show_toggle: bool = True,
                 default: float = 0, on_change: Callable[[float, bool], None] | None = None,
                 on_custom: Callable[[bool, float], None] | None = None):
        super().__init__(master, fg_color="transparent")
        self.label_text = label
        self.lo, self.hi, self.step = lo, hi, step
        self.is_int = is_int
        self.unit = unit
        self.default = default
        self.on_change = on_change
        self.on_custom = on_custom
        self._value = self._norm(value)
        self._custom = bool(custom)

        self.grid_columnconfigure(0, weight=1 if not label else 0)
        self.grid_columnconfigure(2, weight=1)
        name = None
        if label:
            name = ctk.CTkFrame(self, fg_color="transparent")
            name.grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(name, text=label, font=ctk.CTkFont(theme.FONT, 12),
                         text_color=theme.FG, anchor="w").pack(side="left")
            help_dot(name, tip or label).pack(side="left", padx=(4, 0))

        self.sw = ctk.CTkSwitch(self, text="", width=34, height=16, switch_width=30,
                                switch_height=15, command=self._toggle)
        self.show_toggle = show_toggle
        if show_toggle:
            self.sw.grid(row=0, column=1, sticky="w", padx=(10, 10))
        self.sw.select() if self._custom else self.sw.deselect()

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=0, column=2, sticky="e")
        self.btn_down = ctk.CTkButton(box, text="−", width=24, height=24, corner_radius=6,
                                      fg_color=theme.CARD_SOFT, hover_color=theme.BORDER,
                                      text_color=theme.FG, font=ctk.CTkFont(theme.FONT, 13),
                                      command=lambda: self._bump(-1))
        self.btn_down.pack(side="left")
        self.var = tk.StringVar(value=self._fmt(self._value))
        self.entry = ctk.CTkEntry(box, textvariable=self.var,
                                  width=(64 if label else 56), height=24,
                                  justify="center", corner_radius=6, border_width=1,
                                  border_color=theme.BORDER, fg_color=theme.CARD_SOFT,
                                  font=ctk.CTkFont(theme.FONT_MONO, 12))
        self.entry.pack(side="left", padx=4)
        self.btn_up = ctk.CTkButton(box, text="+", width=24, height=24, corner_radius=6,
                                    fg_color=theme.CARD_SOFT, hover_color=theme.BORDER,
                                    text_color=theme.FG, font=ctk.CTkFont(theme.FONT, 13),
                                    command=lambda: self._bump(1))
        self.btn_up.pack(side="left")
        if unit:
            ctk.CTkLabel(box, text=" " + unit, font=ctk.CTkFont(theme.FONT, 11),
                         text_color=theme.FG_MUTED).pack(side="left", padx=(2, 0))
        # 关闭 DIY 时的「用默认 X」紧贴名称，省掉额外一行高度
        self.hint = ctk.CTkLabel(name or self, text="",
                                 font=ctk.CTkFont(theme.FONT, 10),
                                 text_color=theme.FG_MUTED, anchor="w")
        if name is not None:
            self.hint.pack(side="left", padx=(6, 0))

        self.entry.bind("<Return>", self._commit_entry)
        self.entry.bind("<FocusOut>", self._commit_entry)
        self._apply_state()

    # ---------- 基础 ----------
    def _norm(self, v: float) -> float:
        try:
            v = float(v)
        except Exception:
            v = float(self.default)
        v = max(self.lo, min(self.hi, v))
        return int(round(v)) if self.is_int else round(v, 2)

    def _fmt(self, v: float) -> str:
        if self.is_int:
            return str(int(v))
        return ("%.2f" % v).rstrip("0").rstrip(".")

    def value(self) -> float:
        return self._value

    def custom(self) -> bool:
        return self._custom

    def set_value(self, v: float, notify: bool = False) -> None:
        self._value = self._norm(v)
        self.var.set(self._fmt(self._value))
        if notify and self.on_change:
            self.on_change(self._value, self._custom)

    # ---------- 交互 ----------
    def _apply_state(self) -> None:
        state = "normal" if self._custom else "disabled"
        for w in (self.entry, self.btn_up, self.btn_down):
            try:
                w.configure(state=state)
            except Exception:
                pass
        self.entry.configure(text_color=(theme.FG if self._custom else theme.FG_MUTED))
        self.hint.configure(text="" if self._custom else f"用默认 {self._fmt(self.default)}{self.unit}")

    def set_custom(self, custom: bool, value: float | None = None) -> None:
        """给外部开关（如范围行）同步本字段的可编辑状态。"""
        self._custom = bool(custom)
        if value is not None:
            self._value = self._norm(value)
            self.var.set(self._fmt(self._value))
        self._apply_state()

    def _toggle(self) -> None:
        self._custom = bool(self.sw.get())
        if not self._custom:
            self._value = self._norm(self.default)
            self.var.set(self._fmt(self._value))
        self._apply_state()
        if self.on_custom:
            self.on_custom(self._custom, self._value)

    def _bump(self, sign: int) -> None:
        if not self._custom:
            return
        self._value = self._norm(self._value + sign * self.step)
        self.var.set(self._fmt(self._value))
        if self.on_change:
            self.on_change(self._value, self._custom)

    def _commit_entry(self, _evt=None) -> None:
        if not self._custom:
            self.var.set(self._fmt(self._value))
            return
        raw = self.var.get().strip().replace("，", "").replace(",", "")
        try:
            new = float(raw)
        except Exception:
            self.var.set(self._fmt(self._value))
            return
        clamped = self._norm(new)
        self._value = clamped
        self.var.set(self._fmt(clamped))
        if self.on_change:
            self.on_change(clamped, self._custom)


class RangeRow(ctk.CTkFrame):
    """一个「区间」参数：共用一个 DIY 开关，左右两个数值（双头）。

    例：多数回复字数  [− 6 +] ~ [− 18 +] 字。
    """

    def __init__(self, master, label: str, lo_value: float, hi_value: float,
                 lo_range: tuple[float, float], hi_range: tuple[float, float],
                 unit: str = "", tip: str = "", is_int: bool = True,
                 step: float = 1, custom: bool = True,
                 lo_default: float = 0, hi_default: float = 0,
                 on_change: Callable[[float, float, bool], None] | None = None,
                 on_custom: Callable[[bool], None] | None = None):
        super().__init__(master, fg_color="transparent")
        self.on_change = on_change
        self.on_custom = on_custom
        self.grid_columnconfigure(2, weight=1)
        name = ctk.CTkFrame(self, fg_color="transparent")
        name.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(name, text=label, font=ctk.CTkFont(theme.FONT, 12),
                     text_color=theme.FG).pack(side="left")
        help_dot(name, tip or label).pack(side="left", padx=(4, 0))
        self.sw = ctk.CTkSwitch(self, text="", width=34, height=16, switch_width=30,
                                switch_height=15, command=self._toggle)
        self.sw.grid(row=0, column=1, sticky="w", padx=(10, 10))
        self.sw.select() if custom else self.sw.deselect()

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=0, column=2, sticky="e")
        self.lo = SpinRow(box, label="", value=lo_value, lo=lo_range[0], hi=lo_range[1],
                          step=step, unit="", is_int=is_int, custom=custom,
                          default=lo_default, show_toggle=False,
                          on_change=lambda v, c: self._fire())
        self.lo.pack(side="left")
        ctk.CTkLabel(box, text=" ~ ", font=ctk.CTkFont(theme.FONT, 12),
                     text_color=theme.FG_MUTED).pack(side="left")
        self.hi = SpinRow(box, label="", value=hi_value, lo=hi_range[0], hi=hi_range[1],
                          step=step, unit=unit, is_int=is_int, custom=custom,
                          default=hi_default, show_toggle=False,
                          on_change=lambda v, c: self._fire())
        self.hi.pack(side="left")

    def _toggle(self) -> None:
        custom = bool(self.sw.get())
        self.lo.set_custom(custom)
        self.hi.set_custom(custom)
        if not custom:
            self.lo.set_value(self.lo.default)
            self.hi.set_value(self.hi.default)
        self._fire(toggle=True)
        if self.on_custom:
            self.on_custom(custom)

    def values(self) -> tuple[float, float]:
        return self.lo.value(), self.hi.value()

    def set_values(self, lo_v: float, hi_v: float) -> None:
        self.lo.set_value(lo_v)
        self.hi.set_value(hi_v)

    def custom(self) -> bool:
        return bool(self.sw.get())

    def _fire(self, toggle: bool = False) -> None:
        if self.on_change:
            self.on_change(self.lo.value(), self.hi.value(), bool(self.sw.get()))


class TextField(ctk.CTkFrame):
    """带标题与问号的单行输入。"""

    def __init__(self, master, label: str, value: str = "", tip: str = "",
                 placeholder: str = "", on_change: Callable[[str], None] | None = None,
                 width: int = 300):
        super().__init__(master, fg_color="transparent")
        self.label = label
        self.on_change = on_change
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(anchor="w")
        ctk.CTkLabel(head, text=label, font=ctk.CTkFont(theme.FONT, 12),
                     text_color=theme.FG).pack(side="left")
        help_dot(head, tip or label).pack(side="left", padx=(4, 0))
        self.var = tk.StringVar(value=value or "")
        self.entry = ctk.CTkEntry(self, textvariable=self.var, width=width, height=30,
                                  corner_radius=6, border_width=1, border_color=theme.BORDER,
                                  fg_color=theme.CARD_SOFT, placeholder_text=placeholder,
                                  font=ctk.CTkFont(theme.FONT, 12))
        self.entry.pack(anchor="w", pady=(4, 0), fill="x")
        self.entry.bind("<FocusOut>", self._fire)
        self.entry.bind("<Return>", self._fire)

    def _fire(self, _evt=None):
        if self.on_change:
            self.on_change(self.var.get().strip())

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, v: str) -> None:
        self.var.set(v or "")


class AreaField(ctk.CTkFrame):
    """带标题与问号的多行输入（性格底色、示例对话这类）。"""

    def __init__(self, master, label: str, value: str = "", tip: str = "",
                 height: int = 90, on_change: Callable[[str], None] | None = None,
                 placeholder: str = ""):
        super().__init__(master, fg_color="transparent")
        self.label = label
        self.on_change = on_change
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(anchor="w")
        ctk.CTkLabel(head, text=label, font=ctk.CTkFont(theme.FONT, 12),
                     text_color=theme.FG).pack(side="left")
        help_dot(head, tip or label).pack(side="left", padx=(4, 0))
        self.box = ctk.CTkTextbox(self, height=height, corner_radius=6, border_width=1,
                                  border_color=theme.BORDER, fg_color=theme.CARD_SOFT,
                                  font=ctk.CTkFont(theme.FONT, 12), wrap="word")
        self.box.pack(fill="x", pady=(4, 0))
        if placeholder:
            ctk.CTkLabel(self, text=placeholder, font=ctk.CTkFont(theme.FONT, 10),
                         text_color=theme.FG_MUTED, anchor="w", justify="left").pack(anchor="w")
        self.box.insert("1.0", value or "")
        self.box.bind("<FocusOut>", self._fire)

    def _fire(self, _evt=None):
        if self.on_change:
            self.on_change(self.get())

    def get(self) -> str:
        return self.box.get("1.0", "end").strip()

    def set(self, v: str) -> None:
        self.box.delete("1.0", "end")
        self.box.insert("1.0", v or "")


def segmented(master, values: list[str], command: Callable[[str], None],
              width: int = 240, value: str = "") -> ctk.CTkOptionMenu:
    m = ctk.CTkOptionMenu(master, values=values, command=command, width=width, height=30,
                          corner_radius=6, fg_color=theme.CARD_SOFT,
                          button_color=theme.BORDER, button_hover_color=theme.BORDER,
                          dropdown_fg_color=theme.CARD, dropdown_hover_color=theme.SIDEBAR_ACTIVE,
                          font=ctk.CTkFont(theme.FONT, 12),
                          dropdown_font=ctk.CTkFont(theme.FONT, 12))
    if value and value in values:
        m.set(value)
    return m
