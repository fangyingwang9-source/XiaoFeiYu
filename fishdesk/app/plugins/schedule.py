"""作息页：唤醒时段、休眠触发词、空闲阈值，底下接的是已有 power 脚本。"""
from __future__ import annotations

import re
import tkinter.messagebox as mb
from datetime import datetime, timedelta

import customtkinter as ctk

from ..ui import theme
from ..ui.widgets import BasePage, EntryRow, SwitchRow, ghost_button, primary_button
from .registry import register

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@register("schedule", "作息设置", order=50, icon="☾", subtitle="唤醒时段 / 说「休眠」就睡 / 空闲兜底")
def build(parent, ctx):
    return SchedulePage(parent, ctx)


class SchedulePage(BasePage):
    key = "schedule"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        s = ctx.cfg.get("schedule", {})
        self._slot_rows: list[tuple[ctk.CTkFrame, ctk.CTkEntry]] = []

        wake = self.section("每日唤醒时段", "改完要重装计划任务才会生效（下方按钮，需管理员）")
        self.slot_box = ctk.CTkFrame(wake, fg_color="transparent")
        self.slot_box.grid(row=wake.body_row, column=0, sticky="ew", padx=16, pady=(10, 4))
        self.slot_box.grid_columnconfigure(0, weight=1)
        for t in (s.get("wake_slots") or ["09:20"]):
            self._add_slot_row(t)
        bar = ctk.CTkFrame(wake, fg_color="transparent")
        bar.grid(row=wake.body_row + 1, column=0, sticky="ew", padx=16, pady=(6, 14))
        ghost_button(bar, "+ 添加时段", self._add_slot, width=110).pack(side="left")
        ghost_button(bar, "应用计划任务", self._apply_task, width=130).pack(side="left", padx=(8, 0))
        self.next_label = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(theme.FONT, 11),
                                       text_color=theme.FG_MUTED)
        self.next_label.pack(side="left", padx=(14, 0))
        self.sw_notify = SwitchRow(wake, "唤醒后发一条问候", s.get("notify_on_wake", True),
                                   command=lambda v: self.autosave("schedule.notify_on_wake", v))
        self.sw_notify.grid(row=wake.body_row + 2, column=0, sticky="ew", padx=16, pady=(0, 14))

        trig = self.section("「去睡觉」口令", "只有主人 wxid 发这几个词才会真睡，别人说当没看见")
        self.sw_allow = SwitchRow(trig, "允许微信口令休眠", s.get("allow_wechat_sleep", True),
                                  command=lambda v: self.autosave("schedule.allow_wechat_sleep", v))
        self.sw_allow.grid(row=trig.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.entry_trig = EntryRow(trig, "触发词（逗号分隔）",
                                   "，".join(s.get("sleep_triggers", [])), width=320,
                                   command=lambda v: self.autosave(
                                       "schedule.sleep_triggers",
                                       [x.strip() for x in re.split(r"[,，\s]+", v) if x.strip()]))
        self.entry_trig.grid(row=trig.body_row + 1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.sw_owner = SwitchRow(trig, "仅主人可触发", s.get("owner_only", True),
                                  command=lambda v: self.autosave("schedule.owner_only", v))
        self.sw_owner.grid(row=trig.body_row + 2, column=0, sticky="ew", padx=16, pady=(4, 14))

        test = self.section("立刻验证", "点击后 3 秒进入睡眠，唤醒依赖上面的计划任务或手动开机")
        bar2 = ctk.CTkFrame(test, fg_color="transparent")
        bar2.grid(row=test.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        primary_button(bar2, "立即休眠", self._sleep_now, danger=True).pack(side="left")
        ghost_button(bar2, "查看状态", self._status, width=100).pack(side="left", padx=(8, 0))
        self.status_label = ctk.CTkLabel(test, text="", font=ctk.CTkFont(theme.FONT, 11),
                                         text_color=theme.FG_MUTED, anchor="w", justify="left",
                                         wraplength=820)
        self.status_label.grid(row=test.body_row + 1, column=0, sticky="ew", padx=16, pady=(0, 14))

        self.note("提示：休眠/睡眠由 power 脚本调用 SetSuspendState，需要管理员；"
                  "普通权限下会静默失败，所以本程序启动时会自请提权。")
        self._refresh_next()

    # ---------- 时段 ----------
    def _add_slot_row(self, value: str) -> None:
        row = ctk.CTkFrame(self.slot_box, fg_color="transparent")
        row.grid(row=len(self._slot_rows), column=0, sticky="ew", pady=2)
        row.grid_columnconfigure(0, weight=1)
        ent = ctk.CTkEntry(row, width=96, height=30, font=ctk.CTkFont(theme.FONT, 12),
                           fg_color=theme.CARD_SOFT, border_color=theme.BORDER, placeholder_text="HH:MM")
        ent.insert(0, value)
        ent.grid(row=0, column=0, sticky="w")
        ent.bind("<FocusOut>", lambda e: self._commit_slots())
        ent.bind("<Return>", lambda e: self._commit_slots())
        ghost_button(row, "删除", lambda: self._del_slot(row), width=64).grid(row=0, column=1, padx=(8, 0))
        self._slot_rows.append((row, ent))

    def _add_slot(self) -> None:
        self._add_slot_row("12:00")
        self._commit_slots()

    def _del_slot(self, row) -> None:
        for item in list(self._slot_rows):
            if item[0] is row:
                self._slot_rows.remove(item)
        row.destroy()
        self._commit_slots()

    def _commit_slots(self) -> None:
        slots = []
        for _, ent in self._slot_rows:
            v = ent.get().strip()
            if TIME_RE.match(v):
                slots.append(v.zfill(5))
        slots = sorted(set(slots))
        if slots:
            self.autosave("schedule.wake_slots", slots)
            self._refresh_next()

    def _refresh_next(self) -> None:
        try:
            self.next_label.configure(text=self.ctx.power.next_wake_text(
                self.ctx.cfg.get("schedule", {}).get("wake_slots", [])))
        except Exception:
            pass

    def _apply_task(self) -> None:
        if not mb.askyesno("应用计划任务",
                           "将重新注册 DSH-Power-Wake（需要管理员权限），继续？", parent=self):
            return
        ok, msg = self.ctx.power.apply_schedule()
        self.ctx.toast(msg)
        if not ok:
            mb.showwarning("应用计划任务", msg, parent=self)

    # ---------- 动作 ----------
    def _sleep_now(self) -> None:
        if not mb.askyesno("立即休眠", "马上让电脑睡眠？3 秒后执行。", parent=self):
            return
        ok, msg = self.ctx.power.sleep_now("gui-manual", delay=3)
        self.ctx.toast(msg)

    def _status(self) -> None:
        st = self.ctx.power.status()
        self.status_label.configure(
            text=f"上次睡眠：{st.get('last_sleep') or '无'}    上次唤醒：{st.get('last_wake') or '无'}")

    def on_show(self) -> None:
        self._status()
