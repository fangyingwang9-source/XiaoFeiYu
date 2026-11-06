"""耳朵 -> 桥 的语音转写信箱。

VoiceListener（listener.py）把每条识别结果追加到 out/voice-in.jsonl；
桥主循环收到一条语音消息后，用 (会话, 时间戳) 在这里等对应那一行，
拿到文字就当这条消息的正文送进 do_reply —— 不是打印一条旁路日志就算完。
"""
from __future__ import annotations

import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "out")
LOG_PATH = os.path.join(OUT_DIR, "voice-in.jsonl")

# 语义：转写行的 ts 是微信里那条语音的 create_time；桥拿到消息的 ts 也是同一个值，
# 但两个监听器有先后，给一点前后容差（语音先落库、桥后扫到）。
WINDOW_BEFORE = 10
WINDOW_AFTER = 300

_USED: list = []


def read_records(log_path: str = LOG_PATH, limit: int = 40) -> list:
    """读最近的识别记录（只要成功且有文字的），旧的在前。"""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("ok") and str(rec.get("text") or "").strip():
            out.append(rec)
    return out


def _match(rec: dict, chat: str) -> bool:
    rc = str(rec.get("chat") or "")
    return bool(rc) and (rc == chat or rc in chat or chat in rc)


def wait_text(chat: str, ts: int, timeout: float = 15.0,
              log_path: str = LOG_PATH, on_log=None) -> str:
    """等这条语音的转写文字；等到就返回并标记已消费，超时返回 ""。"""
    deadline = time.time() + timeout
    chat = str(chat or "")
    ts = int(ts or 0)
    while True:
        near = []
        for rec in read_records(log_path):
            try:
                rts = int(rec.get("ts") or 0)
            except Exception:
                continue
            key = (str(rec.get("chat") or ""), rts)
            if key in _USED:
                continue
            if rts < ts - WINDOW_BEFORE or rts > ts + WINDOW_AFTER:
                continue
            near.append(rec)
            if _match(rec, chat):
                if len(_USED) > 200:
                    del _USED[:100]
                _USED.append(key)
                return str(rec.get("text") or "").strip()
        if time.time() >= deadline:
            if near and on_log:
                on_log("[asr] 有转写但会话对不上（%s≠%s）：群语音暂不支持按群名映射"
                       % (near[-1].get("chat"), chat))
            return ""
        time.sleep(0.5)


def reset_used() -> None:
    del _USED[:]

# ---- 进模型前的清洗：只留「人说的话」，元数据一律剥掉 -------------------------
# 微信语音消息的 content 是整段 XML：<msg><voicemsg voicelength="3958" length="6400"
# aeskey="…" voiceurl="…" …/></msg>。以前直接拿它当正文送进 do_reply，模型就把
# voicelength 当内容读，回一句「六秒呢」「这回才两秒半」——像测速仪一样点评时长。
_XML_TAG_RE = re.compile(r"<[^<>]{0,600}>")
_VOICE_BUBBLE_RE = re.compile(
    r"[\[（(【]\s*(?:语音|音频|视频|图片|动画表情|表情|文件|链接)\s*[\]）)】]"
    r"|(?:语音|音频|时长)\s*[:：]?\s*\d+(?:\.\d+)?\s*[”\"']?\s*秒?")


def sanitize_user_text(text: str) -> str:
    """把一条消息洗成「纯人话」：去 XML 标签、去时长/格式占位、压空白。

    只剥元数据形态的东西——正常说的「你发个语音」「等三秒」不会被误删
    （方括号占位和「语音+数字+秒」才删）。
    """
    t = str(text or "")
    if not t:
        return ""
    if "<" in t and ">" in t:
        t = _XML_TAG_RE.sub(" ", t)
    t = _VOICE_BUBBLE_RE.sub(" ", t)
    t = re.sub(r"[\[（(【]\s*[\]）)】]", " ", t)   # 占位删掉后剩下的空括号
    t = re.sub(r"[ \t\r\f\v]{2,}", " ", t)
    return t.strip()

