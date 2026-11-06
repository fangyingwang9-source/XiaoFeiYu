"""wechat-adapter：微信客户端操作的可替换适配层。"""
from __future__ import annotations

try:
    from .base import AdapterError, ProbeResult, WeChatAdapter
    from .factory import get_adapter
except ImportError:  # pragma: no cover
    from base import AdapterError, ProbeResult, WeChatAdapter
    from factory import get_adapter

__all__ = ["AdapterError", "ProbeResult", "WeChatAdapter", "get_adapter"]
