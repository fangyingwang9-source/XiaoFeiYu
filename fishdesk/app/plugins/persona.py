"""人设页：直接编辑 persona.md（桥热读的就是这个文件）。"""
from __future__ import annotations

import os
import shutil
import tkinter.messagebox as mb
from datetime import datetime

import customtkinter as ctk

from .. import paths
from ..ui import theme
from ..ui.widgets import BasePage, ghost_button, primary_button
from .registry import register


@register("persona", "人设设置", order=20, icon="✎", subtitle="persona.md 唯一来源，保存即生效")
def build(parent, ctx):
    return PersonaPage(parent, ctx)


class PersonaPage(BasePage):
    key = "persona"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        self.path = paths.persona_path(self.ctx.ws)
        card = self.section("人设正文", "桥每次会话前重读该文件；改完点保存，无需重启桥")
        self.box = ctk.CTkTextbox(card, height=380, font=ctk.CTkFont(theme.FONT, 13),
                                  fg_color="#0b0f13", text_color=theme.FG,
                                  border_width=1, border_color=theme.BORDER, wrap="word")
        self.box.grid(row=card.body_row, column=0, sticky="nsew", padx=16, pady=(12, 6))
        card.grid_rowconfigure(card.body_row, weight=1)

        foot = ctk.CTkFrame(card, fg_color="transparent")
        foot.grid(row=card.body_row + 1, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.count = ctk.CTkLabel(foot, text="", font=ctk.CTkFont(theme.FONT, 11),
                                  text_color=theme.FG_MUTED)
        self.count.pack(side="left")
        primary_button(foot, "保存人设", self._save).pack(side="right", padx=(8, 0))
        ghost_button(foot, "从磁盘重载", self._reload, width=120).pack(side="right", padx=(8, 0))
        ghost_button(foot, "打开文件", self._open, width=100).pack(side="right")
        self.box.bind("<KeyRelease>", lambda e: self._update_count())

        tip = self.section("改人设的两个雷区", "踩了会把「人机感」带回来")
        ctk.CTkLabel(
            tip, justify="left", anchor="w", wraplength=820, font=ctk.CTkFont(theme.FONT, 12),
            text_color=theme.FG_MUTED,
            text=("1. 说话风格还有一份 HUMAN_STYLE 常量在 wechat_bridge.py 里（约 145 字），"
                  "只改 persona.md 不改它，模型会两种腔调打架。\n"
                  "2. 每日重置会清掉 persona_sig / last_reply 等状态；人设改动太大时建议顺手重启一次桥，"
                  "让新会话从干净状态开始。"),
        ).grid(row=tip.body_row, column=0, sticky="ew", padx=16, pady=(12, 14))
        self._reload()

    def _load_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as exc:
            self.ctx.toast(f"读取失败：{exc}")
            return ""

    def _reload(self) -> None:
        text = self._load_text()
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.box.get("1.0", "end").strip())
        self.count.configure(text=f"当前 {n} 字符　·　文件：{self.path}")

    def _save(self) -> None:
        text = self.box.get("1.0", "end").rstrip() + "\n"
        if not text.strip():
            mb.showwarning("保存人设", "人设正文是空的，已取消保存。", parent=self)
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_file():
                bdir = self.path.parent / "persona-backups"
                bdir.mkdir(exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(self.path, bdir / f"persona-{stamp}.md")
            tmp = self.path.with_suffix(".md.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
            self._update_count()
            self.ctx.hooks.fire("on_persona_saved", path=str(self.path), chars=len(text.strip()))
            self.ctx.toast(f"人设已保存（{len(text.strip())} 字符）")
        except Exception as exc:
            mb.showerror("保存人设", f"{type(exc).__name__}: {exc}", parent=self)

    def on_config_reload(self, cfg: dict) -> None:
        pass

    def _open(self) -> None:
        try:
            os.startfile(str(self.path))
        except Exception as exc:
            self.ctx.toast(f"打开失败：{exc}")
