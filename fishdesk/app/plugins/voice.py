"""语音扩展槽位：只做接口占位，接 ASR/TTS 时不用改本文件以外的任何地方。"""
from __future__ import annotations

import customtkinter as ctk

from ..ui import theme
from ..ui.widgets import BasePage, EntryRow, SliderRow, SwitchRow
from .registry import register


@register("voice", "语音扩展", order=80, icon="◎", subtitle="预留槽位：配置项已进 config.json，插件注册即可接")
def build(parent, ctx):
    return VoicePage(parent, ctx)


class VoicePage(BasePage):
    key = "voice"

    def __init__(self, parent, ctx):
        super().__init__(parent, ctx)
        v = ctx.cfg.get("voice", {})

        on = self.section("总开关", "关掉时钩子都不会被调用，零开销")
        self.sw_on = SwitchRow(on, "启用语音扩展", v.get("enabled", False),
                               command=lambda x: self.autosave("voice.enabled", x))
        self.sw_on.grid(row=on.body_row, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.sw_in = SwitchRow(on, "语音输入（对方发语音 → 转文字）", v.get("voice_input", False),
                               command=lambda x: self.autosave("voice.voice_input", x))
        self.sw_in.grid(row=on.body_row + 1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.sw_out = SwitchRow(on, "语音输出（文字 → 语音条）", v.get("voice_output", False),
                                command=lambda x: self.autosave("voice.voice_output", x))
        self.sw_out.grid(row=on.body_row + 2, column=0, sticky="ew", padx=16, pady=(4, 14))

        eng = self.section("引擎", "选 none 表示只留接口不加载模型")
        self.engine = ctk.CTkOptionMenu(
            eng, values=["none", "edge-tts", "azure", "whisper-local", "funasr-local"],
            width=220, height=30, font=ctk.CTkFont(theme.FONT, 12),
            fg_color=theme.CARD_SOFT, button_color=theme.SIDEBAR_ACTIVE,
            command=lambda x: self.autosave("voice.voice_engine", x))
        self.engine.set(v.get("voice_engine", "none"))
        self.engine.grid(row=eng.body_row, column=0, sticky="w", padx=16, pady=(10, 6))
        self.entry_asr = EntryRow(eng, "ASR 模型", v.get("asr_model", ""), width=320,
                                  placeholder="例如 small / paraformer-zh",
                                  command=lambda x: self.autosave("voice.asr_model", x))
        self.entry_asr.grid(row=eng.body_row + 1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.entry_tts = EntryRow(eng, "TTS 音色", v.get("tts_voice", ""), width=320,
                                  placeholder="例如 zh-CN-XiaoyiNeural",
                                  command=lambda x: self.autosave("voice.tts_voice", x))
        self.entry_tts.grid(row=eng.body_row + 2, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.vad = SliderRow(eng, "VAD 静音判定", float(v.get("vad_seconds", 1.0)), 0.2, 3.0, 28,
                             unit=" 秒", command=lambda x: self.autosave("voice.vad_seconds", x))
        self.vad.grid(row=eng.body_row + 3, column=0, sticky="ew", padx=16, pady=(4, 14))

        how = self.section("怎么接进来", "接口已经留好，写一个模块就够了")
        ctk.CTkLabel(
            how, anchor="w", justify="left", wraplength=820, font=ctk.CTkFont(theme.FONT, 12),
            text_color=theme.FG_MUTED,
            text=("1. 写 app/plugins/voice_impl.py（或独立包），在里面调 ctx.hooks.register('on_message_received', fn)。\n"
                  "2. 钩子 fn(payload) 里若消息是语音，下载→ASR→把 payload['text'] 换掉返回即可，"
                  "桥侧只认最终文本，无需改 do_reply。\n"
                  "3. 输出方向注册 on_voice_output：拿到要发的文本，生成音频文件后塞进 payload['audio']，"
                  "由桥的发送层决定走文件还是语音条。\n"
                  "4. 以上都不需要改 wechat_bridge.py 的主流程——它只在收/发两个位置 fire 一次钩子。"),
        ).grid(row=how.body_row, column=0, sticky="ew", padx=16, pady=(12, 14))
