"""插件钩子总线。

消息管道与 UI 共用一套命名：
  on_message_received / on_message_sent   —— 桥的消息流（语音中间件挂这里）
  on_voice_input / on_voice_output       —— 语音扩展（本版仅占位）
  on_config_reload / on_persona_saved    —— 配置与人设变更
钩子函数可返回 dict 覆盖 payload，实现「中间件改写」。
"""
from __future__ import annotations

import time
from typing import Any, Callable

HOOK_NAMES = (
    "on_message_received",
    "on_message_sent",
    "on_voice_input",
    "on_voice_output",
    "on_config_reload",
    "on_persona_saved",
)


class HookBus:
    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[str, Callable[..., Any | None]]]] = {n: [] for n in HOOK_NAMES}
        self.events: list[str] = []

    def register(self, name: str, fn: Callable[..., Any | None], label: str = "") -> None:
        self._hooks.setdefault(name, []).append((label or getattr(fn, "__name__", "hook"), fn))

    def unregister(self, name: str, label: str) -> None:
        self._hooks[name] = [h for h in self._hooks.get(name, []) if h[0] != label]

    def fire(self, name: str, **payload) -> dict:
        out = dict(payload)
        for label, fn in list(self._hooks.get(name, [])):
            try:
                res = fn(**out)
                if isinstance(res, dict):
                    out.update(res)
            except Exception as exc:
                self.events.append(f"{time.strftime('%H:%M:%S')} {name}/{label} 失败: {exc}")
        self.events = self.events[-100:]
        return out

    def summary(self) -> list[str]:
        return [f"{n}: {len(v)}" for n, v in self._hooks.items() if v]
