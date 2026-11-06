"""微信 UI 适配层接口（可替换）。

上层业务（TTS、语音发送编排）只依赖本文件的接口，不直接碰 wxui / uiautomation /
微信内部控件。将来换微信版本（4.x -> 3.9 + pywxauto）或改注入方式，只需要再写一个
WeChatAdapter 子类并在 factory 里注册。

约定：
- 适配层只负责"怎么点/怎么按/怎么判断状态"，不负责生成或解码音频。
- 语音发送拆成 voice_start / voice_stop / voice_cancel 三步，中间由上层播放音频，
  这样上层不需要知道是"按住右 Alt"还是"长按鼠标"。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class AdapterError(RuntimeError):
    """适配层失败：窗口拉不起来、置前失败、录音态没起来等。"""


@dataclass
class ProbeResult:
    adapter: str
    ok: bool
    hwnd: int = 0
    rect: tuple = ()
    recording: bool = False
    detail: str = ""
    extra: dict = field(default_factory=dict)


class WeChatAdapter(ABC):
    """微信客户端操作的最小接口集合。"""

    name = "base"

    @abstractmethod
    def probe(self) -> ProbeResult:
        """只读探测：窗口是否在、是否有尺寸、是否处在录音态。"""

    @abstractmethod
    def ensure_ready(self) -> dict:
        """确保主窗口存在、可见且被置于前台；失败抛 AdapterError。"""

    @abstractmethod
    def open_chat(self, who: str) -> None:
        """打开与 who 的会话（已在当前会话时应保持不动）。"""

    @abstractmethod
    def send_text(self, who: str, text: str) -> None:
        """发送一条文本消息。"""

    @abstractmethod
    def voice_start(self) -> bool:
        """进入录音态（按住说话）。实现自行负责置前/聚焦/按键方式差异。"""
        """进入录音态并保持。返回是否确认进入录音态。"""

    @abstractmethod
    def voice_stop(self) -> None:
        """结束录音并触发发送。"""

    @abstractmethod
    def voice_cancel(self) -> None:
        """放弃当前录音（不发送），并保证回到正常聊天态。"""

    @abstractmethod
    def recording(self) -> bool:
        """当前是否处在录音态（用于轮询）。"""

    @abstractmethod
    def wait_voice_sent(self, timeout: float = 8.0) -> bool:
        """等待松开后的发送动作完成；返回是否确认出现新的语音气泡（细节放 self.last_send）。"""

    def iter_voice_messages(self, stop_event: threading.Event | None = None, poll_seconds: float = 0.8):
        """纯后台监听收到的语音消息，逐条 yield VoiceMessage。

        只读文件系统/微信缓存，**不做任何前台操作**（不置前、不点鼠标、不发按键）。
        不支持的适配实现默认抛 AdapterError。
        """
        raise AdapterError(f"{self.name} 适配层不支持语音接收")

    def display_name(self, wxid: str) -> str:
        """把 wxid 换成人类可读的名字（群聊标发言人用）；取不到就原样返回。"""
        return str(wxid or "")

    def newest_voice_clip(self, chat: str, since_ts: float, wait_s: float = 5.0):
        """回读`since_ts`之后该会话最新一条已发出语音的音频：{path, ts, size}，取不到返回 None。

        只读微信缓存/数据库，用于确认「气泡里到底有没有声音」。
        """
        return None


@dataclass
class VoiceMessage:
    """收到的一条语音：微信缓存里的原始音频文件 + 可选的会话/时长信息。"""

    path: str
    chat: str = ""
    duration_s: float = 0.0
    ts: float = 0.0
    size: int = 0
    source: str = ""
    head_hex: str = ""
