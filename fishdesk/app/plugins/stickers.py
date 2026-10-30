"""表情库页：情绪筛选 + 首帧缩略图。数据源是 stickers/meta.json 与 tags25.json。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from .. import paths
from ..ui import theme
from ..ui.widgets import BasePage, ghost_button
from .registry import register

THUMB = 104
PAGE_SIZE = 24


@register("stickers", "表情库", order=40, icon="☺", subtitle="322 张表情 · 按情绪筛选 · 点击看用法")
def build(parent, ctx):
    return StickerPage(parent, ctx)


class StickerPage(BasePage):
    key = "stickers"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        self.dir = paths.stickers_dir(self.ctx.ws)
        self._cache: dict[str, ctk.CTkImage] = {}
        self._all: list[dict] = []
        self._filtered: list[dict] = []
        self._shown = 0

        self._load_data()

        flt = self.section("筛选", "精选 25 张是人工标的场景/用法，其余是完整库")
        bar = ctk.CTkFrame(flt, fg_color="transparent")
        bar.grid(row=flt.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        emotions = ["全部"] + sorted({e["emotion"] for e in self._all if e.get("emotion")})
        self.emo_menu = ctk.CTkOptionMenu(bar, values=emotions, width=130, height=30,
                                          font=ctk.CTkFont(theme.FONT, 12),
                                          fg_color=theme.CARD_SOFT, button_color=theme.SIDEBAR_ACTIVE,
                                          command=lambda _v: self._apply_filter())
        self.emo_menu.set("全部")
        self.emo_menu.pack(side="left")
        self.kw = ctk.CTkEntry(bar, width=220, height=30, placeholder_text="搜关键词 / 用法…",
                               font=ctk.CTkFont(theme.FONT, 12),
                               fg_color=theme.CARD_SOFT, border_color=theme.BORDER)
        self.kw.pack(side="left", padx=(8, 0))
        self.kw.bind("<KeyRelease>", lambda e: self._apply_filter())
        self.pure = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(bar, text="只看精选", variable=self.pure, width=44,
                      progress_color=theme.ACCENT, font=ctk.CTkFont(theme.FONT, 12),
                      command=self._apply_filter).pack(side="left", padx=(14, 0))
        ghost_button(bar, "打开目录", self._open_dir, width=100).pack(side="right")
        self.count = ctk.CTkLabel(flt, text="", font=ctk.CTkFont(theme.FONT, 11),
                                  text_color=theme.FG_MUTED, anchor="w")
        self.count.grid(row=flt.body_row + 1, column=0, sticky="ew", padx=16, pady=(0, 14))

        wall = self.section("表情墙", "缩略图取 GIF 首帧，点击看场景与用法")
        self.gridbox = ctk.CTkFrame(wall, fg_color="transparent")
        self.gridbox.grid(row=wall.body_row, column=0, sticky="ew", padx=10, pady=(8, 4))
        for c in range(6):
            self.gridbox.grid_columnconfigure(c, weight=1, uniform="tile")
        foot = ctk.CTkFrame(wall, fg_color="transparent")
        foot.grid(row=wall.body_row + 1, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.more_btn = ghost_button(foot, "加载更多", self._more, width=100)
        self.more_btn.pack(side="left")
        self.info = ctk.CTkLabel(wall, text="", font=ctk.CTkFont(theme.FONT, 11),
                                 text_color=theme.FG_MUTED, anchor="w", justify="left",
                                 wraplength=820)
        self.info.grid(row=wall.body_row + 2, column=0, sticky="ew", padx=16, pady=(0, 14))
        self._apply_filter()

    # ---------- 数据 ----------
    def _load_data(self) -> None:
        meta_path = self.dir / "meta.json"
        tags_path = self.dir / "tags25.json"
        meta = self._read_json(meta_path)
        tags = self._read_json(tags_path)
        files_dir = self.dir / "files"

        pure_ids = set()
        for item in (tags or {}).get("stickers", []):
            f = item.get("file", "")
            p = self.dir / f
            if not p.is_file():
                continue
            pure_ids.add(p.name)
            self._all.append({
                "path": p,
                "title": item.get("label") or p.stem,
                "emotion": item.get("emotion") or "",
                "detail": f"{item.get('scene', '')}｜{item.get('when', '')}",
                "tags": [str(item.get("label", "")), str(item.get("scene", ""))],
                "pure": True,
            })

        for item in (meta or {}).get("stickers", []):
            sid = item.get("id", "")
            if "/" not in sid:
                continue
            pack, name = sid.split("/", 1)
            p = files_dir / f"{pack}_{name}.gif"
            if not p.is_file() or p.name in pure_ids:
                continue
            self._all.append({
                "path": p,
                "title": name,
                "emotion": item.get("emotion") or "",
                "detail": item.get("use") or item.get("desc", "")[:120],
                "tags": [str(t) for t in item.get("tags", [])] + [sid],
                "pure": False,
            })

    @staticmethod
    def _read_json(p: Path) -> dict:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ---------- 筛选 ----------
    def _apply_filter(self, *_a) -> None:
        emo = self.emo_menu.get()
        kw = self.kw.get().strip().lower()
        only_pure = bool(self.pure.get())
        out = []
        for e in self._all:
            if only_pure and not e["pure"]:
                continue
            if emo != "全部" and e["emotion"] != emo:
                continue
            if kw:
                hay = " ".join([e["title"], e["emotion"], e["detail"]] + e["tags"]).lower()
                if kw not in hay:
                    continue
            out.append(e)
        self._filtered = out
        self._shown = 0
        for w in self.gridbox.winfo_children():
            w.destroy()
        self._more()

    def _more(self) -> None:
        end = min(len(self._filtered), self._shown + PAGE_SIZE)
        for e in self._filtered[self._shown:end]:
            tile = ctk.CTkFrame(self.gridbox, fg_color=theme.CARD_SOFT, corner_radius=10)
            r, c = divmod(self._shown, 6)
            tile.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
            img = self._thumb(e["path"])
            if img is None:
                lbl = ctk.CTkLabel(tile, text="读取失败", font=ctk.CTkFont(theme.FONT, 10),
                                   text_color=theme.WARN)
                lbl.pack(padx=6, pady=30)
            else:
                btn = ctk.CTkButton(tile, text="", image=img, width=THUMB, height=THUMB,
                                    fg_color="transparent", hover_color=theme.SIDEBAR_ACTIVE,
                                    command=lambda x=e: self._show_info(x))
                btn.pack(padx=4, pady=(6, 2))
            cap = ctk.CTkLabel(tile, text=(e["title"][:10] or e["emotion"]),
                               font=ctk.CTkFont(theme.FONT, 10), text_color=theme.FG_MUTED)
            cap.pack(pady=(0, 6))
            self._shown += 1
        self.count.configure(text=f"共 {len(self._filtered)} 张，已显示 {self._shown} 张"
                                 f"（全库 {len(self._all)} 张）")
        self.more_btn.configure(state=("normal" if self._shown < len(self._filtered) else "disabled"))

    def _thumb(self, path: Path) -> ctk.CTkImage | None:
        key = str(path)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            im = Image.open(path)
            im.seek(0)
            im = im.convert("RGBA")
            im.thumbnail((THUMB, THUMB), Image.LANCZOS)
            image = ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
        except Exception:
            return None
        if len(self._cache) > 400:
            self._cache.clear()
        self._cache[key] = image
        return image

    def _show_info(self, e: dict) -> None:
        self.info.configure(text=f"{e['title']}｜{e['emotion']}｜{e['detail']}｜文件：{e['path'].name}")

    def _open_dir(self) -> None:
        try:
            os.startfile(str(self.dir))
        except Exception as exc:
            self.ctx.toast(f"打开失败：{exc}")

    def on_show(self) -> None:
        pass
