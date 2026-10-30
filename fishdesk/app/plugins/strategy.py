"""回复策略页：把 wechat_bridge.py 里的常量变成滑杆。"""
from __future__ import annotations

import customtkinter as ctk

from ..ui import theme
from ..ui.widgets import BasePage, SliderRow, SwitchRow
from .registry import register


@register("strategy", "回复策略", order=30, icon="⚙", subtitle="节奏、拆条、表情与风控，改完即写 config.json")
def build(parent, ctx):
    return StrategyPage(parent, ctx)


class StrategyPage(BasePage):
    key = "strategy"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        s = ctx.cfg.get("strategy", {})

        pace = self.section("说话节奏", "越大越慢；冷场（对方半天不回）用另一套更慢的参数")
        self._slider(pace, "暖场随机延迟", "strategy.quiet_warm", s.get("quiet_warm", 1.5), 0.3, 5.0, 47, unit=" 秒")
        self._slider(pace, "冷场随机延迟", "strategy.quiet_cold", s.get("quiet_cold", 2.5), 0.5, 10.0, 95, unit=" 秒")
        self._slider(pace, "一次最多连发", "strategy.bubble_max", s.get("bubble_max", 18), 1, 40, 39, fmt="{:.0f}", unit=" 条")
        self._slider(pace, "拆条数上限", "strategy.bubble_parts", s.get("bubble_parts", 3), 1, 6, 5, fmt="{:.0f}", unit=" 段")
        self._slider(pace, "单条字数上限", "strategy.max_reply_chars", s.get("max_reply_chars", 1200), 100, 4000, 39,
                     fmt="{:.0f}", unit=" 字")
        self._slider(pace, "主动发言上限", "strategy.active_max", s.get("active_max", 6), 1, 15, 14, fmt="{:.0f}", unit=" 条")

        risk = self.section("风控", "太密会被判定营销；这些值是踩过坑之后的安全区")
        self._slider(risk, "回复冷却（暖场）", "strategy.cooldown", s.get("cooldown", 1.2), 0.2, 5.0, 48, unit=" 秒")
        self._slider(risk, "回复冷却（冷场）", "strategy.cooldown_cold", s.get("cooldown_cold", 3.0), 0.5, 10.0, 95, unit=" 秒")
        self._slider(risk, "刷屏判定阈值", "strategy.spam_trigger", s.get("spam_trigger", 6), 3, 15, 12, fmt="{:.0f}", unit=" 条")
        self._slider(risk, "命中后静音", "strategy.mute", s.get("mute", 45), 10, 180, 34, fmt="{:.0f}", unit=" 分钟")
        self._slider(risk, "裁判冷却", "strategy.judge_cooldown", s.get("judge_cooldown", 180), 30, 600, 57,
                     fmt="{:.0f}", unit=" 秒")

        st = self.section("表情包", "关闭后一条都不发；频率按「每 N 条回复夹一张」计算")
        self.sw = SwitchRow(st, "启用表情包", s.get("sticker_enabled", True),
                            command=lambda v: self.autosave("strategy.sticker_enabled", v))
        self.sw.grid(row=st.body_row, column=0, sticky="ew", padx=16, pady=(10, 4))
        self._slider(st, "每隔最少", "strategy.sticker_every_min", s.get("sticker_every_min", 5), 1, 30, 29,
                     fmt="{:.0f}", unit=" 条")
        self._slider(st, "每隔最多", "strategy.sticker_every_max", s.get("sticker_every_max", 8), 1, 40, 39,
                     fmt="{:.0f}", unit=" 条")

        fine = self.section("细项", "不常动，但影响手感")
        self._slider(fine, "主动发言窗口", "strategy.active_window", s.get("active_window", 150), 30, 600, 57,
                     fmt="{:.0f}", unit=" 秒")
        self._slider(fine, "合并等待", "strategy.batch_wait", s.get("batch_wait", 1.5), 0.0, 6.0, 60, unit=" 秒")
        self._slider(fine, "链长上限", "strategy.max_chain", s.get("max_chain", 6), 1, 20, 19, fmt="{:.0f}", unit=" 轮")

    def _slider(self, card, label, dotted, value, lo, hi, steps, fmt="{:.1f}", unit=""):
        row = SliderRow(card, label, float(value), lo, hi, steps, fmt=fmt, unit=unit,
                        command=lambda v, d=dotted: self.autosave(d, v))
        row.grid(row=card.body_row, column=0, sticky="ew", padx=16, pady=(8, 6))
        return row
