"""桥的扩展钩子（语音等后续功能挂这里）。

桥每轮循环都会 import 本模块；下面这些函数名就是钩子点，按需实现，函数可以不存在。

约定：
  * 每个钩子可以返回 dict，桥会把 dict 合并进 payload（目前桥只用 received/sent 做事件通知）。
  * 钩子内抛异常只会写日志，不影响收发。
  * 想读配置就 load_config()，别 import wechat_bridge（会二次执行主模块）。

已实现的钩子点：
  on_message_received(chat, sender, text, raw, is_group)  # 收到一条非自己发的消息
  on_message_sent(chat, to, text, kind, local_id)         # 文本确认送达后
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def on_message_received(chat: str, sender: str, text: str, raw: dict, is_group: bool):
    """语音输入接入点：可在 raw 上取媒体，转文字后再决定是否影响回复（当前桥按纯事件处理）。"""
    return None


def on_message_sent(chat: str, to: str, text: str, kind: str, local_id: int):
    """语音输出接入点：对 bot 发出的每条消息做 TTS 或其它副作用。"""
    return None
