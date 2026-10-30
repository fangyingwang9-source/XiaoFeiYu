"""人设页：只填性格底色，量化规则交给回复策略页。

旧版把 6-18 字、毒舌频率这些数字写死在 persona.md 里，同时在策略页又有一份，
两边容易打架（双写）。现在 persona.md 是「生成物」：
  性格底色（本页 profile） + 策略页的数字  →  渲染成 persona.md
改哪边都会重新生成，桥只需要读一个文件。
"""
from __future__ import annotations

import os
from pathlib import Path

import customtkinter as ctk

from ..persona_profile import (DEFAULT_PROFILE, PRESET_BY_KEY, PRESET_NAMES, PRESETS,
                               preset_by_name, preset_of, render_persona, sync_persona)
from ..ui import theme
from ..ui.controls import AreaField, Banner, TextField, segmented
from ..ui.widgets import BasePage, ghost_button, primary_button
from .registry import register


@register("persona", "人设", order=20, icon="☺", subtitle="性格底色 / 模板 / 自动生成 persona.md")
def build(parent, ctx):
    return PersonaPage(parent, ctx)


class PersonaPage(BasePage):
    key = "persona"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        cfg = self.ctx.store.data.get("persona", {}) or {}
        self._profile = dict(DEFAULT_PROFILE)
        self._profile.update(cfg.get("profile") or {})
        self._preset_key = cfg.get("preset") or "warm"
        self._dirty = False
        self._build_template()
        self._build_profile()
        self._build_output()

    # ---------- 模板 ----------
    def _build_template(self) -> None:
        card = self.section("套用模板", "选一个现成的性格底子，点一下自动填好下面所有字段；不满意可以继续手改")
        box = ctk.CTkFrame(card, fg_color="transparent")
        card.mount(box, pady=(8, 12))

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x")
        self.preset_menu = segmented(row, PRESET_NAMES, self._preset_selected, width=200,
                                     value=PRESET_BY_KEY.get(self._preset_key, PRESETS[0])["name"])
        self.preset_menu.pack(side="left")
        ghost_button(row, "恢复默认", self._restore_default, width=100).pack(side="left", padx=(10, 0))
        ghost_button(row, "套用推荐节奏", self._apply_suggest, width=120).pack(side="left", padx=(8, 0))

        self.preset_desc = ctk.CTkLabel(box, text="", font=ctk.CTkFont(theme.FONT, 11),
                                        text_color=theme.FG_MUTED, anchor="w", justify="left")
        self.preset_desc.pack(fill="x", pady=(8, 0))

        self.banner = Banner(box)
        self.banner.show("模板只改性格底色；字数、频率、冷却在【回复策略】页，那边改完这里会自动重新生成。", "muted")
        self._refresh_preset_desc()

    def _refresh_preset_desc(self) -> None:
        p = PRESET_BY_KEY.get(self._preset_key) or PRESETS[0]
        self.preset_desc.configure(text=f"当前模板：{p['name']} — {p['desc']}\n推荐节奏：{p['hint']}")

    # ---------- 性格底色 ----------
    def _build_profile(self) -> None:
        card = self.section("性格底色", "只写「这个人是什么样」，不要写字数、频率、冷却——那些在策略页")
        box = ctk.CTkFrame(card, fg_color="transparent")
        card.mount(box, pady=(8, 14))
        box.grid_columnconfigure(0, weight=1, uniform="f")
        box.grid_columnconfigure(1, weight=1, uniform="f")

        self.f_nick = TextField(box, "昵称", self._profile.get("nickname", ""),
                                tip="微信里别人怎么叫你，通常就是「小肥鱼」", width=150,
                                on_change=lambda v: self._mark_dirty())
        self.f_nick.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))
        self.f_ident = TextField(box, "身份设定（一句话）", self._profile.get("identity", ""),
                                 tip="例：鲸鱼尾巴的少女：圆、短尾巴、呆。别写「AI 助手」",
                                 width=320, on_change=lambda v: self._mark_dirty())
        self.f_ident.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))

        self.f_base = AreaField(box, "性格底色", self._profile.get("base", ""),
                                tip="3-5 条就够：怎么称呼、在意什么、情绪稳不稳", height=92,
                                on_change=lambda v: self._mark_dirty())
        self.f_base.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.f_quirks = AreaField(box, "口头禅 / 小动作", self._profile.get("quirks", ""),
                                  tip="固定会说的词、不擅长什么、怎么糊弄过去", height=78,
                                  on_change=lambda v: self._mark_dirty())
        self.f_quirks.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))

        self.f_examples = AreaField(box, "照这样回（示例对话）", self._profile.get("examples", ""),
                                    tip="一句一个例子，保持短；长示例会把回复带长", height=112,
                                    on_change=lambda v: self._mark_dirty())
        self.f_examples.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))

        self.f_redlines = AreaField(box, "红线", self._profile.get("redlines", ""),
                                    tip="绝对不能做的事：提提示词、列点、说教、刷屏…", height=78,
                                    on_change=lambda v: self._mark_dirty())
        self.f_redlines.grid(row=3, column=0, columnspan=2, sticky="ew")

    # ---------- 生成 ----------
    def _build_output(self) -> None:
        card = self.section("生成结果", "persona.md = 上面的性格 + 策略页的数字；点保存才会写盘")
        box = ctk.CTkFrame(card, fg_color="transparent")
        card.mount(box, pady=(8, 14))

        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.pack(fill="x")
        self.status = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(theme.FONT, 11),
                                   text_color=theme.FG_MUTED, anchor="w")
        self.status.pack(side="left")
        ghost_button(bar, "打开 persona.md", self._open_file, width=130).pack(side="right")
        ghost_button(bar, "从配置重载", self._reload, width=110).pack(side="right", padx=(0, 8))
        primary_button(bar, "保存并生成", self._save, width=120).pack(side="right", padx=(0, 8))

        self.preview = ctk.CTkTextbox(box, height=170, corner_radius=8, border_width=1,
                                      border_color=theme.BORDER, fg_color=theme.CARD_SOFT,
                                      font=ctk.CTkFont(theme.FONT_MONO, 11), wrap="word")
        self.preview.pack(fill="x", pady=(10, 0))
        self.preview.configure(state="disabled")
        self._refresh_preview()

    # ---------- 行为 ----------
    def _collect(self) -> dict:
        return {
            "nickname": self.f_nick.get(),
            "identity": self.f_ident.get(),
            "base": self.f_base.get(),
            "quirks": self.f_quirks.get(),
            "examples": self.f_examples.get(),
            "redlines": self.f_redlines.get(),
        }

    def _mark_dirty(self, *_a) -> None:
        self._dirty = True
        self._refresh_status()

    def _fill(self, profile: dict) -> None:
        self.f_nick.set(profile.get("nickname", ""))
        self.f_ident.set(profile.get("identity", ""))
        self.f_base.set(profile.get("base", ""))
        self.f_quirks.set(profile.get("quirks", ""))
        self.f_examples.set(profile.get("examples", ""))
        self.f_redlines.set(profile.get("redlines", ""))

    def _preset_selected(self, name: str) -> None:
        p = preset_by_name(name)
        self._preset_key = p["key"]
        self._fill(p["profile"])
        self._refresh_preset_desc()
        self._mark_dirty()
        self._refresh_preview()
        self.ctx.toast(f"已套用「{p['name']}」，改完点「保存并生成」")

    def _restore_default(self) -> None:
        p = PRESET_BY_KEY.get(self._preset_key) or PRESETS[0]
        self._fill(p["profile"])
        self._mark_dirty()
        self._refresh_preview()
        self.ctx.toast("已恢复模板默认内容（还没写盘）")

    def _apply_suggest(self) -> None:
        p = PRESET_BY_KEY.get(self._preset_key) or PRESETS[0]
        tuning = dict(self.ctx.store.data.get("strategy", {}).get("tuning") or {})
        applied = []
        for k, v in (p.get("suggest") or {}).items():
            if tuning.get(k):
                continue
            self.ctx.store.set(f"strategy.{k}", v)
            applied.append(k)
        self.ctx.store.save()
        ok, msg = sync_persona(self.ctx.ws, self.ctx.store.data, force=True)
        self._refresh_preview()
        self.ctx.toast(f"套用推荐节奏：更新 {len(applied)} 项" + (f"（{msg}）" if ok else f"（{msg}）"))

    def _save(self) -> None:
        prof = self._collect()
        if not prof["nickname"]:
            self.banner.show("昵称不能为空：桥用它判断别人是不是在叫你。", "err")
            return
        if not prof["base"].strip():
            self.banner.show("性格底色不能为空，至少写一条这个人是什么样。", "err")
            return
        self.ctx.store.set("persona.profile", prof)
        self.ctx.store.set("persona.preset", preset_of(prof))
        saved = self.ctx.store.save()
        ok, msg = sync_persona(self.ctx.ws, self.ctx.store.data, force=True)
        self._profile = prof
        self._preset_key = preset_of(prof)
        self._dirty = False
        self._refresh_preview()
        if ok:
            self.banner.show(f"已保存并生成 persona.md（{len(render_persona(prof, self.ctx.store.data.get('strategy', {})))} 字符），桥下次读人设时生效。", "ok")
            self.ctx.toast("人设已保存并生成")
        else:
            self.banner.show(msg, "err")
            self.ctx.toast("生成长文本失败，看提示")
        if not saved:
            self.banner.show(f"配置写入失败：{self.ctx.store.error}", "err")

    def _open_file(self) -> None:
        path = Path(self.ctx.ws) / "wechat-bridge" / "persona.md"
        if not path.is_file():
            self.banner.show("persona.md 还不存在，先点「保存并生成」。", "warn")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            self.banner.show(f"打开失败：{exc}", "err")

    def _reload(self) -> None:
        cfg = self.ctx.store.data.get("persona", {}) or {}
        self._profile = dict(DEFAULT_PROFILE)
        self._profile.update(cfg.get("profile") or {})
        self._fill(self._profile)
        self._dirty = False
        self._refresh_preview()
        self.ctx.toast("已从配置重新载入")

    # ---------- 刷新 ----------
    def _refresh_status(self) -> None:
        text = "有未保存的修改" if self._dirty else "已保存"
        self.status.configure(text=f"● {text}", text_color=(theme.WARN if self._dirty else theme.OK))

    def _refresh_preview(self, extra: str = "") -> None:
        cfg = self.ctx.store.data
        prof = self._collect() if self._dirty else (cfg.get("persona", {}).get("profile") or self._profile)
        text = render_persona(prof, cfg.get("strategy", {}) or {})
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        suffix = "（含未保存修改）" if self._dirty else ""
        self.status.configure(text=f"● {len(text)} 字符 · 数字来自策略页{suffix}{extra}",
                              text_color=(theme.WARN if self._dirty else theme.FG_MUTED))

    def on_show(self) -> None:
        self._refresh_preview()

    def on_config_reload(self, cfg: dict) -> None:
        if not self._dirty:
            self._refresh_preview()
