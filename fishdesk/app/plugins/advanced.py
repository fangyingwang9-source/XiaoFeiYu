"""高级页：dryrun、日记、token 统计、路径与维护动作。"""
from __future__ import annotations

import os
import tkinter.messagebox as mb

import customtkinter as ctk

from ..ui import theme
from ..ui.widgets import BasePage, EntryRow, SwitchRow, ghost_button, primary_button
from .registry import register


@register("advanced", "高级设置", order=60, icon="⚒", subtitle="dryrun / 日记 / token 统计 / 维护")
def build(parent, ctx):
    return AdvancedPage(parent, ctx)


class AdvancedPage(BasePage):
    key = "advanced"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        a = ctx.cfg.get("advanced", {})

        run = self.section("运行开关", "dryrun 打开后只写日志不真发微信，调试时用")
        self.sw_dry = SwitchRow(run, "dryrun（不真发）", a.get("dryrun", False),
                                command=lambda v: self.autosave("advanced.dryrun", v))
        self.sw_dry.grid(row=run.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.sw_diary = SwitchRow(run, "启用日记", a.get("diary_enabled", True),
                                  command=lambda v: self.autosave("advanced.diary_enabled", v))
        self.sw_diary.grid(row=run.body_row + 1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.sw_reset = SwitchRow(run, "每日重置会话", a.get("session_reset_daily", True),
                                  command=lambda v: self.autosave("advanced.session_reset_daily", v))
        self.sw_reset.grid(row=run.body_row + 2, column=0, sticky="ew", padx=16, pady=(4, 14))

        stats = self.section("Token 统计", "读 usage.jsonl 累加，仅供参考")
        self.stat_label = ctk.CTkLabel(stats, text="", font=ctk.CTkFont(theme.FONT_MONO, 12),
                                       text_color=theme.FG, anchor="w", justify="left")
        self.stat_label.grid(row=stats.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        ghost_button(stats, "刷新统计", self._refresh_stats, width=100).grid(
            row=stats.body_row + 1, column=0, sticky="w", padx=16, pady=(0, 14))

        path = self.section("路径", "换机器时改这里；workspace 必须是包含 wechat-bridge 的目录")
        self.entry_py = EntryRow(path, "桥用 Python", str(ctx.cfg.get("bridge", {}).get("python", "")),
                                 width=560, command=lambda v: self.autosave("bridge.python", v),
                                 desc="一般是 wxauto 的 venv：C:\\Users\\<你>\\.dsh\\wxauto\\venv\\Scripts\\python.exe")
        self.entry_py.grid(row=path.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.label_ws = ctk.CTkLabel(path, text=f"工作区：{ctx.ws}", font=ctk.CTkFont(theme.FONT, 11),
                                     text_color=theme.FG_MUTED, anchor="w")
        self.label_ws.grid(row=path.body_row + 1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.label_cfg = ctk.CTkLabel(path, text=f"配置：{ctx.store.path}", font=ctk.CTkFont(theme.FONT, 11),
                                      text_color=theme.FG_MUTED, anchor="w")
        self.label_cfg.grid(row=path.body_row + 2, column=0, sticky="ew", padx=16, pady=(0, 14))

        maint = self.section("维护", "这些都是本地脚本，不会联网")
        bar = ctk.CTkFrame(maint, fg_color="transparent")
        bar.grid(row=maint.body_row, column=0, sticky="ew", padx=16, pady=(10, 14))
        ghost_button(bar, "打开工作区", self._open_ws, width=110).pack(side="left")
        ghost_button(bar, "打开配置", self._open_cfg, width=110).pack(side="left", padx=(8, 0))
        ghost_button(bar, "同步 power 配置", self._sync, width=140).pack(side="left", padx=(8, 0))
        ghost_button(bar, "重启桥", self._restart, width=100).pack(side="left", padx=(8, 0))
        ghost_button(bar, "打开日志", self._open_log, width=110).pack(side="left", padx=(8, 0))

        hooks = self.section("钩子", "语音等扩展模块在启动时注册到总线；这里是当前挂载情况")
        self.hook_label = ctk.CTkLabel(hooks, text="", font=ctk.CTkFont(theme.FONT_MONO, 11),
                                       text_color=theme.FG_MUTED, anchor="w", justify="left")
        self.hook_label.grid(row=hooks.body_row, column=0, sticky="ew", padx=16, pady=(10, 14))
        self._refresh_stats()
        self._refresh_hooks()

    def _refresh_stats(self) -> None:
        s = self.ctx.stats
        try:
            d = s.token_today()
            self.stat_label.configure(
                text=f"今日模型调用：{d['calls']} 次    今日 token：{d['tokens']:,}    "
                     f"persona.md：{s.persona_chars()} 字符    usage.jsonl：{d['bytes'] / 1024:.0f} KB")
        except Exception as exc:
            self.stat_label.configure(text=f"统计失败：{exc}")

    def _refresh_hooks(self) -> None:
        try:
            items = self.ctx.hooks.summary()
            self.hook_label.configure(text="\n".join(items) if items else "（暂无扩展注册）")
        except Exception as exc:
            self.hook_label.configure(text=f"读取失败：{exc}")

    def _open_ws(self) -> None:
        try:
            os.startfile(str(self.ctx.ws))
        except Exception as exc:
            self.ctx.toast(f"打开失败：{exc}")

    def _open_cfg(self) -> None:
        try:
            os.startfile(str(self.ctx.store.path))
        except Exception as exc:
            self.ctx.toast(f"打开失败：{exc}")

    def _sync(self) -> None:
        try:
            ok = self.ctx.store.sync_power_config()
            self.ctx.toast("power/config.json 已同步" if ok else "同步失败，看控制台日志")
        except Exception as exc:
            self.ctx.toast(f"同步失败：{exc}")

    def _restart(self) -> None:
        self.ctx.bridge.stop()
        import time
        time.sleep(1.0)
        cfg = self.ctx.cfg
        ok, msg = self.ctx.bridge.start(
            cfg.get("bridge", {}).get("python", ""),
            bool(cfg.get("strategy", {}).get("sticker_enabled", True)),
            bool(cfg.get("advanced", {}).get("dryrun", False)))
        self.ctx.toast(msg)

    def _open_log(self) -> None:
        try:
            target = self.ctx.bridge.log_file
            os.startfile(str(target if target.is_file() else self.ctx.bridge.dir))
        except Exception as exc:
            self.ctx.toast(f"打开失败：{exc}")

    def on_show(self) -> None:
        self._refresh_stats()
        self._refresh_hooks()
