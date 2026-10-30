"""回复策略页：把「人设里的数字」集中成一块可调面板。

设计要点
- 参数按「人话」分组：说话节奏 / 字数与口吻 / 表情包 / 防刷屏与冷却 / 上下文记忆，
  两列布局一屏看完，不再是一条条滑杆往下滚。
- 每个参数都有 DIY 开关：关 = 用内置安全默认值并置灰，开 = 自己填（写进 strategy.tuning）。
- 所有改动立即写回 config.json 并重新生成 persona.md，桥 2 秒内热加载。
"""
from __future__ import annotations

import customtkinter as ctk

from ..persona_profile import render_conflicts, sync_persona
from ..ui import theme
from ..ui.controls import Banner, RangeRow, SpinRow, help_dot
from ..ui.widgets import BasePage, SwitchRow, ghost_button
from .registry import register


@register("strategy", "回复策略", order=30, icon="⚙", subtitle="节奏 / 字数 / 冷却，改完即生效")
def build(parent, ctx):
    return StrategyPage(parent, ctx)


class StrategyPage(BasePage):
    key = "strategy"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        self._rows: dict[str, object] = {}
        self._save_job = None
        self.body.grid_columnconfigure(0, weight=1, uniform="col")
        self.body.grid_columnconfigure(1, weight=1, uniform="col")
        s = dict(self.ctx.store.data.get("strategy", {}) or {})
        self._tuning = dict(s.get("tuning") or {})

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="改完自动保存：写进 config.json，persona.md 重生成，桥最迟 2 秒热加载。",
                     font=ctk.CTkFont(theme.FONT, 10), text_color=theme.FG_MUTED,
                     anchor="w").grid(row=0, column=0, sticky="w")
        ghost_button(bar, "全部恢复内置默认", self._reset_all, width=140).grid(row=0, column=1, sticky="e")

        self.banner = Banner(self.body, manager="grid",
                             grid_kw=dict(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8)))
        self._build_left()
        self._build_right()
        self._refresh_conflicts()

    # ---------- 布局工具 ----------
    def _card(self, row: int, col: int, title: str, sub: str) -> ctk.CTkFrame:
        from ..ui.widgets import Card
        card = Card(self.body, title=title, subtitle=sub)
        card.grid(row=row, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0),
                  pady=(0, 10))
        box = ctk.CTkFrame(card, fg_color="transparent")
        card.mount(box, pady=(4, 8))
        return box

    def _spin(self, parent, key: str, label: str, lo: float, hi: float, step: float,
              unit: str, tip: str, default: float, is_int: bool = True) -> SpinRow:
        s = self.ctx.store.data.get("strategy", {}) or {}
        row = SpinRow(parent, label=label, value=s.get(key, default), lo=lo, hi=hi,
                      step=step, unit=unit, tip=tip, is_int=is_int,
                      custom=bool(self._tuning.get(key)), default=default,
                      on_change=lambda v, c: self._set(key, v),
                      on_custom=lambda c, v: self._custom(key, c, v))
        row.pack(fill="x", pady=0)
        self._rows[key] = row
        return row

    def _range(self, parent, lo_key: str, hi_key: str, label: str, tip: str,
               lo_range: tuple[float, float], hi_range: tuple[float, float],
               unit: str, default: tuple[float, float]) -> RangeRow:
        s = self.ctx.store.data.get("strategy", {}) or {}
        row = RangeRow(parent, label=label,
                       lo_value=s.get(lo_key, default[0]), hi_value=s.get(hi_key, default[1]),
                       lo_range=lo_range, hi_range=hi_range, unit=unit, tip=tip,
                       custom=bool(self._tuning.get(lo_key)), lo_default=default[0],
                       hi_default=default[1],
                       on_change=lambda a, b, c: self._set_pair(lo_key, hi_key, a, b),
                       on_custom=lambda c: self._custom_pair(lo_key, hi_key, c))
        row.pack(fill="x", pady=0)
        self._rows[lo_key] = row
        return row

    # ---------- 左列：说话节奏 / 字数 / 表情包 ----------
    def _build_left(self) -> None:
        b1 = self._card(2, 0, "说话·节奏", "太急像机器人，太慢像掉线")
        self._spin(b1, "quiet_warm", "群里热聊时先等", 0.5, 5, 0.1, "秒",
                   "群里刚有人说话时，先等这么久再回，避免抢话", 1.5, is_int=False)
        self._spin(b1, "quiet_cold", "群里冷场时先等", 1, 8, 0.1, "秒",
                   "半天没人说话时，等更久一点显得不刻意", 2.5, is_int=False)
        self._spin(b1, "bubble_parts", "一条回复最多拆成", 1, 6, 1, "段",
                   "长回复拆成几条发，更像真人；拆太多会刷屏", 3)
        self._spin(b1, "bubble_max", "连发上限", 1, 30, 1, "条",
                   "一次最多连着发几条，超过就等对方回", 18)

        b2 = self._card(3, 0, "说话·字数与口吻", "字数会同时写进人设，别和人设冲突")
        self._range(b2, "reply_chars_min", "reply_chars_max", "多数回复字数",
                    "平时聊天一条回复的字数区间；太短像敷衍，太长像写作文",
                    (1, 60), (1, 60), "字", (6, 18))
        self._spin(b2, "short_reply_chars", "被怼时只回", 1, 40, 1, "字",
                   "被怼、被催、懒得聊时用更短的一句（上限）", 6)
        self._spin(b2, "tease_every", "毒舌频率", 3, 60, 1, "条/次",
                   "平均多少条回一句毒舌，调大更嘴甜，调小更毒", 10)
        self._spin(b2, "max_reply_chars", "单条硬上限", 20, 1200, 10, "字",
                   "防御性上限：再啰嗦也不会超过这个长度", 400)

        b3 = self._card(4, 0, "表情包", "关掉就纯文字，不再发表情")
        on = ctk.BooleanVar(value=bool((self.ctx.store.data.get("strategy", {}) or {}).get("sticker_enabled", True)))
        sw = ctk.CTkSwitch(b3, text="允许发微信表情包", variable=on, width=34, height=16,
                           switch_width=30, switch_height=15,
                           font=ctk.CTkFont(theme.FONT, 12),
                           command=lambda: self._set("sticker_enabled", bool(on.get())))
        sw.pack(anchor="w", pady=(2, 4))
        self._range(b3, "sticker_every_min", "sticker_every_max", "每隔几条发一张",
                    "区间内随机；太密会被当成卖萌机器人", (1, 30), (2, 60), "条", (5, 8))

    # ---------- 右列：防刷屏 / 上下文 ----------
    def _build_right(self) -> None:
        b4 = self._card(2, 1, "防刷屏与冷却", "防别人刷屏，也防自己刷屏")
        self._spin(b4, "spam_trigger", "刷屏判定：一人连发", 2, 20, 1, "条",
                   "同一个人连续发这么多条还没等到回复，就算刷屏", 6)
        self._spin(b4, "mute", "命中后冷处理", 10, 600, 5, "秒",
                   "刷屏的人暂时不接，过这么久再理", 45)
        self._spin(b4, "judge_cooldown", "防刷屏冷却时间", 30, 1800, 10, "秒",
                   "两次「判定刷屏」之间至少隔多久，避免误伤一群人", 180)
        self._spin(b4, "cooldown", "热聊回复间隔", 0.5, 10, 0.1, "秒",
                   "两条回复之间至少隔这么久，防止手速不像人", 1.2, is_int=False)
        self._spin(b4, "cooldown_cold", "冷场回复间隔", 0.5, 15, 0.1, "秒",
                   "冷场时的最短间隔，比热聊更慢", 3.0, is_int=False)

        b5 = self._card(3, 1, "上下文记忆", "记得太多会变慢，太少会失忆")
        self._spin(b5, "max_chain", "上下文记忆轮数", 1, 12, 1, "轮",
                   "回复时回看最近多少轮对话（一轮≈一问一答）", 6)
        self._spin(b5, "active_window", "主动找话题窗口", 30, 600, 10, "秒",
                   "静默超过这么久才会自己找话说", 150)
        self._spin(b5, "active_max", "热聊时最多主动", 1, 20, 1, "次",
                   "一轮里最多主动搭话几次（热聊）", 6)
        self._spin(b5, "active_max_cold", "冷场时最多主动", 1, 20, 1, "次",
                   "冷场时最多主动搭话几次，别硬聊", 9)
        self._spin(b5, "batch_wait", "合并消息等待", 0.3, 5, 0.1, "秒",
                   "对方连发几条时，先等这么久再一起回", 1.5, is_int=False)


    # ---------- 写值 ----------
    def _set(self, key: str, value) -> None:
        self.ctx.store.set(f"strategy.{key}", value)
        self._after_change()

    def _custom(self, key: str, custom: bool, value) -> None:
        if custom:
            self._tuning[key] = True
        else:
            self._tuning.pop(key, None)
            self.ctx.store.set(f"strategy.{key}", value)
        self.ctx.store.set("strategy.tuning", dict(self._tuning))
        self._after_change()

    def _set_pair(self, lo_key: str, hi_key: str, lo_v: float, hi_v: float) -> None:
        if lo_v > hi_v:
            lo_v, hi_v = hi_v, lo_v
            row = self._rows.get(lo_key)
            if isinstance(row, RangeRow):
                row.set_values(lo_v, hi_v)
        self.ctx.store.set(f"strategy.{lo_key}", lo_v)
        self.ctx.store.set(f"strategy.{hi_key}", hi_v)
        self._after_change()

    def _custom_pair(self, lo_key: str, hi_key: str, custom: bool) -> None:
        if custom:
            self._tuning[lo_key] = True
        else:
            self._tuning.pop(lo_key, None)
        self.ctx.store.set("strategy.tuning", dict(self._tuning))
        self._after_change()

    def _reset_all(self) -> None:
        self._tuning.clear()
        self.ctx.store.set("strategy.tuning", {})
        from ..config_store import DEFAULTS
        for k, v in (DEFAULTS.get("strategy") or {}).items():
            if k == "tuning":
                continue
            self.ctx.store.set(f"strategy.{k}", v)
        self._rebuild()
        self._after_change()
        self.ctx.toast("已恢复内置默认值")

    def _after_change(self) -> None:
        sync_persona(self.ctx.ws, self.ctx.store.data, force=False)
        self._refresh_conflicts()
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(500, self.flush)

    # ---------- 冲突检查 ----------
    def _refresh_conflicts(self) -> None:
        issues = render_conflicts(self.ctx.store.data.get("strategy", {}) or {})
        if issues:
            self.banner.show("参数互相打架：\n" + "\n".join(f"· {i}" for i in issues), "err")
        else:
            self.banner.show("", "muted")

    # ---------- 重建 ----------
    def _rebuild(self) -> None:
        for child in list(self.body.winfo_children()):
            if child is not self.banner:
                child.destroy()
        self._rows.clear()
        self._build_left()
        self._build_right()
        self._refresh_conflicts()

    def on_config_reload(self, cfg: dict) -> None:
        self._refresh_conflicts()
