"""插件注册表：页面 = 名称 + 图标 + 工厂函数，主窗口只认注册表。

主窗口不认识任何具体页面，所以加页面（包括以后的语音页）零改动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

UiFactory = Callable[[Any, Any], Any]  # (parent, ctx) -> CTkFrame


@dataclass
class PageSpec:
    key: str
    title: str
    factory: UiFactory
    order: int = 100
    icon: str = "•"
    subtitle: str = ""
    condition: Callable[[Any], bool] | None = None
    tags: list[str] = field(default_factory=list)


_REGISTRY: dict[str, PageSpec] = {}


def register(
    key: str,
    title: str,
    order: int = 100,
    icon: str = "•",
    subtitle: str = "",
    condition: Callable[[Any], bool] | None = None,
    tags: list[str] | None = None,
):
    def deco(fn: UiFactory) -> UiFactory:
        _REGISTRY[key] = PageSpec(
            key=key,
            title=title,
            factory=fn,
            order=order,
            icon=icon,
            subtitle=subtitle,
            condition=condition,
            tags=list(tags or []),
        )
        return fn

    return deco


def all_pages() -> list[PageSpec]:
    return sorted(_REGISTRY.values(), key=lambda p: (p.order, p.key))


def pages_for(cfg: Any) -> list[PageSpec]:
    out = []
    for p in all_pages():
        try:
            if p.condition is None or p.condition(cfg):
                out.append(p)
        except Exception:
            out.append(p)
    return out
