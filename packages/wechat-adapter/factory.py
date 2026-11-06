"""适配层工厂：按名字取实现，换版本只改这里 + 新实现文件。"""
from __future__ import annotations

import os

try:
    from .base import AdapterError, WeChatAdapter
except ImportError:  # pragma: no cover
    from base import AdapterError, WeChatAdapter


def get_adapter(name=None) -> WeChatAdapter:
    """name 默认取环境变量 WECHAT_ADAPTER，再退回 wx4。"""
    key = (name or os.environ.get("WECHAT_ADAPTER") or "wx4").strip().lower()
    if key in ("wx4", "wechat4", "4.x"):
        try:
            from .wx4 import Wx4Adapter
        except ImportError:
            from wx4 import Wx4Adapter

        return Wx4Adapter()
    if key in ("wx3", "3.9", "pywxauto"):
        raise AdapterError("wx 3.9 适配层尚未实现（预留 pywxauto 实现位）")
    raise AdapterError("未知适配层: %r" % key)


__all__ = ["AdapterError", "WeChatAdapter", "get_adapter"]
