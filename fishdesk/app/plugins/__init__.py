"""页面插件注册表。

新增页面只需三步：
  1) 在本目录新建 xxx.py，里面写一个 @register(...) 装饰的工厂函数；
  2) 把模块名加进下面的 PLUGIN_MODULES；
  3) 重启程序（或开发态刷新）。
语音页就是照这个约定预留的槽位（plugins/voice.py）。
"""
from __future__ import annotations

import importlib

from .registry import PageSpec, register, pages_for, all_pages  # noqa: F401

PLUGIN_MODULES = [
    "console",
    "persona",
    "strategy",
    "stickers",
    "schedule",
    "advanced",
    "voice",
]

_LOADED = False


def load_plugins() -> list[str]:
    global _LOADED
    if _LOADED:
        return []
    errors: list[str] = []
    for name in PLUGIN_MODULES:
        try:
            importlib.import_module(f"{__name__}.{name}")
        except Exception as exc:  # 单个页面坏了不能拖垮整个壳
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    _LOADED = True
    return errors
