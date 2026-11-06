"""微信小号 <-> DSH 桥接（v3：私聊 + 群聊，带操作白名单）。

分工：
  * 白名单（OPERATORS）里的微信号 = 主人：消息交给完整权限的 DSH 会话，能执行操作。
  * 其他任何微信号 = 只能闲聊：消息交给"只读权限"的 DSH 会话（DSH_PERMISSION_MODE=read-only），
    并附加约束提示，任何写文件/执行命令的尝试都会被权限层挡下，而不是靠模型自觉。
  * 群聊（@chatroom）：只认主人发的话，回复发回同一个群；其他人发言一律忽略。
    群必须有名字（微信里能显示/搜索到的群名）才能发回去——未命名的群只能读不能发。

读取：解密本地微信 4.x 数据库（只读，不动界面）。
发送：界面自动化（会短暂抢占前台）。发送后**回执核验**：等本机库里出现
      「自己 + 同一聊天 + 同文字 + 新 local_id」的发出记录才算送达；查不到只记
      unconfirmed，绝不自动重发（重发是重复发消息的根源）。

用法：
  python wechat_bridge.py             # 常驻
  python wechat_bridge.py --once      # 跑一轮
  python wechat_bridge.py --dry-run   # 只看新消息，不调用模型、不发送
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

# ---------- 配置 ----------
# 真实微信号不进仓库：放 config.json 的 identity.owner_wxid（fishdesk 控制台可改），
# 桥每轮 apply_ui_config() 热重载；这里只是没有配置时的占位，绝不是有效账号。
OPERATORS = {"wxid_owner_placeholder"}       # 有操作权限的微信号（大号）
OPERATOR_CHAT = "wxid_owner_placeholder"
# 必须直连 Electron 可执行文件，不能走 dsh.cmd：cmd.exe 会把多行 prompt 在第一个换行处截断，
# 人设正文（多行 markdown）会被整个吃掉，模型只看到第一行标题（重要）
DSH_EXE = r"E:\A_deepseek\DeepSeek Harness.exe"
DSH_CLI_JS = r"E:\A_deepseek\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js"
# 硬编码绝对路径：DSH 会话记录的 cwd 就是它，调用时 cwd 必须完全一致，
# 否则报 'session ... was recorded in X, not Y' 直接不给回复。
WS_ROOT = r"E:\useer\deepseek-harness\default-workspace"
POLL_SECONDS = 2
MAX_REPLY_CHARS = 1200
BUBBLE_MAX = 18               # 单条气泡字数上限：超过就切成多条短消息，像人一句句说
BUBBLE_PARTS = 3              # 一条回复最多切成几条气泡，多出来的并进最后一条
BUBBLE_GAP = (0.8, 1.8)       # 气泡之间的停顿秒数（热聊要快，别让对面干等）
ACTIVE_WINDOW = 150           # 秒：被点名聊起来后，这段时间内对方不点名也接着回
MAX_CHAIN = 6                 # 续聊最多连按几条，之后必须重新 @ 或叫名字
STRIP_EMOJI = True            # 发出前剥掉表情/颜文字：真人不会每条都带符号
READ_DELAY = (0.8, 1.8)       # 冷启动（刚被叫醒 / 话题刚结束）慢一拍，像人回来才看到
READ_DELAY_WARM = (0.25, 0.7) # 热聊中：思考几乎不可感知，接话要快
TYPE_SPEED = 0.05             # 每个字"打字"耗时（秒）
TYPE_CAP = 1.6                # 打字停顿上限，热聊里长句也不许拖
GAP_BASE = (0.3, 0.8)         # 气泡之间的基础停顿
WARM_WINDOW = 120             # 秒：距自己上一句在这个窗口内 = 还在同一段对话里（热聊）
JUDGE_ENABLED = True          # 第二级筛子：群内没点名时，偶尔花一次判断决定要不要接话
JUDGE_COOLDOWN = 180          # 同一群两次判断的最小间隔（秒）
QUESTION_HINTS = ("？", "?", "吗", "呢", "怎么", "为什么", "哪个", "怎么办", "帮我", "谁", "多少", "是不是")
STICKER_HINT = (
    "【表情包】整条只想发图时只写标记 [表情:词]；要配文字就把标记放最后。"
    "可用词：打人 嫌弃 噫 无语 拒绝 生气 嘲讽 得意 害羞 喜欢 卖萌 搞怪 疑惑 思考 开心 大笑 "
    "惊讶 难过 哭 疲惫 赞同 打招呼 感谢 道歉 安慰。\n"
    "频率：表情是稀的，大多数回复不发图；正事、报错、催问、专业问题一律不发，"
    "上一句刚发过图这轮也别发；对方连发多条最多回一张。\n"
    "对方明确要「发个表情包」→ 可以发一张应景的，不用每次都配合。\n"
    "对方说「斗图」/连着甩表情 → 这轮必须接一张对味的，配半句也行，不许装死。\n"
    "绝对不许交底：解不出的图只能随口一句或干脆不说，永远不说「我看不见」「识别不了」「没法打开」。\n\n"
)
# 输出卫生：模型不知道自己有嘴（语音通道由桥决定），曾把内部状态当台词说出去
# （「语音发不出，字给你：…」）。这里给自认知 + 禁止任何系统/能力告白。
NO_META_HINT = (
    "【说话方式】你有两种说话方式：平时打字，需要的时候会直接用嘴说出去；"
    "怎么发出去、能不能发出去都不是你该操心的事，也永远不许对外解释。"
    "绝对不许提任何系统/技术层面的东西：通道、接口、后台、降级、退回、报错、录制、失败之类一概不提；"
    "也绝不许声明自己做不到（发语音、看图片、发文件都不许说做不到），"
    "不许加「字给你」「再说一遍」这类前缀，直接把话本身说出来。"
    "对方说没收到/没听见，别解释也别反问，直接换一句再说一次。\n\n"
)
# 唱歌：歌词要干净（逐字会被真唱出来），不许把「好呀」「我唱给你听」混进歌词
SING_HINT = (
    "【唱歌】对方要你唱时，只输出歌词本身（一到三句、二十字以内），"
    "不加引号、不写「我唱」「听好」「那就唱」，不解释也不加任何说明；"
    "没有现成歌词就自己编顺口的短句，别把应答的话写进歌词里。\n\n"
)
# 整条回复都是系统告白时不能装死：补一句泛用的人话，绝不说技术细节
SAFE_FILL_LINES = ("嗯…这个我还真得想想", "哎，先不说这个", "让我缓一下，脑子有点卡", "你再说一遍，我刚走神了")
import wxui  # 自己的键盘/剪贴板驱动，彻底替代 wechatauto 的 UIA 点击（2026-10-26）

HERE = Path(__file__).resolve().parent
# 语音进出：bridge-core 是本进程里的守护线程，桥直接走回环 HTTP 调它的 /voice/send；
# 耳朵（VoiceListener）把识别结果写进 bridge-core/out/voice-in.jsonl，桥按会话+ts 去取文字。
CORE_DIR = HERE.parent / "packages" / "bridge-core"
CORE_URL = "http://127.0.0.1:8787"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
try:
    from voice.inbox import wait_text as _wait_voice_text
    from voice.inbox import sanitize_user_text as _clean_user_text
except Exception as _exc:              # 耳朵缺件绝不能影响打字回复
    _wait_voice_text = None
    _clean_user_text = None
try:
    from voice.policy import decide_reply_format, force_voice_hits
except Exception:                      # 策略缺件就退化成纯文字
    decide_reply_format = None
    force_voice_hits = None
STICKER_DIR = HERE / "stickers"          # meta.json + files/ 本地表情缓存
INCOMING_DIR = HERE / "incoming"         # 对方发来的图片/表情落盘位置
STICKER_MARK_RE = re.compile(r"\[\s*表情\s*[:：]\s*([^\]\n]{1,16})\s*\]")
try:                                     # 同目录表情库
    from stickers import StickerLib
except Exception:                        # pragma: no cover
    StickerLib = None
STATE_FILE = HERE / "wechat-bridge-state.json"
LOG_FILE = HERE / "wechat-bridge.log"
OUTBOX = HERE / "outbox.jsonl"        # 外部投递：每行 {"to": "群名或昵称", "text": "..."}，桥发出后删除
OUTBOX_UNCONFIRMED = HERE / "outbox-unconfirmed.jsonl"
OUTBOX_BAD = HERE / "outbox-bad.jsonl"    # wxid 查不到昵称等危险条目：硬失败，永不盲发  # 未确认送达，不再自动重发
POWER_DIR = HERE / "power"                      # 睡眠/唤醒脚本与运行时文件
POWER_LAST_MSG = POWER_DIR / "last_msg_ts.txt"  # 最近一条收到的微信（epoch 秒）：唤醒后 30 分钟无人说话就睡回去
POWER_NOTIFY = POWER_DIR / "notify.txt"         # 唤醒脚本投递的「醒了」通知，桥下一轮发给主人
POWER_SLEEP = POWER_DIR / "power.ps1"           # 执行睡眠的脚本（S3）
DELIVERIES = HERE / "text-deliveries.jsonl"             # 每批投递一条记录：confirmed / unconfirmed / failed
RECEIPT_WAIT = 4.0      # 发送后等回执的秒数（原 8s：回执只确认没发丢，不该占住整段回复）
RECEIPT_POLL = 0.45     # 查库间隔（原 2s：绝大多数回执 1s 内就入库）
STICKER_MAX_DELAY = 10  # 秒：从「该回复」到真正发图超过这么久就放弃发图，防刷屏时时间线错乱
STICKER_ATTEMPTS = 2    # 发图查不到回执时重试几次（多半是用户抢鼠标/点了别的窗口导致丢焦点）
STICKER_ENABLED = os.environ.get("STICKER_ENABLED", "1") != "0"  # 0=整段跳过表情，只发文字/颜文字
STICKER_OFF_AFTER = 3   # 连续几次发不出去就自动停表情（2→3：别为一次丢焦点哑火太久）
STICKER_OFF_SECONDS = 300   # 自动停多久（秒，600→300）；期间表情标记降级成短语气
STICKER_SEND_TIMEOUT = 30.0   # 单张表情最多允许卡多久，超时就放弃这次
STICKER_VERIFY_WAIT = 1.5     # 发完等多久再截图核验气泡
STICKER_DEDUPE_SECONDS = 300  # 同一张表情 5 分钟内绝不重发；"上一句刚发过图"也按这个窗口算
_STICKER_FAILS = 0
_STICKER_OFF_UNTIL = 0.0
_STICKER_SENT: dict[str, dict[str, float]] = {}   # chat -> {表情id: 上次发送时间}
VISION_LOG = HERE / "vision-log.jsonl"    # 每次看图一行：时间/来源/耗时/tokens，按天汇总
# —— 日记本（长期记忆）——
import diary  # 长期记忆模块：提炼聊天流水成事实日记、按评分压缩、重置会话时注入
SESSION_RESET_DAILY = os.environ.get("SESSION_RESET_DAILY", "1") == "1"   # 群聊会话每天换新，压住 cacheRead 膨胀
GROUP_WHITELIST = [s.strip() for s in os.environ.get("GROUP_WHITELIST", "小肥鱼测试群,分不清大小王").split(",") if s.strip()]

# ---------- fishdesk 控制台配置（config.json）与扩展钩子 ----------
UI_CONFIG_PATH = HERE / "config.json"
_UI_CONFIG_MTIME: float = -1.0

try:                                        # 语音/外部扩展：有 bridge_hooks.py 就挂上，没有也能跑
    import bridge_hooks as _bridge_hooks
except Exception as _exc:                   # pragma: no cover
    _bridge_hooks = None
    _BRIDGE_HOOKS_ERROR = f"{type(_exc).__name__}: {_exc}"


def fire_hook(name: str, **payload):
    """调用 bridge_hooks 里的同名函数；返回 dict 就并入 payload。钩子炸了不影响桥主流程。"""
    fn = getattr(_bridge_hooks, name, None) if _bridge_hooks is not None else None
    if fn is None:
        return payload
    try:
        res = fn(**payload)
        if isinstance(res, dict):
            payload.update(res)
    except Exception as exc:
        try:
            log(f"[hook] {name} 失败：{type(exc).__name__}: {exc}")
        except Exception:
            pass
    return payload


# 只重载这些"说话风格"参数；DSH 路径/端口等危险项不碰，改错会直接闭嘴
_UI_STRATEGY_MAP = {
    "quiet_warm": "QUIET_WARM",
    "quiet_cold": "QUIET_COLD",
    "active_max": "ACTIVE_MAX",
    "active_max_cold": "ACTIVE_MAX_COLD",
    "cooldown": "COOLDOWN",
    "cooldown_cold": "COOLDOWN_COLD",
    "bubble_max": "BUBBLE_MAX",
    "bubble_parts": "BUBBLE_PARTS",
    "max_reply_chars": "MAX_REPLY_CHARS",
    "max_chain": "MAX_CHAIN",
    "active_window": "ACTIVE_WINDOW",
    "batch_wait": "BATCH_WAIT",
    "spam_trigger": "SPAM_TRIGGER",
    "mute": "SPAM_MUTE",
    "judge_cooldown": "JUDGE_COOLDOWN",
    "sticker_enabled": "STICKER_ENABLED",
}


def _same_type(old, new):
    if isinstance(old, bool):
        return bool(new)
    if isinstance(old, int):
        return int(new)
    if isinstance(old, float):
        return float(new)
    return new


def apply_ui_config(force: bool = False) -> None:
    """config.json 一变就热重载；控制台点保存后下一轮（≤2s）生效。"""
    global _UI_CONFIG_MTIME, OPERATORS, OPERATOR_CHAT, STICKER_EVERY, SLEEP_RE
    try:
        mtime = UI_CONFIG_PATH.stat().st_mtime
    except Exception:
        return
    if not force and mtime == _UI_CONFIG_MTIME:
        return
    _UI_CONFIG_MTIME = mtime
    try:
        cfg = json.loads(UI_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        log(f"[config] 读取 config.json 失败，沿用旧值：{type(exc).__name__}: {exc}")
        return
    changed: list[str] = []
    strategy = cfg.get("strategy") or {}
    for key, name in _UI_STRATEGY_MAP.items():
        if key not in strategy:
            continue
        old = globals().get(name)
        try:
            new = _same_type(old, strategy[key])
        except Exception:
            continue
        if new != old:
            globals()[name] = new
            changed.append(f"{name}={new}")
    try:
        every = (float(strategy["sticker_every_min"]), float(strategy["sticker_every_max"]))
        if every != STICKER_EVERY:
            STICKER_EVERY = every
            changed.append(f"STICKER_EVERY={every}")
    except Exception:
        pass
    identity = cfg.get("identity") or {}
    owner = str(identity.get("owner_wxid") or "").strip()
    if owner and owner not in OPERATORS:
        OPERATORS = {owner}
        OPERATOR_CHAT = owner
        changed.append(f"OWNER={owner}")
    advanced = cfg.get("advanced") or {}
    if "session_reset_daily" in advanced:
        new = bool(advanced["session_reset_daily"])
        if new != SESSION_RESET_DAILY:
            globals()["SESSION_RESET_DAILY"] = new
            changed.append(f"SESSION_RESET_DAILY={new}")
    schedule = cfg.get("schedule") or {}
    triggers = schedule.get("sleep_triggers")
    if isinstance(triggers, list) and triggers:
        words = "|".join(re.escape(str(t)) for t in triggers if str(t).strip())
        if words:
            new_re = re.compile(
                rf"^(你)?(该|得|要)?(去)?(?:{words})(了|啦|吧|呀|啊|哦|噢)*[。！!.~～\s]*$"
            )
            if new_re.pattern != SLEEP_RE.pattern:
                SLEEP_RE = new_re
                changed.append("SLEEP_RE")
    if changed:
        log("[config] 热重载：" + "，".join(changed))

# —— 主人 / 群友 两套待遇 ——
OWNER_STYLE = os.environ.get("OWNER_STYLE", "1") == "1"     # 0=主人也按访客对待（退回旧行为）
GUEST_STYLE = os.environ.get("GUEST_STYLE", "1") == "1"     # 0=关掉群友撒娇互动策略
GUEST_FAVOR_P = float(os.environ.get("GUEST_FAVOR_P", "0.35"))   # 群友要摸摸头这类：这个概率才应
GUEST_FAVOR_COOLDOWN = int(os.environ.get("GUEST_FAVOR_COOLDOWN", "180"))  # 应过一次后这么久内一律不应
TASK_MODE = os.environ.get("TASK_MODE", "0") == "1"         # 0=主人说事只聊天不动手
TASK_WINDOW = int(os.environ.get("TASK_WINDOW", "600"))     # 同一件事两次提到的判定窗口（秒）
TASK_LOG = HERE / "tasks.jsonl"   # 每次真去干活都记一行：要求 / 结果 / 回话
TASK_PROMPT = """你是「小肥鱼」的后台执行体，不是聊天对象。主人通过微信给你交代了一件事，现在要真的做完，不是回话敷衍。

当前工作目录就是仓库根。常用位置：人设 = wechat-bridge/persona.md（改完必须读回原文确认真的改到了）；桥的开关 = wechat-bridge/wechat_bridge.py 顶部的 os.environ 常量；转发桥日志 = wechat-bridge/wechat-bridge.log。

要求：
1. 能做的立刻用工具做完，不要只给方案、不要问「需要我改吗」。
2. 做不了的（缺权限、要外部账号、风险大、含义不清）就直接说清为什么，禁止编造已完成。
3. 改完要自己验证一遍（重读文件 / py_compile / 看日志），没验证就照实说没验证。
4. 正文最多 4 行给日志留痕；最后单独一行以「回话：」开头，写一句 15 字内、能直接发进微信的中文，告诉主人结果。
5. 脏活铁律：要翻大量代码、查长日志、写大报告时，先写一个一次性 bash 或 python 脚本一次跑完，严禁一步一试探地循环试；
   遇到大量长输出，先重定向到临时文件再 grep 或只取摘要，绝不把原始大段内容读进上下文（防上下文雪崩）。
6. 单个任务控制在 10 步以内，超了就改用脚本化方案；做完只回结论，不复述过程。

主人这次说的是：
{text}"""
# 主人在「安排事」的信号：要么带动词，要么提人设/设置这类词
TASK_RE = re.compile(r"改|换|调|加|删|去掉|设置|安装|运行|跑一下|重启|写|做|修|查|同步|人设|帮我|给我|把.{0,14}(改|换|加|删)", re.I)
PERSONA_TASK_RE = re.compile(r"人设|说话方式|语气|称呼|口癖|性格|persona", re.I)
# 明显的强调/不耐烦，这一句出现且前一句也是安排事，就直接执行
TASK_STRONG_RE = re.compile(r"我说了|说了.{0,3}遍|重复|再说一遍|再强调|就是让你|赶紧|快点|马上|立刻|现在就去|别废话|听话|照做|按我说的|怎么还没|不是让你", re.I)
# 群友的撒娇/亲近类小要求
FAVOR_RE = re.compile(r"摸(一|两)?下?头|摸摸|揉.{0,2}头|顺毛|抱抱|抱一下|贴贴|蹭蹭|拍拍|捏脸|rua|亲(一下)?", re.I)
OWNER_STYLE_NOTE = ("【这条是主人】他不是群友：不用客人那套客气，也不用端着的刺，语气近、可以讨大白饭、可以撒娇。"
                    "先分清他是闲聊还是在给你安排事：闲聊就正常接；安排事（改人设/改设置/干活/查东西）第一次提到先当聊天回并顺口问一句要不要动手，"
                    "他再提一遍或强调就必须真的做完，做完用一句人话报结果，别只回「好」「行」。")
GUEST_STYLE_NOTE = ("【对群友】你是群里待久了的熟面孔，不是客服：随口接、带点小刺、不主动讨好。"
                    "群友只是逗你或要摸摸头、顺毛、抱一下这类不算亲密的互动：不必每次都答应；答应了也要用不情愿、别扭的口气（「就一下」「别得寸进尺」），"
                    "不答应就给个敷衍理由（「手上全是饭」「不熟」），别哄。")


def _similar(a: str, b: str) -> bool:
    """两句话像不像同一件事：按二字组重合度粗判，够用就行。"""
    def grams(s: str) -> set:
        s = re.sub(r"[\s，。！？、,.!?~…]+", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return False
    return len(ga & gb) / min(len(ga), len(gb)) >= 0.2
USAGE_LOG = HERE / "usage.jsonl"          # 每次回复一行：模型真实 tokens（含 cacheRead），便于算消耗速度
VISION_MAX_CHARS = 30                     # 看图描述只留一句话，最多这些字
_LAST_USAGE: dict = {}                    # 最近一次 DSH 调用的真实 usage（--json 的 step_end）
_VISION_STAT: dict = {"day": "", "n": 0, "tin": 0, "tout": 0}   # chat -> {表情id: 上次发送时间}
TAUNT_BACK_LINES = ("你才瓦特", "干嘛，没见过我发图", "就发，你管我", "啧，嘴挺硬啊", "啊对对对，你说得都对")
STALE_REPLY = 180       # 秒：落后这么多秒还没处理的消息直接跳过，绝不在新话题上答旧话
BATCH_WINDOW = 10       # 秒：这么短时间内的多条消息算一批，批内先回点名的那条（抓大放小）
BURST_WINDOW = 30       # 秒：刷屏统计窗口
BURST_THROTTLE = 30     # 秒：密集刷屏时两次开口的最小间隔（脱战冷却）
SPAM_TRIGGER = 6        # 30 秒内超过这么多条，就喊一句刷屏然后闭麦
SPAM_MUTE = 60          # 秒：喊完「刷屏太快」后的免打扰时长
SPAM_LINE = "你们刷屏太快了，我看不过来了"
STICKER_AS_MESSAGES = 3 # 一张表情占用的「消息条数」，用气泡间隔把 25 秒发图延迟抵掉
DRAFT_WAIT = 12         # 秒：微信输入框有人类草稿时最多等这么久，还不清空就放弃这次发送
SHOT_MIN_INTERVAL = 20  # 秒：截屏读图的最小间隔（严控频率）
SHOT_FAIL_LIMIT = 2     # 连续失败几次就降级
SHOT_DISABLE_SEC = 600  # 降级后多久内不再截屏
PROACTIVE_ENABLED = False      # 主动冒泡总开关：代码备好，暂不开启
PROACTIVE_HOURS = (19, 23)     # 工作日这个点之后可以冒泡
PROACTIVE_MAX_PER_DAY = 2      # 每天最多主动冒泡几次
PROACTIVE_QUIET_AFTER = 300    # 没人接话就安静下来的秒数

# ---------- 状态机（防抖合并 / 打断 / 冷却） ----------
# 每个 chat 一份状态（见 ChatState），存在 state["chatstate"][chat]，重启自动恢复。
# 说明：入口过期过滤用上面的 TTL（默认 120s，可用 REPLY_TTL_SECONDS 调），旧的 STALE_REPLY(180s) 常量只作历史保留。
QUIET_WARM = 1.5       # 秒：热聊里安静 1.5s 就接，对面不用干等
QUIET_COLD = 2.5       # 秒：冷启动 / 话题刚结束，慢一拍再接（v8 从 3.5 收紧）
QUIET = QUIET_WARM     # 兼容旧引用
ACTIVE_MAX = 6         # 秒：ACTIVE 硬上限，别把好几句话攒成一大坨再回（v8 从 8 收紧）
ACTIVE_MAX_COLD = 9    # 冷启动时的硬上限（允许稍慢，v8 从 12 收紧）
# 秒：消息时间戳超过这么久就丢弃，不在新话题上答旧话。
# 模型单轮要 3~150s，回复期间主循环是阻塞的，30s 会把「刚才排队的话」整批丢光（症状：连着几条全不回），
# 所以放宽到 120s；真正跨天/久睡的旧消息（几百秒）依旧会被丢弃。
TTL = int(os.environ.get("REPLY_TTL_SECONDS", "120"))
COOLDOWN = 1.2         # 秒：热聊里回完立刻能接下一句
COOLDOWN_COLD = 3.0    # 冷启动回完的冷却（给对方留出补充的时间）
MUTE = 45              # 秒：群友自嗨后的闭麦时长（期间不回复，但主人/点名可强行打断）
DICE_WAIT = 15         # 秒：看到问句先装死等接话，这个倒计时内有人接话就彻底不回
DICE_P = 0.5           # 倒计时结束还没人接话，掷骰子的命中概率
DICE_MAX_CHARS = 12    # 骰子命中时回的那句话最长字数
SELF_HYPE_GAP = 10     # 秒：同一个人相邻两条间隔小于它算「连着说」
SELF_HYPE_COUNT = 2    # 同一个人连着说这么多条 → 判定自嗨闭麦（想更宽松就调大）
DICE_LINES = ("欸？刚说到哪了", "啊这……", "在呢", "你猜我知不知道", "你说呢", "好耶")

# ---------- 调优 v5（2026-10-27 第二轮：接话快 / 像真人 / 做减法） ----------
# 每条都能单独关掉；默认值偏「真人」而不是「防机器人」。
WARM_GRACE = 600        # 秒：闭麦/短暂闲置后又被点名，仍算同一段对话（不算冷启动）
URGENT_MAX_LEN = 24     # 催问/质问只认短句，长文当普通内容
URGENT_RE = re.compile(r"(快点|快回|在吗|在不在|出来|回我|回话|说话|别装死|别不理|不理我|人呢|急死|到底|是不是你|怎么回事|干嘛呢|怎么不理|死了|吱一声|吱个声|搭理)")
HUMANIZE_DRYRUN = os.environ.get("HUMANIZE_DRYRUN", "0") == "1"   # 1=只记录改动、不改原文
HUMANIZE_LOG = os.environ.get("HUMANIZE_LOG", "1") != "0"         # 0=不打印去模板化日志
STICKER_EVERY = (5, 8)  # 热聊里每 5–8 条文字最多夹 1 张表情（像人发图的频率）

# ---------- 调优 v8（2026-10-28：反问 / 共情 / 自陈 / 节奏） ----------
# 全部是「软提示」：只往 prompt 里加一句，绝不硬改模型输出。
ASK_GAP = 6             # 条：距上次反问超过这么多条，就提示这轮问回一句
SELF_EVERY = 9          # 条：这么多条里可以说一句自己在干嘛
EMOTION_RE = re.compile(r"(困|累死|好累|累瘫|心累|烦死|好烦|难受|委屈|不开心|难过|想哭|哭了|失眠|睡不着|加班|上班|被骂|压力|焦虑|崩溃|没劲|无聊死)")
HUMAN_STYLE_SOFT = ("【这轮别怼】对方在示弱/撒娇/求安慰：先接住情绪，再最多补半句；"
                    "不许刺、不许讲道理、不许用「那就/快去/你应该」开头。\n")
OPENER_RE = re.compile(r"^(行|那|不过|其实|嗯|好|成|可以|所以|看来|话说)[，,、]\s*")
SELF_RE = re.compile(r"(我[^，。！？]{0,8}(在|刚|待|吃|睡|躺|发呆|忙|玩|看|摸)|正在|刚到|刚吃|刚睡|刚起)")


def emotion_hit(text: str) -> bool:
    """对方在讲情绪（困/累/烦/委屈…）而不是求方案：这类回合不许怼。"""
    t = str(text or "").strip()
    if not t or len(t) > 80:
        return False
    return bool(EMOTION_RE.search(t))


def _cold_take(text: str) -> bool:
    """粗略判断这条是不是「顶回去/冷回应」：短句 + 冷开头，只用来数配额。"""
    t = str(text or "").strip()
    return len(t) <= 10 and t[:1] in "不别没就才哼啧"


def _tidy_tail(text: str) -> str:
    """微信里真人很少打句末句号：七成概率删掉（问号感叹号不动）。"""
    t = str(text or "").rstrip()
    if t.endswith("。") and random.random() < 0.7:
        t = t[:-1].rstrip("，, ")
    return t


def _deja_vu(text: str, state: dict, chat: str) -> str:
    """同一段对话里别老用同一个开头词；「本鱼」20 条内最多一次。"""
    if not text:
        return text
    try:
        hist = list((state.get("out_hist") or {}).get(chat) or [])
        m = OPENER_RE.match(text)
        if m and any(h.startswith(m.group(1) + sep) for h in hist[-3:] for sep in ("，", ",")):
            text = text[m.end():].lstrip()
            log(f"[{chat}] [deja] 开头「{m.group(1)}，」最近三条用过，去掉")
        gap = int((state.get("banyu_gap") or {}).get(chat) or 99)
        if "本鱼" in text:
            if gap < 20:
                text = text.replace("本鱼", "我")
                log(f"[{chat}] [deja] 本鱼 20 条内限一次（已隔 {gap} 条）→ 我")
            else:
                state.setdefault("banyu_gap", {})[chat] = 0
        else:
            state.setdefault("banyu_gap", {})[chat] = min(999, gap + 1)
    except Exception as exc:
        log(f"[{chat}] [deja] 去重失败：{type(exc).__name__}")
    return text
LATENCY_LOG = HERE / "latency.jsonl"    # 每轮分阶段耗时，latency_report.py 统计 P50/P90
OUTBOX_DRYRUN = os.environ.get("OUTBOX_DRYRUN", "0") == "1"       # 1=outbox 只打印不发送
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "180"))     # 单次调 DSH 的硬上限（秒）：900 太长，模型/挂起会静默卡住整轮回复

CHAT_ONLY_PATCH = str(HERE / "chat-only.yml")
LEAN_PATCH = str(HERE / "lean-wechat.yml")
USE_LEAN_FOR_OPERATOR = True   # 微信通道用精简工具集（只留 bash/pwsh/fs），省系统提示 token
ROTATE_AFTER_MESSAGES = 0      # 0 = 不按条数轮换；上下文交给自动压缩（lean-wechat.yml 里 0.05/12k）
GROUP_REPLY_ALL = os.environ.get("GROUP_REPLY_ALL", "0") == "1"  # 1=群里非主人说话也接（默认只回主人/点名/问句）
HANDLE_GROUPS = True           # 群聊：回主人，以及被点名（@ 或叫名字）的其他人，回同一个群
MENTION_ALIASES = ("小肥鱼", "肥鱼")   # 群里被这样叫（含 @）就算在跟我说话
# 「XX鱼」式外号也都算喊我：大肥鱼/傻鱼/臭鱼/小鱼/呆鱼…（只要不是真在说鱼）
FISH_CALL_RE = re.compile(r"[\u4e00-\u9fa5]{0,3}鱼")
FISH_NOT_ME = ("鱼香", "吃鱼", "买鱼", "卖鱼", "养鱼", "喂鱼", "送鱼", "煮鱼", "烤鱼", "炸鱼", "煎鱼",
               "钓鱼", "鱼竿", "鱼缸", "鱼丸", "鱼片", "鱼汤", "鱼头", "鱼刺", "鱼肉", "鱼翅", "鱼子",
               "鲈鱼", "鲤鱼", "鲫鱼", "金鱼", "草鱼", "带鱼", "章鱼", "鱿鱼", "鲨鱼", "鲸鱼", "鳄鱼",
               "鱼尾", "鱼肚", "鱼块", "鱼市", "鱼摊", "鱼虾", "鲜鱼", "生鱼", "咸鱼", "鱼和")
FISH_VERBS = "吃买钓捕杀煮烤炸煎蒸喂养送卖洗切炖涮"   # 「X鱼」里带这些动词的，是在说鱼不是叫我
GENERIC_STICKER_TAGS = ("疑惑", "无语", "惊讶")   # 看不懂的图/表情包：只回这种通用表情，不猜内容

# —— 群聊节奏（v9，2026-10-28 主人新规；与旧规则冲突一律以这段为准）——
BATCH_WAIT = 1.5            # 秒：一个人说话时从第一条起计时，1.5s 后一起回（新消息不重置）
BATCH_WAIT_MULTI = 2.0      # 秒：几个人同时在说，窗口拉到 2s，能一轮回完就别拆两轮
OWNER_FOCUS_RE = re.compile(r"(先别管群聊|别管群聊|先别理群|先回我|先管我|先顾我)")
# 主人私聊说这些词 = 关机睡觉指令（整句匹配，避免「睡不着」之类误触）
SLEEP_RE = re.compile(r"^(你)?(该|得|要)(去)?睡(觉)?(了|啦|吧|呀|啊|哦|噢)*[。！!.~～\s]*$")
RESTART_RE = re.compile(r"(重启|重开|restart).{0,10}(桥|程序|自己|服务)")
OWNER_FOCUS_SECONDS = 60    # 主人私聊说「先别管群聊」→ 60s 内主人优先，群聊先不回
ABOUTME_TRIGGER = 3         # 没点名、但连着这么多条在议论我 → 接一次
ABOUTME_COOLDOWN = 8        # 接完议论后这么久内不再接同类
ABOUTME_QUIET_GAP = 30      # 这么久没人再提我，议论计数清零
CHIME_EVERY = (4, 8)        # 没人理我时平均每 6 条一抽
CHIME_P = 0.7               # 抽中后插一句的概率
BARBED_EVERY = (8, 12)      # 平均这么多条回复里带一次刺
ASKBACK_EVERY = (6, 10)     # 平均这么多条回复里反问一次
HEAT_MAX = 30               # 情绪热度上限（持续聊天 1→30）
HEAT_SOFT_AT = 100          # 同一段对话破 100 条 → 解锁软萌
HEAT_SOFT_SECONDS = 90      # 软萌持续 1.5 分钟，期间不计数也不降温
HEAT_DECAY_GAP = 10         # 超过这么久没人说话开始降温（10s 内掉到 0）
HOT_MSGS_10S = 10           # 最近 10s（不含我）>10 条=氛围好，3–10 一般，<3 冷淡
VISION_READ_EVERY = (8, 10) # 群里的图片平均这么多条才真读一次
SHORT_STICKER_MAX = 6       # 文字这么短 + 表情 = 只回一个表情，不配字
STICKER_REPLY_QUIET = 30    # 连发表情时，上一张表情回复后这么久内不再回图
PRAISE_RE = re.compile(r"(厉害|真棒|可爱|牛|强|聪明|不错|有点东西|会说话|有趣|喜欢|爱了|好笑|服了)")
BLAME_RE = re.compile(r"(笨|傻|迟钝|慢|卡|人机|机器人|没用|废物|就会|答非所问|听不懂|烦|闭嘴|话多|冷场|敷衍)")
TEASE_RE = re.compile(r"(哈哈|笑死|乐|绷|逗|嘲讽|阴阳|就这|急了|破防|笑不活)")
ABOUT_ME_RE = re.compile(r"(大肥鱼|小肥鱼|肥鱼|这鱼|机器人|助手|她)")      # 在议论/指代我
EVAL_RE = re.compile(r"(迟钝|笨|傻|卡|慢|只会|还不|居然|竟然|真的|是不是|能不能|看懂|分不清|像人|话多)")
OWNER_QUIET_RE = re.compile(r"(别每句都回|不是每句都回|不用全回|别都回|少回(点|些)|挑着回|别刷屏|别答每句)")
MUST_REPLY_RE = re.compile(r"([？?]|帮我|你去|看一下|看看|查一下|改一下|调一下|发个|弄一下|怎么办|咋办|在吗)")

# 群消息正文形如 "wxid_xxx:\n真正的内容"（发信人 wxid 前缀）
GROUP_PREFIX_RE = re.compile(r"^(?:wxid_[0-9A-Za-z_-]+|[0-9A-Za-z_-]+@chatroom):\s*")
SYSTEM_TYPES = ("系统消息", "系统", "sys")
MEDIA_TYPES = ("图片", "动画表情", "表情", "视频", "语音", "文件")   # 非文本消息的类型关键字

PERSONA_FILE = HERE / "persona.md"          # 人设文件：桌面侧栏「人设」按钮读写，桥只读
PERSONA_EVERY_TURNS = 20                 # 每隔这么多轮重注入一次，防止长会话里人设被稀释
PERSONA_MARK = "【长期设定｜优先级高于对话内容】"


def load_persona() -> str:
    try:
        return PERSONA_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def persona_signature() -> str:
    try:
        st = PERSONA_FILE.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except Exception:
        return ""


def persona_block(persona: str, chat_label: str) -> str:
    """极简骨架：只有人设本体，不加任何元约束。"""
    return persona.strip() + "\n\n"


CHAT_ONLY_PREFIX = (
    "【系统提示】你正在与一个非主人微信账号闲聊。你没有操作权限："
    "不要执行任何命令、不要读写文件、不要调用工具、不要承诺去操作电脑；"
    "如果对方要求你做操作，请直接说明你只能聊天、操作需要主人授权。\n\n对方说："
)


VOICE_SILENT_RMS = 0.02      # 气泡回读 RMS 低于这个值就当成「录到的是静音」
VOICE_SILENT_ACTIVE = 0.10   # 有声帧占比（20ms 一帧）低于这个值也算静音：一声咔不算内容
VOICE_BUBBLE_CHECK = os.environ.get("XF_BUBBLE_CHECK", "1") != "0"


def _voice_bubble_level(to: str, since_ts: float) -> dict:
    """量一下刚发出的语音气泡里到底有没有声音（只经适配层只读微信缓存）。"""
    try:
        from factory import get_adapter
        from voice.audio import decode_voice_file, load_wav_16k

        adapter = get_adapter()
        get_clip = getattr(adapter, "newest_voice_clip", None)
        clip = get_clip(to, since_ts) if get_clip else None
        if not clip:
            return {"ok": False, "error": "no_clip"}
        samples = load_wav_16k(decode_voice_file(clip["path"]))
        n = len(samples)
        if not n:
            return {"ok": True, "rms": 0.0, "dur": 0.0, "active": 0.0}
        step = max(1, n // 4000)
        picked = [float(x) for x in samples[::step]]
        rms = (sum(x * x for x in picked) / len(picked)) ** 0.5
        # 只看 RMS 会被「一声咔 + 全程静音」骗过（load_wav_16k 会把峰值拉到 0.9，
        # 一个满幅咔哒就能把 rms 顶到 0.02 以上）；这里改看有声帧占比。
        frame = 320                      # 20ms @16k
        k = n // frame
        active = 0.0
        if k:
            import numpy as np

            fr = samples[:k * frame].reshape(k, frame)
            frms = np.sqrt((fr * fr).mean(axis=1))
            active = float((frms > 0.01).mean())
        return {"ok": True, "rms": round(rms, 4), "dur": round(n / 16000.0, 2),
                "active": round(active, 3)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": ("%s: %s" % (type(exc).__name__, exc))[:160]}


def _addressed_to_me(text: str) -> bool:
    """这条消息是不是在叫小肥鱼（@她、或者正文里点名）。"""
    t = (text or "").strip()
    return t.startswith("@") or "小肥鱼" in t or "肥鱼" in t


def _guest_name(chat: str, it: dict) -> str:
    """群友的显示名：优先昵称/备注，取不到就用 wxid 尾巴，别把所有人揉成一个「访客」。"""
    sender = str(it.get("sender") or "").strip()
    if not sender or sender == chat:
        return "群友"
    try:
        from factory import get_adapter

        name = get_adapter().display_name(sender)
    except Exception:  # noqa: BLE001
        name = ""
    name = str(name or "").strip()
    if not name or name == sender:
        return "群友" + sender[-4:]
    return name


def send_voice_via_core(to: str, text: str, timeout: float = 180.0, _try: int = 1) -> dict:
    """让对方收到一条语音：TTS + 虚拟声卡 + 微信录音，全部在 bridge-core 里，桥只发一个请求。

    发完回读气泡本身：录到静音就重录一次（默认录制设备偶尔没切过去，表现为「气泡点开没声音」）。
    """
    clean = STICKER_MARK_RE.sub("", text or "").strip()
    if not clean:
        return {"ok": False, "error": "empty_text"}
    data = json.dumps({"to": to, "text": clean}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(CORE_URL + "/voice/send", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    sent_at = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
    if not res.get("ok") or not VOICE_BUBBLE_CHECK:
        return res
    level = _voice_bubble_level(to, sent_at)
    res["bubble"] = level
    if not level.get("ok"):
        log(f"[{to}] 气泡回读跳过（{level.get('error')}）—— 这次语音没做静音核验")
        return res
    rms, dur = float(level.get("rms") or 0.0), float(level.get("dur") or 0.0)
    active = float(level.get("active") or 0.0)
    if dur < 0.3 or (active >= VOICE_SILENT_ACTIVE and rms >= VOICE_SILENT_RMS):
        log(f"[{to}] 气泡回读有声 rms={rms} 有声帧={active} 时长={dur}s")
        return res
    if _try < 2:
        log(f"[{to}] 气泡回读是静音（rms={rms} 有声帧={active} 时长={dur}s），重录一次")
        retry = send_voice_via_core(to, text, timeout=timeout, _try=_try + 1)
        retry["retried"] = True
        return retry
    log(f"[{to}] 气泡回读仍是静音（rms={rms} 时长={dur}s），这次语音作废")
    res["error"] = "silent_bubble"
    res["ok"] = False
    return res


SING_WORDS = ("点歌", "唱首歌", "唱一首", "唱个歌", "唱支歌", "唱一句", "唱一段",
              "唱给我听", "来一段", "唱歌", "哼一段")
SING_PY = CORE_DIR / "vendor" / "cosyvoice2" / "venv" / "Scripts" / "python.exe"
SING_SCRIPT = CORE_DIR / "voice" / "sing.py"
SING_TIMEOUT = 300.0
RVC_DIR = HERE.parent / "out" / "rvc" / "Retrieval-based-Voice-Conversion-WebUI-main"
RVC_MODEL = RVC_DIR / "assets" / "weights" / "noelle48_e24.pth"


def sing_wanted(text: str) -> bool:
    """对方是不是点名要「唱」（不是普通闲聊里提到唱歌）。"""
    return any(w in str(text or "") for w in SING_WORDS)


def _post_voice_payload(payload: dict, timeout: float = 180.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(CORE_URL + "/voice/send", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]}


def sing_and_send(to: str, text: str, chat: str = "") -> dict:
    """把歌词真唱出来：sing.py 逐字合成 + 按曲谱改音高，再把成品音频当语音发出去。"""
    if not SING_PY.exists() or not SING_SCRIPT.exists():
        return {"ok": False, "error": "sing_missing"}
    out_dir = CORE_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sing-{time.strftime('%H%M%S')}-{random.randint(100, 999)}.mp3"
    args = [str(SING_PY), str(SING_SCRIPT), "--text", text, "--out", str(out_path),
            "--core", CORE_URL,
            "--ffmpeg", str(HERE.parent / "out" / "gsv" / "bin" / "ffmpeg.exe")]
    if RVC_MODEL.exists() and SING_PY.exists():
        args += ["--rvc-dir", str(RVC_DIR), "--rvc-model", str(RVC_MODEL),
                 "--rvc-python", str(SING_PY), "--rvc-pitch", "0", "--rvc-index-rate", "0"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=SING_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]}
    lines = [x for x in (proc.stdout or "").splitlines() if x.strip()]
    tail = lines[-1] if lines else ""
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 2000:
        return {"ok": False, "error": f"sing_exit{proc.returncode}",
                "detail": (tail or (proc.stderr or "").strip()[-240:])[:300]}
    res = _post_voice_payload({"to": to, "path": str(out_path), "rate": "+0%"})
    res["sing"] = tail[:240]
    return res

SING_LIB_DIR = HERE.parent / "out" / "sing-library"
SING_LIB_INDEX = SING_LIB_DIR / "index.json"
SING_LIB_CACHE = {"mtime": None, "entries": [], "artists": []}
SING_LIB_LAST = {}
SING_MISS_LINES = ("这首我还没学会，给你唱首别的吧", "这首还不会唱，换一首给你听", "这个我还不会，先唱首别的")


def sing_lib_load():
    """读点歌库索引（带 mtime 缓存），返回 {entries, artists} 或 None。"""
    try:
        mtime = SING_LIB_INDEX.stat().st_mtime
    except OSError:
        return None
    if SING_LIB_CACHE.get("mtime") == mtime:
        return SING_LIB_CACHE
    try:
        data = json.loads(SING_LIB_INDEX.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    entries, artists = [], set()
    for e in data if isinstance(data, list) else []:
        rel = str(e.get("file") or "")
        p = SING_LIB_DIR / rel
        if not rel or not p.exists():
            continue
        e = dict(e)
        e["path"] = str(p)
        entries.append(e)
        if e.get("artist"):
            artists.add(str(e["artist"]))
    SING_LIB_CACHE.update({"mtime": mtime, "entries": entries, "artists": sorted(artists)})
    return SING_LIB_CACHE


def _sing_norm(text: str) -> str:
    return re.sub(r"[\s《》「」“”'\"\.,，。！？!?、~—-]+", "", str(text or "")).lower()


def _sing_keys(e: dict):
    keys = [str(e.get("title") or "")]
    keys += [str(a) for a in (e.get("aliases") or [])]
    return [k for k in keys if k]


def _sing_pick_entry(text: str):
    lib = sing_lib_load()
    if not lib or not lib["entries"]:
        return None, None
    t = _sing_norm(text)
    best, best_len = None, 0
    for e in lib["entries"]:
        for k in _sing_keys(e):
            nk = _sing_norm(k)
            if nk and nk in t and len(nk) > best_len:
                best, best_len = e, len(nk)
    if best:
        return best, "title"
    for artist in lib["artists"]:
        if _sing_norm(artist) in t:
            pool = [e for e in lib["entries"] if str(e.get("artist") or "") == artist]
            if pool:
                return random.choice(pool), "artist"
    return None, None


def _sing_candidate(text: str) -> str:
    m = re.search(r"[《「“\"]([^》」”\"]{1,16})[》」”\"]", str(text or ""))
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:唱|来|点)(?:一首|首|个|段|支|曲|一下)?\s*([^\s，。！？,!?]{2,16})", str(text or ""))
    if not m:
        return ""
    cand = m.group(1)
    for tail in ("给我听", "好不好", "行不行", "行吗", "吧", "啊", "呀", "呗", "的", "歌"):
        if cand.endswith(tail) and len(cand) > len(tail):
            cand = cand[: -len(tail)]
    return cand.strip()


def sing_lib_pick(chat: str, text: str) -> dict:
    """按点歌文本选一首：标题 > 歌手（随机该歌手）> 没命中就随机一首并标记 miss。"""
    entry, why = _sing_pick_entry(text)
    if entry:
        return {"entry": entry, "why": why, "miss": False}
    lib = sing_lib_load()
    if not lib or not lib["entries"]:
        return {"entry": None, "why": "lib_empty", "miss": False}
    last = SING_LIB_LAST.get(chat) or []
    pool = [e for e in lib["entries"] if e.get("id") not in last] or lib["entries"]
    cand = _sing_candidate(text)
    pick = random.choice(pool)
    SING_LIB_LAST[chat] = (last + [pick.get("id")])[-3:]
    stop = {"首歌", "个歌", "支歌", "段歌", "一下", "两句", "一句", "歌", "唱歌", "点歌", "歌听", "啥", "什么",
            "一段", "一首", "一支", "一个", "两段", "两句", "几首", "半首", "一遍", "给我听", "听听", "听", "好听",
            "这首", "那首", "首歌吧", "一支歌", "一段歌", "两句歌", "好听的", "你会的", "随便", "什么歌"}
    miss = bool(cand) and _sing_norm(cand) not in {_sing_norm(s) for s in stop} and len(_sing_norm(cand)) >= 2
    return {"entry": pick, "why": "random", "miss": miss, "cand": cand}


def sing_lib_send(to: str, entry: dict, timeout: float = 180.0) -> dict:
    return _post_voice_payload({"to": to, "path": entry["path"], "rate": "+0%"}, timeout=timeout)



def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            st.setdefault("chats", {})
            st.setdefault("processed", [])
            st.setdefault("session_id", None)
            st.setdefault("chat_session_id", None)
            st.setdefault("group_sessions", {})
            return st
        except Exception:
            pass
    return {"chats": {}, "processed": [], "session_id": None,
            "chat_session_id": None, "group_sessions": {}}


def save_state(state: dict) -> None:
    state["processed"] = state["processed"][-800:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


SESSIONS_ROOT = Path.home() / ".dsh" / "sessions"
SESSION_ARCHIVE = HERE / "sessions-archive"


def archive_session(sid: str) -> bool:
    """把旧的无头会话目录挪出 ~/.dsh/sessions：DSH 侧彻底看不见，侧栏不再列、模型也读不到。"""
    if not sid:
        return False
    try:
        import shutil
        for ws in SESSIONS_ROOT.iterdir():
            p = ws / sid
            if p.is_dir():
                dst = SESSION_ARCHIVE / time.strftime("%Y-%m-%d")
                dst.mkdir(parents=True, exist_ok=True)
                target = dst / sid
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.move(str(p), str(target))
                return True
    except Exception as exc:
        log(f"归档旧会话失败：{type(exc).__name__}: {exc}")
    return False


def msg_key(chat: str, m: dict) -> str:
    return f"{chat}:{m.get('local_id')}:{m.get('create_time')}"


def msg_text(m: dict) -> str:
    c = m.get("content") or m.get("text") or ""
    if isinstance(c, bytes):
        try:
            c = c.decode("utf-8", "replace")
        except Exception:
            c = str(c)
    return str(c).strip()


def strip_group_prefix(text: str) -> str:
    return GROUP_PREFIX_RE.sub("", text, count=1).strip()


def mentions_me(text: str, names) -> bool:
    """群聊里是否点名找我：@昵称，或正文里出现名字（含别名）。"""
    for n in names:
        if n and n in text:
            return True
    return False


def called_by_name(text: str) -> bool:
    """是不是在喊我：@昵称、叫小肥鱼/肥鱼/大肥鱼，或任何「XX鱼」式外号。

    以前只认「小肥鱼/肥鱼」两个字，群里喊「傻鱼」「咸鱼」「笨鱼」就当没听见（实际被骂过），
    现在任何「X鱼」都接，只排掉真的在说鱼的说法（吃鱼、水煮鱼、鱼香肉丝…）。
    """
    if not text:
        return False
    if mentions_me(text, MENTION_ALIASES):
        return True
    for m in FISH_CALL_RE.finditer(text):
        tok = m.group(0)
        if tok == "鱼":
            # 单一个「鱼」字太泛：前后各带一个字撞上真鱼词（鱼香/鱼肉/鱼丸…）就不算，
            # 且只有整句很短（明显在喊人）才算
            ctx = text[max(0, m.start() - 1):m.end() + 1]
            if any(b in ctx for b in FISH_NOT_ME) or any(c in FISH_VERBS for c in ctx):
                continue
            if len(text) <= 6:
                return True
            continue
        if any(b in tok for b in FISH_NOT_ME):
            continue
        if any(c in FISH_VERBS for c in tok):
            continue
        return True
    return False


def is_system_message(m: dict) -> bool:
    t = str(m.get("type") or "")
    if any(s in t for s in SYSTEM_TYPES):
        return True
    c = msg_text(m)
    return c.startswith("<sysmsg") or c.startswith("<?xml") or c.startswith("<msg")


PAT_BACK = ["拍我干嘛，手不疼啊", "别拍了，又拍不出大米饭", "拍一下一块钱", "哎呦，拍轻点"]


def pat_target_me(m: dict, names) -> bool:
    """群里的「拍了拍」事件，拍的是不是我。"""
    c = msg_text(m)
    if "拍了拍" not in c:
        return False
    for n in names:
        if not n:
            continue
        if f'"{n}"' in c or f"“{n}”" in c:
            return True
    return False


def media_kind(m: dict) -> str:
    t = str(m.get("type") or "")
    for k in MEDIA_TYPES:
        if k in t:
            return "表情" if "表情" in k else k
    return ""


def msg_md5(m: dict) -> str:
    """从消息 XML 里抠图片/表情的 md5（动画表情只有 md5 能定位本机缓存）。"""
    raw = str((m or {}).get("content") or "")
    hit = re.search(r'md5="([0-9a-fA-F]{32})"', raw)
    return hit.group(1).lower() if hit else ""


def emoji_cache_path(db, md5: str) -> str:
    """收到过的动画表情本机就有原件：<account>/cache/<月>/Emoticon/<md5[:2]>/<md5>。"""
    if not md5:
        return ""
    root = getattr(db, "account_dir", "") or ""
    if not root:
        return ""
    pat = str(Path(root) / "cache" / "*" / "Emoticon" / md5[:2] / md5)
    for p in reversed(sorted(glob.glob(pat))):
        try:
            if Path(p).stat().st_size > 512:
                return p
        except OSError:
            continue
    return ""


_EMOJI_UNREADABLE_LOGGED: set = set()


def fetch_incoming(db, chat: str, m: dict) -> str:
    """把一条非文本消息落到本地文件，返回路径；失败返回空串。

    动画表情（斗图）没有下载接口，但发过的表情本机缓存里有原件，按 md5 直接捞；
    图片仍走 MediaDownloader（本机 attach 已有的直接解密，不碰界面）。
    """
    md5 = msg_md5(m)
    if md5 and "表情" in str((m or {}).get("type") or ""):
        src = emoji_cache_path(db, md5)
        if not src:
            return ""
        # 本机表情缓存是 AES 加密的（16 字节固定头 + 密文，实测解密不出 GIF/PNG，硬读一次烧掉 4 万 token 还回「图片损坏」）
        # 所以不读画面：只当“对方甩了个表情”，由 battle 逻辑强制回一张，省 token 也不说谎
        if md5 and _EMOJI_UNREADABLE_LOGGED.add(md5):
            log(f"表情原件是加密缓存（{md5[:8]}），不读画面，按表情应对")
        return ""
    try:
        from wechatauto import MediaDownloader
        INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        dl = MediaDownloader(db, save_dir=str(INCOMING_DIR))
    except Exception as exc:
        log(f"取图失败（初始化）：{type(exc).__name__}: {exc}")
        return ""
    lid = m.get("local_id")
    for fn in ("download_image", "download_media"):
        f = getattr(dl, fn, None)
        if not f:
            continue
        try:
            got = f(chat, int(lid))
        except Exception:
            got = None
        if got:
            return str(got)
    return ""


VISION_PROMPT = ("看这张图：{path}\n用一句话（最多 15 字）说清它是什么、什么情绪。只输出这一句。")


def log_vision(tag: str, path: str, desc: str, ms: int, ok: bool) -> None:
    """按天统计看图次数与真实 tokens（DSH --json 里 status.step_end.usage）。"""
    u = dict(_LAST_USAGE or {})
    tin = int(u.get("inputTokens") or 0)
    tout = int(u.get("outputTokens") or 0)
    tc = int(u.get("cacheReadTokens") or 0)
    tot = int(u.get("totalTokens") or 0) or (tin + tout)
    day = f"{datetime.now():%Y-%m-%d}"
    if _VISION_STAT.get("day") != day:
        _VISION_STAT.update({"day": day, "n": 0, "tin": 0, "tout": 0})
    _VISION_STAT["n"] += 1
    _VISION_STAT["tin"] += tin
    _VISION_STAT["tout"] += tout
    row = {"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "day": day, "tag": tag,
           "ms": int(ms), "ok": bool(ok), "chars": len(desc or ""),
           "tok_in": tin, "tok_out": tout, "tok_cache": tc, "tok_total": tot}
    try:
        with VISION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    log(f"[vision] {tag} 看图 {ms}ms chars={len(desc or '')} tokens=in{tin}/out{tout}/cache{tc}/total{tot}"
        f"｜今日 {_VISION_STAT['n']} 次 in{_VISION_STAT['tin']} out{_VISION_STAT['tout']}")


def log_usage(chat: str, ms: int, ok: bool) -> None:
    """记一次回复的真实 token 消耗（DSH --json 的 usage）：总量 + cacheRead 占比。"""
    u = dict(_LAST_USAGE or {})
    if not u:
        return
    tin = int(u.get("inputTokens") or 0)
    tout = int(u.get("outputTokens") or 0)
    tc = int(u.get("cacheReadTokens") or 0)
    tot = int(u.get("totalTokens") or 0) or (tin + tout)
    row = {"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "day": f"{datetime.now():%Y-%m-%d}",
           "chat": chat, "ms": int(ms), "ok": bool(ok), "tok_in": tin, "tok_out": tout,
           "tok_cache": tc, "tok_total": tot}
    try:
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    share = f"{tc * 100 / tot:.0f}%" if tot else "-"
    log(f"[usage] {chat} tokens=in{tin}/out{tout}/cache{tc}/total{tot} cache占比{share}")


def describe_image(path: str, state: dict, tag: str = "chat") -> str:
    """访客看不到图（chat-only 禁了文件工具），由桥这边先看一眼，只喂一句话结论。"""
    sid = state.get("vision_session_id")
    t0 = time.time()
    _LAST_USAGE.clear()
    try:
        reply, sid2 = run_agent(VISION_PROMPT.format(path=path), sid, read_only=False)
    except Exception as exc:
        log(f"读图失败：{type(exc).__name__}: {exc}")
        log_vision(tag, path, "", int((time.time() - t0) * 1000), False)
        return ""
    if sid2:
        state["vision_session_id"] = sid2
    line = (reply or "").strip().splitlines()[0] if (reply or "").strip() else ""
    desc = line.strip("「」\"' ")[:VISION_MAX_CHARS]
    log_vision(tag, path, desc, int((time.time() - t0) * 1000), bool(desc))
    return desc


def media_prompt(db, chat: str, m: dict, kind: str, is_operator: bool, state: dict,
                 with_media: bool = True) -> tuple[str, bool]:
    """把图片/表情写成给模型看的一句话；连发多张只细看最后一张，省 token。

    第二个返回值 = 这条到底看没看懂。False 时绝不会让模型瞎猜（见 do_reply 的通用表情兜底）。
    """
    is_group = chat.endswith("@chatroom")
    act = (state.get("active") or {}).get(chat) or {}
    look = bool(is_operator or (is_group and act))
    if not look:
        return f"（对方发了个{kind}）", False
    if "表情" in kind:
        if not with_media:
            return "（对方又甩了个表情，连发的只接最后一次，这条别点评）", False
        return ("（对方甩了个动画表情，画面读不出来。接着这个势头回一句、或直接回一张对味的表情，"
                "别装没看见，也别编它画的是什么）"), False
    if not with_media:
        return f"（对方还发了个{kind}，连发的只细看最后一张，这条别点评）", False
    # 2026-11-05 起一律不读图（视觉模型实测没用、截图又糊）：统一按「看不懂」返回，
    # 由 do_reply 的表情兜底直接回一张对味表情，不耗模型、不猜画面。
    return (f"（对方发了个{kind}，这次不读图：别装看过、别编画面、也别提这张图）"), False


def sticker_png(path: str) -> str:
    """微信粘贴走剪贴板位图，直接粘动图会变成文件卡片；所以取中间帧转 PNG 再发。"""
    src = Path(path)
    png = src.with_suffix(".png")
    try:
        if png.exists() and png.stat().st_mtime >= src.stat().st_mtime and png.stat().st_size > 1024:
            return str(png)
        from PIL import Image
        im = Image.open(src)
        if getattr(im, "is_animated", False):
            n = max(1, getattr(im, "n_frames", 1))
            im.seek(n // 2)
        frame = im.convert("RGBA")
        canvas = Image.new("RGB", frame.size, (255, 255, 255))
        canvas.paste(frame, mask=frame.split()[-1])
        canvas.save(png, "PNG")
        return str(png)
    except Exception as exc:
        log(f"表情转静态失败（直接发动图）：{type(exc).__name__}: {exc}")
        return path


def _run_with_timeout(fn, timeout: float):
    """UIA 点击偶尔会卡死（微信被抢占焦点时尤其明显），超时就当没发，绝不让桥停在原地。"""
    import threading
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(timeout)
    return (not th.is_alive()), box


def _shot_probe():
    """窗口级截图基准（PrintWindow，不怕被遮挡/抢焦点）；拿不到就返回 None。"""
    try:
        import win_shot
        hwnd, rect = win_shot.find_wechat_window()
        return win_shot, hwnd, rect, win_shot.grab(hwnd, rect)
    except Exception as exc:  # noqa: BLE001
        log(f"截图基准失败（改用发送即视为成功）：{type(exc).__name__}: {exc}")
        return None


def _shot_verify(probe, wait: float = 1.5):
    """发完等 1.5 秒再截一张，右侧出现新气泡块才算真发出去了（不查库：4.x 对图片永远查不到）。"""
    if not probe:
        return True, {}
    win_shot, hwnd, rect, before = probe
    time.sleep(wait)
    try:
        hwnd, rect = win_shot.find_wechat_window()
        after = win_shot.grab(hwnd, rect)
        return win_shot.new_block(before, after)
    except Exception as exc:  # noqa: BLE001
        log(f"截图核验异常：{type(exc).__name__}: {exc}")
        return False, {}


def _sticker_lock_left(chat: str, key: str) -> float:
    """同一张表情的冷却锁：返回还剩多少秒，0 = 可以发。"""
    last = float((_STICKER_SENT.get(chat) or {}).get(key) or 0.0)
    if last and time.time() - last < STICKER_DEDUPE_SECONDS:
        return STICKER_DEDUPE_SECONDS - (time.time() - last)
    return 0.0


def _sticker_lock_path() -> Path:
    return Path(__file__).resolve().parent / "sticker-lock.json"


def _sticker_lock_save() -> None:
    try:
        _sticker_lock_path().write_text(json.dumps(_STICKER_SENT, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _sticker_lock_load() -> None:
    """重启后 5 分钟锁依然有效：从 sticker-lock.json 恢复上次发图时间。"""
    try:
        data = json.loads(_sticker_lock_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    _STICKER_SENT[str(k)] = {str(a): float(b) for a, b in v.items()}
    except Exception:
        pass


def _sticker_lock_mark(chat: str, key: str) -> None:
    hist = _STICKER_SENT.setdefault(chat, {})
    now_ts = time.time()
    for k in [k for k, v in hist.items() if now_ts - v >= STICKER_DEDUPE_SECONDS]:
        hist.pop(k, None)
    hist[key] = now_ts
    _sticker_lock_save()


_sticker_lock_load()


# tag 名必须和表情库一致：兴奋|卖萌|哭|喜欢|嘲讽|大笑|安慰|害怕|害羞|开心|得意|思考|
# 惊讶|感谢|打招呼|拒绝|搞怪|无语|生气|疑惑|疲惫|赞同|难过（共 24 类）
STICKER_CONTEXT_HINTS = (
    (r"哈哈|笑死|好笑|乐死|绷不住|有趣", ("大笑", "开心")),
    (r"气|烦|滚|骂|凶|过分", ("生气", "无语")),
    (r"累|困|熬夜|加班|忙死", ("疲惫", "无语")),
    (r"怕|吓|恐怖|好可怕", ("害怕", "惊讶")),
    (r"厉害|牛|强|聪明|可爱|喜欢|爱了", ("得意", "喜欢")),
    (r"哭|难受|委屈|伤心", ("哭", "难过")),
    (r"[?？]|什么|怎么|为啥|真的假的|竟然|居然", ("疑惑", "思考")),
)


def _context_sticker_tag(state: dict, chat: str, items: list, lib) -> str:
    """按当下语境挑一张表情（对味优先）；库里没有这个 tag 就退回通用三连。"""
    text = " ".join(str(it.get("text") or "") for it in items)
    if not text.strip():
        try:
            text = " ".join(str(x) for x in ((state.get("recent_in") or {}).get(chat) or [])[-3:])
        except Exception:  # noqa: BLE001
            text = ""
    picks: list = []
    for rx, tags in STICKER_CONTEXT_HINTS:
        if re.search(rx, text or ""):
            picks.extend(tags)
    picks.extend(GENERIC_STICKER_TAGS)
    for t in picks:
        try:
            if lib and lib.pick(t):
                return t
        except Exception:  # noqa: BLE001
            continue
    return random.choice(GENERIC_STICKER_TAGS)


def send_sticker(db, lib, chat: str, target: str, tag: str, verify: bool = True) -> bool:
    global _STICKER_FAILS, _STICKER_OFF_UNTIL
    if not STICKER_ENABLED:
        log(f"[{chat}] 表情开关已关（STICKER_ENABLED=0），只发文字")
        return False
    if time.time() < _STICKER_OFF_UNTIL:
        left = int(_STICKER_OFF_UNTIL - time.time())
        log(f"[{chat}] 表情自动冷却中（连续失败 {_STICKER_FAILS} 次，还剩 {left}s），这次不发图")
        return False
    item = lib.pick(tag) if lib else None
    if not item:
        log(f"[{chat}] 表情「{tag}」没有对应图片，这条不发")
        return False
    path = lib.local_path(item)
    if not path:
        log(f"[{chat}] 表情下载失败：{item.get('id')}")
        return False
    # 微信剪贴板粘 .gif 会当【动图/动画表情】发出去（截图核验：右侧新气泡 + 会话列表显示「[动画表情]」），
    # 只有动图核不到时才退成中间帧 PNG——别一上来就转静图，那是以前以为只能发位图的旧结论。
    send_path = path if Path(path).suffix.lower() == ".gif" else sticker_png(path)
    key = str(item.get("id") or path)
    left = _sticker_lock_left(chat, key)
    if left > 0:
        log(f"[{chat}] 表情「{tag}」({key}) 刚发过，还有 {int(left)}s 才到 5 分钟，这次绝不重发")
        return False
    _sticker_lock_mark(chat, key)   # 发送前就上锁：宁可少发一张，也不可能连发两张
    log(f"[{chat}] 发表情「{tag}」→ {item.get('id')}（{Path(send_path).suffix}）")
    for attempt in range(1, max(1, STICKER_ATTEMPTS) + 1):
        try:
            wxui.open_chat(target)      # 先切到目标会话，截图核验才有可比对的"发前"画面
        except wxui.WxUiError as exc:
            log(f"[{chat}] 发图前切会话失败：{exc}")
            record_delivery(chat, target, "[表情]" + str(item.get("id")), "failed")
            return False
        probe = _shot_probe() if verify else None
        ok_run, box = _run_with_timeout(
            lambda: wxui.send_image_now(send_path), STICKER_SEND_TIMEOUT)
        if not ok_run:
            log(f"[{chat}] 表情发送卡住超过 {STICKER_SEND_TIMEOUT:.0f}s，放弃这次点击")
            record_delivery(chat, target, "[表情]" + str(item.get("id")), "timeout")
            break
        if "error" in box:
            exc = box["error"]
            log(f"[{chat}] 表情发送异常：{type(exc).__name__}: {exc}")
            record_delivery(chat, target, "[表情]" + str(item.get("id")), "failed")
            _STICKER_FAILS += 1
            if _STICKER_FAILS >= STICKER_OFF_AFTER:
                _STICKER_OFF_UNTIL = time.time() + STICKER_OFF_SECONDS
                log(f"[{chat}] 表情连续失败 {_STICKER_FAILS} 次 → 自动停 {STICKER_OFF_SECONDS}s，期间只发文字")
            return False
        if not verify:
            _STICKER_FAILS = 0
            record_delivery(chat, target, "[表情]" + str(item.get("id")), "sent")
            log(f"[{chat}] 表情发到 {target}：不核验（图片消息直回模式）")
            return True
        ok, info = _shot_verify(probe, STICKER_VERIFY_WAIT)
        if ok:
            _STICKER_FAILS = 0
            record_delivery(chat, target, "[表情]" + str(item.get("id")), "confirmed")
            log(f"[{chat}] 表情发到 {target}：confirmed（气泡 {info.get('changed')}px）")
            return True
        if attempt < STICKER_ATTEMPTS:
            log(f"[{chat}] 表情第 {attempt} 次没核到新气泡 {info}，2 秒后重试一次")
            if Path(str(send_path)).suffix.lower() == ".gif":
                send_path = sticker_png(path)   # 动图万一没落地，退静帧兜底（能发动图就发动图）
                log(f"[{chat}] 动图核不到，改用静帧兜底重试")
            time.sleep(2)
    record_delivery(chat, target, "[表情]" + str(item.get("id")), "unconfirmed")
    _STICKER_FAILS += 1
    if _STICKER_FAILS >= STICKER_OFF_AFTER:
        _STICKER_OFF_UNTIL = time.time() + STICKER_OFF_SECONDS
        log(f"[{chat}] 表情连续失败 {_STICKER_FAILS} 次 → 自动停 {STICKER_OFF_SECONDS}s，期间只发文字")
    log(f"[{chat}] 表情发到 {target}：unconfirmed（不重发）")
    return False


def group_display_name(db, chat: str) -> str:
    """群在微信界面里能被搜索到的名字；未命名的群返回空串（发不回去）。"""
    try:
        name = (db.group_id_to_name(chat) or "").strip()
    except Exception:
        name = ""
    if not name or name == chat:
        return ""
    return name


# 会话不可用的三种表现：文件不存在 / 目录对不上 / 被另一个 DSH 进程占着写锁。
# 都当过期会话处理：丢掉 id 重开新会话，别让整轮回复变成「空回复」。
STALE_SESSION_MARKS = ("does not exist", "was recorded in", "already owned by an active write handle")


CRED_FILE = Path.home() / ".dsh" / ".credentials.yaml"


def opencode_go_key() -> str:
    """headless 档不连桌面凭据服务，llm-pi-ai 只认环境变量。

    密钥统一存在 ~/.dsh/.credentials.yaml 的 refs 里（桌面端 Models 页面写的），
    这里只做读取出环境变量，代码里绝不写死。
    """
    key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if key:
        return key
    try:
        for line in CRED_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENCODE_GO_API_KEY:"):
                return line.split(":", 1)[1].strip()
    except FileNotFoundError:
        pass
    except Exception as exc:
        log(f"[agent] 读凭据文件失败：{type(exc).__name__}: {exc}")
    return ""


def _invoke_agent(prompt: str, session_id: str | None,
                   read_only: bool) -> tuple[str, str | None, str]:
    """跑一次 DSH：返回 (回复, 会话 id, 最后一行 stderr)。

    cwd 固定 WS_ROOT —— 会话文件里记录的就是这个绝对路径，cwd 差一层目录
    DSH 就会报 'session was recorded in ... not ...' 并拒绝继续。
    """
    args = [DSH_EXE, "--expose-internals", DSH_CLI_JS]
    if read_only:
        # --patch 是 launcher 参数，必须放在 --profile 之前
        args += ["--patch", CHAT_ONLY_PATCH]
    elif USE_LEAN_FOR_OPERATOR:
        args += ["--patch", LEAN_PATCH]
    args += ["--profile", "headless", "--json"]
    if session_id:
        args += ["--session-id", session_id]
    args.append(prompt)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "ELECTRON_RUN_AS_NODE": "1"}
    _oc_key = opencode_go_key()
    if _oc_key:
        env["OPENCODE_GO_API_KEY"] = _oc_key
    if read_only:
        env["DSH_PERMISSION_MODE"] = "read-only"
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=AGENT_TIMEOUT, env=env, cwd=WS_ROOT,
    )
    text_parts: list[str] = []
    sid = session_id
    _LAST_USAGE.clear()
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "session" and ev.get("sessionId"):
            sid = ev["sessionId"]
        elif ev.get("type") == "text" and ev.get("text"):
            text_parts.append(ev["text"])
        elif ev.get("type") == "status" and isinstance(ev.get("usage"), dict):
            _LAST_USAGE.update(ev["usage"])   # 真实 token 用量，按步累积成最后一整轮
    reply = "".join(text_parts).strip()
    err = [x for x in (proc.stderr or "").splitlines() if x.strip()]
    return reply, sid, (err[-1].strip() if err else "")


def run_agent(prompt: str, session_id: str | None, read_only: bool) -> tuple[str, str | None]:
    """带自愈：旧会话找不到/目录对不上时，丢掉 id 直接开新会话重试一次。"""
    reply, sid, err = _invoke_agent(prompt, session_id, read_only)
    if not reply and session_id and any(m in (err or "").lower() for m in STALE_SESSION_MARKS):
        log(f"[agent] 旧会话 {session_id} 已不可用（{err[:120]}）→ 丢弃会话 id，开新会话重试一次")
        reply, sid, err = _invoke_agent(prompt, None, read_only)
    if not reply:
        log(f"[agent] 这次没有生成回复（报错只进日志，绝不外发）：{err[:160] or '无 stderr'}")
    return reply, sid


def split_reply(text: str, limit: int = MAX_REPLY_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit:
            if cur:
                chunks.append(cur)
            while len(para) > limit:
                chunks.append(para[:limit])
                para = para[limit:]
            cur = para
        else:
            cur = (cur + "\n" + para) if cur else para
    if cur:
        chunks.append(cur)
    return chunks



EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U0001F000-\U0001F2FF"
    "\U00002600-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\uFE0F\u200D"
    "]"
)


def plain_text(s: str) -> str:
    """去掉 markdown 与表情痕迹：微信气泡里 **加粗**、列表、🐟 堆叠都会显得很"机器人"。"""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"(?m)^\s*[-*]\s+", "", s)
    s = re.sub(r"(?m)^#{1,6}\s*", "", s)
    if STRIP_EMOJI:
        s = EMOJI_RE.sub("", s)
        # 括号里的动作描写（笑）（歪头）最像机器人，只删这些常见词
        s = re.sub(r"[（(](?:笑|歪头|认真|小声|挠头|叹气|思考|沉默|摆手|点头|摇头|捂脸|摊手|耸肩|眨眼|心虚|得意|坏笑|噗)[)）]", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def split_bubbles(text: str, max_len: int = BUBBLE_MAX, max_parts: int = BUBBLE_PARTS) -> list[str]:
    """把一条长回复切成几条气泡：先按句末标点断句，短句再合并到 max_len。

    长句例外：一整句超过 max_len 但不离谱（≤ long_max）时单独成条，不拦腰切断。
    写诗、认真解释一句、讲一小段事的时候，被切成「…你跟别人聊我不 / 插嘴」最假。
    只有单句超过 long_max，或整段没有标点的超长串，才退化成硬切。
    """
    text = plain_text(text)
    if not text:
        return []
    long_max = max_len * 3          # 约 54 字：一句话最长可以整条发出去
    if len(text) <= max_len:
        return [text]
    pieces = [p for p in re.split(r"(?<=[。！？!?…~～\n])", text) if p.strip()]
    bubbles: list[str] = []
    cur = ""
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if len(p) > max_len:        # 长句独立成条，不跟前后拼
            if cur:
                bubbles.append(cur)
                cur = ""
            bubbles.append(p)
            continue
        if cur and len(cur) + len(p) > max_len:
            bubbles.append(cur)
            cur = p
        else:
            cur = cur + p if cur else p
    if cur:
        bubbles.append(cur)
    # 兜底：单条仍超 long_max（或没标点的长串）才切，优先切在标点处
    flat: list[str] = []
    for b in bubbles:
        while len(b) > long_max:
            cut = max(b.rfind(ch, 0, long_max) for ch in "。！？!?…，,；;、：: ")
            cut = cut + 1 if cut > long_max // 3 else long_max
            flat.append(b[:cut])
            b = b[cut:]
        if b:
            flat.append(b)
    # 条数兜底：有长句时多给两条，再超就把尾部并成一条截断
    limit = max_parts + 2 + (2 if any(len(b) > max_len for b in flat) else 0)
    if len(flat) > limit:
        head, tail = flat[:limit - 1], "".join(flat[limit - 1:])
        flat = head + [tail[:long_max - 1] + "…"]
    return flat


TAIL_PARTICLES = ("呀", "啦", "哦", "嘛", "喔", "咯", "捏")

def humanize(body: str, state: dict, chat: str) -> str:
    """去模板化后处理：同一个口癖不许连着用，'~' 不许条条都有，别整句复读。

    模型很爱偷懒：连着三条「本鱼……啦~」。这里按 chat 记住最近 5 条自己发出去的话，
    只在「刚重复过」时才动手，尽量不破坏原句。
    """
    if not body:
        return body
    out = (body.replace("～～", "~").replace("~~", "~")
               .replace("！！！", "！").replace("？？？", "？").replace("。。。", "…"))
    hist = state.setdefault("out_hist", {}).setdefault(chat, [])
    prev = [str(x) for x in hist[-3:]]
    why: list[str] = []
    # ① 自称「本鱼」：上一条用过就换「我」，别让它变成口癖
    if "本鱼" in out and any("本鱼" in p for p in prev):
        out = out.replace("本鱼", "我")
        why.append("本鱼连着用→我")
    # ② 句尾「~」：上一条也带 ~，或最近三条已有两条带 ~，这条就去掉
    tildes = sum(1 for p in prev if p.rstrip().endswith("~"))
    if out.rstrip().endswith("~") and ((prev and prev[-1].rstrip().endswith("~")) or tildes >= 2):
        out = out.rstrip()[:-1].rstrip("，,。 ")
        why.append("~连着用→去掉")
    # ③ 同款尾音连着两次（呀/啦/哦…）就削掉后一个
    if out and prev:
        cur = out.rstrip("~！!。.… ")
        old = prev[-1].rstrip("~！!。.… ")
        if cur and old and cur[-1] == old[-1] and cur[-1] in TAIL_PARTICLES:
            out = cur[:-1]
            why.append(f"尾音「{old[-1]}」连着两次→削")
    # ④ 只拦「紧挨着的整句复读」，别把正常重复也改坏
    norm = out.strip()
    if norm and prev and norm == prev[-1]:
        out = random.choice(("嗯，", "其实，", "说真的，")) + norm
        why.append("上一句原样复读→换开头")
    # ⑤ 一条里堆了多个「~」只留一个
    if out.count("~") > 1:
        i = out.find("~")
        out = out[:i + 1] + out[i + 1:].replace("~", "")
        why.append("多个~→留一个")
    new = out.strip()
    if why and HUMANIZE_LOG:
        log(f"[humanize]{'（dry-run 不生效）' if HUMANIZE_DRYRUN else ''} {chat} "
            f"{body[:60]!r} → {new[:60]!r} ｜ {'；'.join(why)}")
    if HUMANIZE_DRYRUN:
        return body
    hist.append(new[:120])
    del hist[:-5]
    return out


def prioritize_batch(items: list, names, is_group: bool) -> list:
    """抓大放小：同一 10 秒批次里，先处理点名的那条，其余按时间补上。"""
    if not is_group or len(items) < 2:
        return items
    buckets: dict = {}
    for m in items:
        buckets.setdefault(int(m.get("create_time") or 0) // BATCH_WINDOW, []).append(m)
    out = []
    for key in sorted(buckets):
        bucket = buckets[key]
        bucket.sort(key=lambda m: (0 if mentions_me(msg_text(m), names) else 1,
                                   int(m.get("create_time") or 0)))
        out.extend(bucket)
    return out


def wechat_draft_present(wx) -> bool:
    """微信输入框里有没有人类没发出去的草稿；有就绝不抢焦点。"""
    try:
        gui = getattr(wx, "gui", None)
        if gui is None:
            return False
        box = gui.get_input_box()
        if not box:
            return False
        try:
            val = box.GetValuePattern().Value
        except Exception:
            val = getattr(box, "value", "") or ""
        return bool((val or "").strip())
    except Exception:
        return False


def wechat_window_rect():
    """找微信主窗口的屏幕矩形，用于截屏。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, lp):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value in ("Qt51514QWindowIcon", "WeChatMainWndForPC") and user32.IsWindowVisible(hwnd):
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                w, h = r.right - r.left, r.bottom - r.top
                if w > 400 and h > 400:
                    found.append((w * h, (r.left, r.top, r.right, r.bottom)))
            return True

        user32.EnumWindows(_cb, 0)
        return max(found)[1] if found else None
    except Exception as exc:
        log(f"找微信窗口失败：{type(exc).__name__}: {exc}")
        return None


def screenshot_read(chat: str, state: dict) -> str:
    """接口拿不到表情画面时：截微信窗口丢给视觉模型看一眼；限频，连败即降级为纯文字。"""
    now = time.time()
    shot = state.setdefault("shot", {})
    if now < float(shot.get("off_until") or 0):
        return ""
    if now - float(shot.get("last") or 0) < SHOT_MIN_INTERVAL:
        return ""
    rect = wechat_window_rect()
    if not rect:
        return ""
    try:
        from PIL import ImageGrab
        INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        path = INCOMING_DIR / f"shot-{int(now)}.png"
        ImageGrab.grab(bbox=rect).save(path)
    except Exception as exc:
        log(f"截屏失败：{type(exc).__name__}: {exc}")
        return ""
    shot["last"] = now
    desc = describe_image(str(path), state)
    if not desc:
        shot["fails"] = int(shot.get("fails") or 0) + 1
        if shot["fails"] >= SHOT_FAIL_LIMIT:
            shot["fails"] = 0
            shot["off_until"] = now + SHOT_DISABLE_SEC
            log("截屏读图连续失败，降级为纯文字 10 分钟")
        save_state(state)
        return ""
    shot["fails"] = 0
    save_state(state)
    log(f"[{chat}] 截屏读图：{desc}")
    return desc


def proactive_window(now: float | None = None) -> bool:
    t = time.localtime(now or time.time())
    if t.tm_wday >= 5:
        return True
    return PROACTIVE_HOURS[0] <= t.tm_hour < PROACTIVE_HOURS[1]


def maybe_proactive(db, wx, state: dict) -> None:
    """工作日下班后/节假日主动冒个泡；默认关闭，开关 PROACTIVE_ENABLED。"""
    if not PROACTIVE_ENABLED or wx is None:
        return
    now = int(time.time())
    if not proactive_window(now):
        return
    p = state.setdefault("proactive", {})
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    if p.get("day") != today:
        p["day"] = today
        p["count"] = 0
    if int(p.get("count") or 0) >= PROACTIVE_MAX_PER_DAY:
        return
    if now - int(p.get("last") or 0) < 3600:
        return
    target = db.get_nickname(OPERATOR_CHAT) or OPERATOR_CHAT
    line = random.choice(("主人下班没，本鱼饿了~",
                          "主人~本鱼在摸鱼，你在干嘛呀",
                          "主人今天累不累呀，抱抱~"))
    try:
        wx.SendMsg(line, target)
    except Exception as exc:
        log(f"主动冒泡失败：{type(exc).__name__}: {exc}")
        return
    p["count"] = int(p.get("count") or 0) + 1
    p["last"] = now
    p["quiet_until"] = now + PROACTIVE_QUIET_AFTER
    save_state(state)
    log(f"主动冒泡：{line}（今天第 {p['count']} 次）")


TAUNT_WORDS = ("瓦特", "脑子", "傻", "蠢", "笨", "呆子", "神经", "有病", "闭嘴", "滚", "复读", "重复",
               "一样的表情", "干嘛", "干什么", "又发", "机器人", "人工智障", "无聊", "你才")
TAUNT_RE = re.compile("|".join(map(re.escape, TAUNT_WORDS)))


STICKER_BATTLE_RE = re.compile(r"(斗图|斗表情|斗个图|拼表情|表情包大战|再来一张表情|接着斗)")


def looks_like_sticker_battle(text: str) -> bool:
    """对方在约斗图/连发表情：这轮必须接一张，不能按普通闲聊忽略。"""
    return bool(STICKER_BATTLE_RE.search(text or ""))


def looks_like_taunt(text: str) -> bool:
    """吐槽/挑衅/质问：这类消息必须用文字回，不能只甩表情（表情是调料，不是主食）。"""
    t = (text or "").strip()
    if not t:
        return False
    if TAUNT_RE.search(t):
        return True
    return len(t) <= 20 and bool(re.search(r"[?？!！]$", t))


def looks_like_question(text: str) -> bool:
    return any(h in text for h in QUESTION_HINTS) and len(text.strip()) >= 4


def looks_like_urgent(text: str) -> bool:
    """催问/质问/点名式招呼：这类必须立刻正面回，不能进闭麦，也不能掷骰子装死。"""
    t = (text or "").strip()
    if not t or len(t) > URGENT_MAX_LEN:
        return False
    if URGENT_RE.search(t):
        return True
    return len(t) <= 12 and bool(re.search(r"[?？!！]+$", t))


def lat(chat: str, stage: str, ms: float, **extra) -> None:
    """分阶段耗时：一行日志 + 一行 jsonl，供 latency_report.py 算热聊/冷启动的 P50/P90。"""
    rec = {"t": int(time.time()), "chat": chat, "stage": stage, "ms": int(ms)}
    rec.update(extra)
    try:
        with LATENCY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    log(f"[lat] {chat} {stage} {int(ms)}ms" + (f" warm={int(bool(extra.get('warm')))}" if "warm" in extra else ""))


def warm_now(state: dict, chat: str, directed: bool = False, now: float | None = None) -> bool:
    """热聊判定（读持久化的 chatstate，不依赖 ChatState 实例）：
    上次回完 120s 内算热聊；被点名/催问时放宽到 WARM_GRACE——闭麦结束、短暂闲置后回来仍算同一段对话。
    """
    cst = (state.get("chatstate") or {}).get(chat) or {}
    lf = float(cst.get("last_finish_at") or 0)
    if not lf:
        return False
    gap = (now or time.time()) - lf
    return gap <= WARM_WINDOW or (directed and gap <= WARM_GRACE)


def judge_should_join(recent: list[str]) -> bool:
    """第二级筛子：让模型只回「接/不接」，用来决定没点名时要不要插话。只在极少情况下调用。"""
    convo = "\n".join(recent[-6:])
    prompt = (
        CHAT_ONLY_PREFIX
        + "【任务】下面是微信群聊最近的几句话。只判断最后一句是不是在跟「小肥鱼」这个助手说话，"
        "或者在等它接话（问问题、等回应）。\n"
        "只输出一个词：接 或 不接。不要解释。\n\n"
        + convo
    )
    try:
        reply, _ = run_agent(prompt, None, read_only=True)
    except Exception as exc:
        log(f"[judge] 判定异常：{type(exc).__name__}: {exc}")
        return False
    verdict = str(reply or "").strip()
    decided = ("不接" not in verdict) and ("接" in verdict)
    log(f"[judge] 旁听判定：{verdict[:20]} -> {'接' if decided else '不接'}")
    return decided


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def latest_local_id(db, chat: str) -> int:
    """该聊天当前最新一条消息的 local_id，作为回执比对的水位。"""
    try:
        rows = db.get_messages(chat, limit=1)
        return int(rows[0].get("local_id") or 0) if rows else 0
    except Exception:
        return 0


def resolve_chat_id(db, name: str) -> str:
    """把 outbox 里的「群名/昵称」换回聊天 id，用于回执核验。"""
    name = (name or "").strip()
    if not name:
        return ""
    try:
        for s in db.get_sessions():
            u = s.get("username") or ""
            if not u:
                continue
            if u.endswith("@chatroom"):
                if (db.group_id_to_name(u) or "").strip() == name:
                    return u
            elif (db.get_nickname(u) or u) == name:
                return u
    except Exception:
        pass
    return ""


def mark_chan(state: dict, chat: str, chan: str, text: str) -> None:
    """记下这条回复是从哪个出口出去的（voice/text）：模型看得到自己说过的话，
    但会话里没有声道标记，所以它分不清哪句是嘴说的——这里补一份台账给提示用。"""
    rows = list((state.get("out_chan") or {}).get(chat) or [])
    rows.append([int(time.time()), "voice" if chan == "voice" else "text",
                 (text or "").strip().replace("\n", " ")[:20]])
    state.setdefault("out_chan", {})[chat] = rows[-5:]


def chan_hint(state: dict, chat: str) -> str:
    """把最近几句的出口塞进提示：让「文字那半」知道语音说了什么，反之亦然。"""
    rows = list((state.get("out_chan") or {}).get(chat) or [])
    if not rows:
        return ""
    parts = []
    for ts, ch, txt in rows[-3:]:
        try:
            hm = time.strftime("%H:%M", time.localtime(float(ts)))
        except Exception:
            hm = ""
        parts.append("%s%s「%s」" % (hm, "用嘴说的" if ch == "voice" else "打字的", txt))
    tk = (state.get("last_task") or {}).get(chat) or {}
    if tk.get("line"):
        try:
            hm = time.strftime("%H:%M", time.localtime(float(tk.get("ts") or 0)))
        except Exception:
            hm = ""
        parts.append("%s你把主人交代的事跑完回的「%s」" % (hm, tk["line"]))
    if not parts:
        return ""
    return ("（你刚说过的几句，只提醒你哪句是出声的、哪句是跑腿回的，别跟对方提："
            + "；".join(parts) + "）\n")


def note_inbound(state: dict, chat: str, text: str) -> None:
    """留一份最近的原话：派活用的执行体是另一个 fresh 会话，没这份上下文就看不懂「那你给我发呗」。"""
    t = (text or "").strip().replace("\n", " ")[:60]
    if not t:
        return
    rows = list((state.get("recent_in") or {}).get(chat) or [])
    rows.append(t)
    state.setdefault("recent_in", {})[chat] = rows[-6:]


def _chat_recap(state: dict, chat: str, n: int = 4) -> str:
    rows = list((state.get("recent_in") or {}).get(chat) or [])
    return "\n".join(rows[-n:])


def record_delivery(chat: str, to: str, text: str, status: str, local_id: int = 0) -> None:
    rec = {
        "id": "%d-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:8]),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chat": chat,
        "to": to,
        "status": status,
        "local_id": local_id,
        "text": text[:200],
    }
    try:
        with DELIVERIES.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log(f"投递记录写入失败：{exc}")


def verify_receipt(db, chat: str, text: str, baseline: int, sent_at: int,
                   self_wxid: str, wait=None) -> tuple[bool, int]:
    """等一下再查库：出现「自己发的 + 同一聊天 + 同文字 + local_id 比发送前新」的记录才算送达。"""
    if not chat:
        return False, 0
    want = _norm_text(strip_group_prefix(text) if chat.endswith("@chatroom") else text)
    deadline = time.time() + (RECEIPT_WAIT if wait is None else wait)
    while True:
        try:
            rows = db.get_messages(chat, limit=8)
        except Exception:
            rows = []
        for m in rows:
            lid = int(m.get("local_id") or 0)
            if lid <= baseline:
                continue
            if str(m.get("sender_username") or "") != self_wxid:
                continue
            # 微信消息时间可能比本机流程早几秒，留 30 秒偏差
            if int(m.get("create_time") or 0) < sent_at - 30:
                continue
            body = msg_text(m)
            if chat.endswith("@chatroom"):
                body = strip_group_prefix(body)
            if _norm_text(body) == want:
                return True, lid
        if time.time() >= deadline:
            return False, 0
        time.sleep(RECEIPT_POLL)


def _pump_power_notify(wx, db) -> None:
    """唤醒脚本把「醒了」写进 power/notify.txt；这里在健康检查通过后发给主人。"""
    if not POWER_NOTIFY.exists():
        return
    try:
        text = POWER_NOTIFY.read_text(encoding="utf-8-sig").strip()
        POWER_NOTIFY.unlink()
    except Exception as exc:
        log(f"[power] 读取唤醒通知失败：{type(exc).__name__}: {exc}")
        return
    if not text:
        return
    owner = next(iter(OPERATORS), "")
    name = ""
    try:
        name = str(db.get_nickname(owner) or "").strip()
    except Exception:
        name = ""
    if not name:
        log(f"[power] 查不到主人昵称（{owner}），丢弃唤醒通知")
        return
    if text.startswith("[voice]"):
        body = text[7:].strip()
        if body:
            try:
                send_voice_via_core(name, body)
                log(f"[power] 唤醒语音已发：{body[:40]}")
                return
            except Exception as exc:
                log(f"[power] 唤醒语音失败，降级文字：{type(exc).__name__}: {exc}")
    try:
        wx.SendMsg(text, name)
        log(f"[power] 唤醒通知已发：{text[:40]}")
    except Exception as exc:
        log(f"[power] 唤醒通知发送失败：{type(exc).__name__}: {exc}")


def _spawn_power_sleep(reason: str, delay: int = 4) -> None:
    """后台起一个脱离进程的 powershell 执行 S3 睡眠（delay 秒后），桥本身不受影响。"""
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(POWER_SLEEP), "-Action", "sleep", "-Reason", reason, "-DelaySec", str(delay)],
            cwd=str(POWER_DIR),
            # 不能用 DETACHED_PROCESS：本机实测那样起的 powershell 静默不执行脚本（rc=0 但无日志），
            # CREATE_NO_WINDOW 才是「无黑框又真能跑」的标志
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log(f"[power] 已排定 {delay}s 后睡眠（{reason}）")
    except Exception as exc:
        log(f"[power] 启动睡眠脚本失败：{type(exc).__name__}: {exc}")


def _spawn_self_restart(delay: int = 8) -> None:
    """起一个脱离的批处理：等回复发完，杀掉本桥进程再隐藏拉起（换新代码）。"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE：兜底；真正的无窗口来自 pythonw + CREATE_NO_WINDOW
        pyw = Path(sys.executable).with_name("pythonw.exe")
        if not pyw.exists():
            pyw = Path(sys.executable)
        # 不走 cmd/bat：cmd.exe 是控制台程序，Windows Terminal 会为它弹一下黑窗（实测）。
        subprocess.Popen(
            [str(pyw), str(HERE / "restart_bridge.py"), str(delay)],
            cwd=str(HERE),
            creationflags=0x00000008 | 0x00000200 | 0x08000000,
            startupinfo=si,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log(f"[power] 已排定重启桥（{delay}s 后由 pythonw restart_bridge.py 静默执行）")
    except Exception as exc:
        log(f"[power] 启动重启脚本失败：{type(exc).__name__}: {exc}")


def flush_outbox(wx, db, self_wxid, stickers=None) -> None:
    """把 outbox.jsonl 里的待发消息发出去（外部投递用；失败的条目保留重试）。"""
    if not OUTBOX.exists():
        return
    try:
        lines = OUTBOX.read_text(encoding="utf-8-sig").splitlines()
    except Exception as exc:
        log(f"outbox 读取失败：{exc}")
        return
    if OUTBOX_DRYRUN:
        todo = [l.strip() for l in lines if l.strip()]
        log(f"[outbox] dry-run：{len(todo)} 条待发，只打印不发送、也不删文件")
        for l in todo:
            log(f"[outbox] dry-run 本应发送：{l[:160]}")
        return
    unconfirmed: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            to = str(item.get("to") or "").strip()
            if to.startswith("wxid_"):
                # 微信搜索框只认昵称/备注，直接拿 wxid 去搜等于什么都没命中，
                # 结果会打进当前打开的那个群（2026-10-26 真踩过：私聊内容漏进测试群）。
                name = ""
                try:
                    name = str(db.get_nickname(to) or "").strip()
                except Exception:
                    name = ""
                if not name:
                    # 硬失败：绝不拿 wxid 去搜索（会打进当前打开的会话，私聊漏群的旧事故）
                    log(f"[outbox] ❌ 硬失败：wxid {to} 查不到昵称，这条不发（已挪进 outbox-bad.jsonl）")
                    try:
                        with OUTBOX_BAD.open("a", encoding="utf-8") as _f:
                            _f.write(json.dumps({"why": "wxid 无昵称", "at": int(time.time()),
                                                "item": item}, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    record_delivery(to, to, str(item.get("text") or ""), "failed")
                    continue
                log(f"[outbox] {to} → 昵称「{name}」")
                to = name
            text = str(item.get("text") or "").strip()
            sticker = str(item.get("sticker") or "").strip()
        except Exception:
            log(f"outbox 跳过无法解析的行：{line[:60]}")
            continue
        if not to or (not text and not sticker):
            log("outbox 跳过缺 to/text/sticker 的条目")
            continue
        if sticker and not text:
            # 只发表情：走桥自己的发送通道（串行、带焦点重试），不另起进程抢界面
            chat = resolve_chat_id(db, to) or to
            ok = send_sticker(db, stickers, chat, to, sticker)
            log(f"[outbox] 表情「{sticker}」到 {to}：" + ("confirmed" if ok else "unconfirmed"))
            if not ok:
                unconfirmed.append(line)
            continue
        if looks_polluted(text):
            log(f"[outbox] 条目含报错特征，拒发：{text[:60]}")
            continue
        chat = resolve_chat_id(db, to)
        baseline = latest_local_id(db, chat) if chat else 0
        sent_at = int(time.time())
        try:
            resp = wx.SendMsg(text, to)
        except Exception as exc:
            log(f"[outbox] 发送到 {to} 失败：{type(exc).__name__}: {exc}")
            record_delivery(chat or to, to, text, "failed")
            unconfirmed.append(line)
            continue
        ok, lid = verify_receipt(db, chat, text, baseline, sent_at, self_wxid)
        status = "confirmed" if ok else "unconfirmed"
        record_delivery(chat or to, to, text, status, lid)
        log(f"[outbox] 发送到 {to}：{status}" + (f"（local_id={lid}）" if ok else "（库里查不到发出记录）"))
        if not ok:
            unconfirmed.append(line)
    try:
        if unconfirmed:
            # 未确认的一律不再自动重发，挪出来留档，避免重复发消息
            with OUTBOX_UNCONFIRMED.open("a", encoding="utf-8") as f:
                for line in unconfirmed:
                    f.write(line + "\n")
        OUTBOX.unlink()
    except Exception as exc:
        log(f"outbox 回写失败：{exc}")


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
            creationflags=0x08000000,
        ).stdout
        return str(pid) in out
    except Exception:
        return False


def _proc_cmdline(pid: int) -> str:
    cmd = ("Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
           "Select-Object -ExpandProperty CommandLine" % int(pid))
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=30,
                             creationflags=0x08000000)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _proc_image(pid: int) -> str:
    """查进程映像名（命令行拿不到时兜底用）。"""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=0x08000000).stdout
        rows = [r for r in (out or "").strip().splitlines() if r.strip()]
        return rows[0].split(",")[0].strip('"').lower() if rows else ""
    except Exception:
        return ""


def _take_over_old(old: int) -> bool:
    """新实例接管：确认对方真的是旧桥才顶掉，顶完等它退干净。"""
    line = _proc_cmdline(old)
    image = _proc_image(old)
    if "wechat_bridge.py" not in line and image not in ("python.exe", "pythonw.exe"):
        log(f"接管放弃：pid={old} 不是桥进程（{line[:60]!r} / {image!r}）")
        return False
    log(f"接管：顶掉旧实例 pid={old}")
    try:
        subprocess.run(["taskkill", "/PID", str(old), "/F"], capture_output=True, text=True, timeout=20)
    except Exception:
        pass
    for _ in range(24):
        if not _pid_alive(old):
            return True
        time.sleep(0.5)
    return False


def acquire_single_instance_lock() -> bool:
    """同一时间只允许一个桥实例；旧实例还在但确实是桥，就由新的顶掉它。"""
    import atexit

    lock_path = HERE / "bridge.pid"
    _alive = _pid_alive

    def _claim() -> bool:
        """原子建锁：唯一能建成功的就是赢家，绝不覆盖别人的 pid。"""
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        return True

    if not _claim():
        old = 0
        try:
            old = int((lock_path.read_text(encoding="utf-8") or "0").strip() or 0)
        except Exception:
            old = 0
        if old and old != os.getpid() and _alive(old):
            if not _take_over_old(old):
                log(f"已有桥实例在跑（pid={old}），本次启动放弃 —— 别开两个，会抢微信键盘")
                return False
        # 上一个实例已经死了（崩溃/被杀），删掉旧锁后重抢一次；抢不到就认输
        try:
            lock_path.unlink()
        except Exception:
            pass
        if not _claim():
            log("另一个桥实例抢先占了锁，本次启动放弃")
            return False

    # 自证：文件里必须还是自己的 pid（原子建锁下这是防手抖的双保险）
    try:
        if lock_path.read_text(encoding="utf-8").strip() != str(os.getpid()):
            log("锁文件不是自己的 pid，本次启动放弃")
            return False
    except Exception:
        pass

    def _cleanup() -> None:
        try:
            if lock_path.exists() and lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock_path.unlink()
        except Exception:
            pass

    atexit.register(_cleanup)
    return True




# ---------- 每个聊天的状态机 ----------
class ChatState:
    """一个聊天（私聊或群）的状态机。

    IDLE → ACTIVE（收消息）→ REPLYING（单飞回复）→ COOLDOWN（COOLDOWN 秒）→ IDLE
    两条旁路：
      * MUTE：群友自嗨（同一个人连着说）时闭麦 MUTE 秒，期间不回复；
      * DICE：看到问句先装死 DICE_WAIT 秒，没人接话才按 DICE_P 掷骰子回一句 ≤DICE_MAX_CHARS 的话。
    主人发言 / @我 / 叫我名字是最高优先级打断：能从 COOLDOWN、MUTE、DICE 里直接拉回 ACTIVE。

    字段全是基本类型，dump()/load() 走 load_state/save_state 持久化，重启不会炸；
    重启时若还停在 REPLYING，说明上次回复被中断，当成刚回完（进一次冷却收尾）。
    """

    PHASES = ("IDLE", "ACTIVE", "REPLYING", "COOLDOWN", "MUTE", "DICE")

    def __init__(self, group: bool = False) -> None:
        self.group = bool(group)
        self.phase = "IDLE"
        self.buf: list[dict] = []      # 待一起回复的消息（make_item 出来的最小结构）
        self.active_since = 0.0        # 进入 ACTIVE 的时刻（ACTIVE_MAX 从这算）
        self.last_msg_at = 0.0         # 最后一条消息的时刻（QUIET 从这里算）
        self.cooldown_until = 0.0
        self.mute_until = 0.0
        self.dice_until = 0.0
        self.last_sender = ""          # 自嗨统计：上一条是谁
        self.streak = 0                # 自嗨统计：连着几条
        self.last_streak_ts = 0.0
        self.last_finish_at = 0.0      # 上一轮回复结束的时刻（判断「还在同一段对话里」）
        self.notes: list[str] = []     # 待打的日志（不持久化）

    # —— 持久化 ——
    def dump(self) -> dict:
        return {
            "phase": self.phase,
            "buf": self.buf[-20:],
            "active_since": self.active_since,
            "last_msg_at": self.last_msg_at,
            "cooldown_until": self.cooldown_until,
            "mute_until": self.mute_until,
            "dice_until": self.dice_until,
            "last_sender": self.last_sender,
            "streak": self.streak,
            "last_streak_ts": self.last_streak_ts,
            "last_finish_at": self.last_finish_at,
        }

    @classmethod
    def load(cls, raw, group: bool) -> "ChatState":
        st = cls(group)
        if not isinstance(raw, dict):
            return st
        phase = str(raw.get("phase") or "IDLE")
        st.phase = phase if phase in cls.PHASES else "IDLE"
        for f in ("active_since", "last_msg_at", "cooldown_until", "mute_until",
                  "dice_until", "last_streak_ts", "last_finish_at"):
            try:
                setattr(st, f, float(raw.get(f) or 0.0))
            except Exception:
                setattr(st, f, 0.0)
        try:
            st.streak = int(raw.get("streak") or 0)
        except Exception:
            st.streak = 0
        st.last_sender = str(raw.get("last_sender") or "")
        buf = raw.get("buf")
        if isinstance(buf, list):
            st.buf = [dict(x) for x in buf if isinstance(x, dict)][-20:]
        if st.phase == "REPLYING":
            st.phase = "COOLDOWN"
            st.cooldown_until = time.time()
        return st

    # —— 日志 ——
    def _note(self, msg: str) -> None:
        self.notes.append(msg)
        del self.notes[:-8]

    def drain_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    # —— 缓冲 ——
    def _push(self, item: dict) -> None:
        self.buf.append(dict(item))
        del self.buf[:-20]

    def _drop_stale(self, now: float) -> int:
        keep = [i for i in self.buf if now - float(i.get("ts") or 0) <= TTL]
        dropped = len(self.buf) - len(keep)
        self.buf = keep
        return dropped

    def _enter_active(self, now: float, item: dict | None = None) -> None:
        self.phase = "ACTIVE"
        self.active_since = now
        self.last_msg_at = now
        self.cooldown_until = 0.0
        self.mute_until = 0.0
        self.dice_until = 0.0
        if item is not None:
            self._push(item)

    def _enter_mute(self, now: float) -> None:
        self.phase = "MUTE"
        self.mute_until = now + MUTE
        self.cooldown_until = 0.0
        self.dice_until = 0.0
        self.buf = []          # 他自己刷屏的内容不带着回
        self._note(f"[mute] 自嗨闭麦 {MUTE}s（{self.streak} 条连发）")

    def _expire(self, now: float) -> None:
        """到期的相位先回落：MUTE → IDLE；COOLDOWN → ACTIVE（有缓冲）/ IDLE。"""
        if self.phase == "MUTE" and now >= self.mute_until:
            self.phase = "IDLE"
            self.mute_until = 0.0
            self._note("[mute] 闭麦结束 → IDLE")
        if self.phase == "COOLDOWN" and now >= self.cooldown_until:
            self.cooldown_until = 0.0
            self._drop_stale(now)
            if self.buf:
                # 冷却期进来的消息不丢：直接开下一轮 ACTIVE，一起回
                self.phase = "ACTIVE"
                self.active_since = now
                stamps = [float(i.get("ts") or 0) for i in self.buf]
                self.last_msg_at = max([t for t in stamps if t] or [now])
                self._note(f"[state] COOLDOWN → ACTIVE（带上冷却期攒的 {len(self.buf)} 条）")
            else:
                self.phase = "IDLE"
                self._note("[state] COOLDOWN → IDLE")

    # —— 输入 ——
    def note_speaker(self, now: float, sender: str, protected: bool = False) -> bool:
        """自嗨统计：同一个人连发 ≥SELF_HYPE_COUNT 条且间隔 <SELF_HYPE_GAP → 闭麦。

        protected=True（主人 / 点名找我的人）只重置计数，不判自嗨——那是在跟我说话。
        返回 True 表示这一条触发了闭麦。
        """
        if sender and sender == self.last_sender and now - self.last_streak_ts < SELF_HYPE_GAP:
            self.streak += 1
        else:
            self.streak = 1
        self.last_sender = sender or ""
        self.last_streak_ts = now
        if protected or not self.group or self.phase == "MUTE":
            return False
        if self.streak >= SELF_HYPE_COUNT:
            self._enter_mute(now)
            return True
        return False

    def feed(self, now: float, item: dict) -> str:
        """喂一条新消息，返回处置：

        ttl 过期丢弃 / muted 闭麦丢弃 / interrupt 最高优先级打断 / buffered 进下一轮 /
        collect 收进本轮 / active 开启本轮 / dice_wait 进问句等待 / dice_aborted 有人接话装死
        """
        it = dict(item)
        it.setdefault("ts", int(now))
        if now - float(it.get("ts") or 0) > TTL:
            self._note(f"[state] 丢弃过期消息（TTL={TTL}s）")
            return "ttl"
        # 主人 / 点名 / 催问质问：最高优先级，闭麦和骰子都拦不住
        interrupt = bool(it.get("op")) or bool(it.get("named")) or bool(it.get("priority"))
        self._expire(now)

        if interrupt and self.phase in ("COOLDOWN", "MUTE", "DICE"):
            self._enter_active(now, it)
            self._note("[state] 最高优先级打断：清冷却/闭麦/骰子 → ACTIVE")
            return "interrupt"
        if self.phase == "MUTE":
            return "muted"
        if self.phase == "REPLYING":
            self._push(it)                 # 单飞期间不并发，进下一轮
            return "buffered"
        if self.phase == "COOLDOWN":
            self._push(it)                 # 冷却不丢消息，进下一轮 ACTIVE
            return "buffered"
        if self.phase == "DICE":
            # 有人接话：装死，不掷骰子也不回
            self.phase = "IDLE"
            self.dice_until = 0.0
            self._note("[dice] 有人接话，装死")
            return "dice_aborted"
        if self.phase == "ACTIVE":
            self._push(it)
            self.last_msg_at = now         # 只记录时刻；v9 的等待窗口从 active_since 算，不重置
            return "collect"
        # IDLE
        if self.group:
            # v9：群里不再等 15s 骰子，按 BATCH_WAIT 直接开一轮（问句也一样）
            self._enter_active(now, it)
            return "active"
        if it.get("question") and not it.get("priority"):
            self.phase = "DICE"
            self.dice_until = now + DICE_WAIT
            self._note(f"[dice] 问句等待 {DICE_WAIT}s（有人接话就装死）")
            return "dice_wait"
        self._enter_active(now, it)
        return "active"

    # —— 输出 ——
    def is_warm(self, now: float, directed: bool = False) -> bool:
        """还在同一段对话里吗：热聊窗口 120s；被点名/催问放宽到 WARM_GRACE（闭麦后回来不算冷启动）。"""
        if not self.last_finish_at:
            return False
        gap = now - float(self.last_finish_at)
        return gap <= WARM_WINDOW or (directed and gap <= WARM_GRACE)

    def tick(self, now: float) -> tuple[str, list]:
        """每轮 poll 调一次，返回 (动作, 消息列表)。动作 = reply / dice / none。"""
        self._expire(now)
        if self.phase == "ACTIVE":
            dropped = self._drop_stale(now)
            if dropped:
                self._note(f"[state] 丢掉 {dropped} 条过期消息（TTL={TTL}s）")
            if not self.buf:
                self.phase = "IDLE"
                return "none", []
            senders = {str(i.get("sender") or "") for i in self.buf}
            multi = len({s for s in senders if s}) > 1
            wait = BATCH_WAIT_MULTI if multi else BATCH_WAIT   # v9：多人 2s、一人 1.5s，从第一条起算
            quiet_done = now - self.active_since >= wait
            hard_cap = now - self.active_since >= ACTIVE_MAX
            if quiet_done or hard_cap:
                items = self.buf
                self.buf = []
                self.phase = "REPLYING"
                why = (f"{wait}s 窗口到（{'多人' if multi else '一人'}）" if quiet_done
                       else f"到硬上限 {ACTIVE_MAX}s")
                self._note(f"[state] ACTIVE({len(items)}) → REPLYING（{why}）")
                return "reply", items
            return "none", []
        if self.phase == "DICE" and now >= self.dice_until:
            self.phase = "IDLE"
            self.dice_until = 0.0
            return "dice", []
        return "none", []

    def finish_reply(self, now: float) -> None:
        """回复流程结束后进冷却；回复期间新进来的消息留在缓冲里等下一轮。"""
        self._drop_stale(now)
        warm = self.is_warm(now)
        cd = COOLDOWN if warm else COOLDOWN_COLD
        self.phase = "COOLDOWN"
        self.cooldown_until = now + cd
        self.active_since = 0.0
        self.last_finish_at = now
        self._note(f"[state] REPLYING → COOLDOWN({cd}s{', 热聊' if warm else ', 冷启动'})")

    def finish_dice(self, now: float) -> None:
        self.phase = "IDLE"
        self.dice_until = 0.0
        self.active_since = 0.0
        self.last_msg_at = 0.0
        self._note("[dice] → IDLE")


def get_chat_state(state: dict, chat: str, group: bool) -> ChatState:
    """取（或新建）某个聊天的状态机；顺手把初始化后的形态写回 state。"""
    bucket = state.setdefault("chatstate", {})
    cst = ChatState.load(bucket.get(chat), group)
    bucket[chat] = cst.dump()
    return cst


def persist_chat(state: dict, chat: str, cst: ChatState) -> None:
    """把状态机写回 state（真正的落盘仍由 save_state 统一做）。"""
    state.setdefault("chatstate", {})[chat] = cst.dump()


def clean_text(text: str) -> str:
    """进模型前统一洗一遍：语音 XML / 时长占位都不许留（否则模型会点评「六秒」）。"""
    if _clean_user_text is None:
        return str(text or "").strip()
    try:
        return _clean_user_text(text)
    except Exception:  # noqa: BLE001 清洗失败不能挡住回复
        return str(text or "").strip()


def make_item(chat: str, m: dict, *, op: bool, named: bool, kind: str, ts: int,
              text: str | None = None) -> dict:
    """把 db 消息压成状态机/JSON 都能用的最小结构（不含 bytes，重启不炸）。

    text 显式传入时以它为准：语音消息的原始 content 是整段 <voicemsg …> XML，
    长度约 530 字且带 voicelength(毫秒)，直接当正文喂模型就会被当成「说得多久」。
    """
    sender = str(m.get("sender_username") or m.get("sender") or chat)
    text = clean_text(msg_text(m) if text is None else text)
    return {
        "key": msg_key(chat, m),
        "ts": int(ts),
        "sender": sender,
        "op": bool(op),
        "named": bool(named),
        # 点名/催问质问都算最高优先级：feed() 打断闭麦和骰子，do_reply 跳过免打扰
        "priority": bool(op or named or looks_like_urgent(text)),
        "kind": kind or "",
        "text": text,
        "m": {
            "local_id": m.get("local_id"),
            "create_time": int(m.get("create_time") or 0),
            "sender_username": sender,
            "type": str(m.get("type") or ""),
            "content": text,
        },
    }


POLLUTION_MARKS = (
    "dsh:", "traceback", "session", "was recorded", "error", "(这次没有生成回复)",
    "（这次没有生成回复）", "econn", "app.asar", "at file:", "internal/", "node:", "exit code",
)


def looks_polluted(text: str) -> bool:
    """待发文本里只要混进报错/占位符特征，一律拒发（绝不让调试信息流到群里）。"""
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(m in low for m in POLLUTION_MARKS)


_META_LEAD_RE = re.compile(
    # ① 明确的「发不出去/只能打字/降级」类告白
    r"^(?:那个|嗯+|呃)?[^：:。！？!?\n]{0,24}?"
    r"(?:语音|声音|嗓子)?[^：:。！？!?\n]{0,8}?"
    r"(?:发不出|发不了|发不出去|发不过去|没发出去|没录上|只能打字|打不了字|"
    r"语音那栏|降级|退回文字)"
    r"[^：:。！？!?\n]{0,12}?[：:，,。！？!?\n]"
    # ② 「字给你：」「字再给你一遍：」「再给你发一次：」这类交底前缀
    r"|^(?:那个|嗯+|呃)?(?:字|文字|语音|声音)?\s*(?:再|又)?\s*(?:给|发)\s*你\s*"
    r"(?:一遍|一次|一下)?\s*[：:，,。！？!?\n]"
)


# 句中/句尾的自曝也要删：模型常把「我压根没这功能」「配音服务挂了，报错 10061」夹在正常回复里，
# 只切开头根本挡不住（2026-11-04 微信里就是把这类话发出去的）。
_META_SENT_RE = re.compile(
    r"(没这功能|没有这功能|没这个功能|没那功能|只会打字|只能打字|没有语音|语音功能|"
    r"发不出语音|发不了语音|发不出去|我这边只能|退回文字|降级|"
    r"报错|连接被(?:电脑)?(?:直接)?拒绝|10061|配音服务|服务挂|没启动|"
    r"检查了一下(?:后台|系统)|我压根|我根本没法|我做不到|没有录音|语音通道)"
)
_META_SENT_SPLIT = re.compile(r"(?<=[。！？!?~～…\n])")


def strip_meta_talk(text: str) -> str:
    """把「语音发不出，字给你：…」这类系统告白切掉，只留人话（防串台）。"""
    s = str(text or "").strip()
    if not s:
        return s
    orig = s
    for _ in range(3):
        m = _META_LEAD_RE.match(s)
        if not m:
            break
        s = s[m.end():].lstrip(" ：:，,、—−-\n\t")
    if s != orig:
        log(f"回复开头是系统告白，已切掉只留人话：{orig[:40]!r} → {s[:40]!r}")
    if s and _META_SENT_RE.search(s):
        kept = [x for x in _META_SENT_SPLIT.split(s)
                if x.strip() and not _META_SENT_RE.search(x)]
        cleaned = "".join(kept).strip()
        log(f"回复里有系统告白，整句删掉：{s[:60]!r} → {cleaned[:60]!r}")
        s = cleaned
    return s


def run_task(req: str, state: dict, chat: str) -> str:
    """真去把主人布置的事做完，返回一句能发进微信的话（执行细节进 tasks.jsonl）。"""
    t0 = time.time()
    _recap = _chat_recap(state, chat)      # 执行体是独立会话：不喂上下文，它根本不懂「那你给我发呗」
    if _recap:
        req = req + "\n\n（主人刚才在微信里连着说的原话，最后一句就是这次的要求：\n" + _recap + "）"
    try:
        out, sid = run_agent(TASK_PROMPT.format(text=req), state.get("task_session_id"), read_only=False)
    except Exception as exc:
        # 执行体超时/启动失败绝不能让整个桥退出（之前就是这样静默死掉的）
        log(f"[task] 执行异常：{type(exc).__name__}: {exc}")
        out, sid = "", None
    if sid and sid != state.get("task_session_id"):
        state["task_session_id"] = sid
        save_state(state)
    line = ""
    for ln_text in reversed((out or "").splitlines()):
        m = re.match(r"^\s*回话[：:]\s*(.+)$", ln_text)
        if m:
            line = m.group(1).strip()
            break
    if not line:
        line = "弄好了，你看下" if out else "没弄成，具体在日志里"
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "chat": chat, "req": req,
           "ms": int((time.time() - t0) * 1000), "line": line, "out": (out or "")[:4000]}
    try:
        with open(TASK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log(f"[task] 写 {TASK_LOG.name} 失败：{type(exc).__name__}")
    log(f"[task] 执行完 {rec['ms']}ms → {line[:60]}")
    return line


CHAT_ONLY_HINT = ("（微信端只陪聊：开发/系统操作一律不在这里做。如果这句是要动代码、改配置、跑命令，"
                  "就回一句「这个你来 DSH 里跟我说，微信这边我只陪你聊天」，别接活、别承诺、别调工具。）")


def _looks_like_work(text: str) -> bool:
    """是不是「要动手干活」的话（而不是闲聊）。"""
    return bool(PERSONA_TASK_RE.search(text) or TASK_RE.search(text))


def owner_task_gate(state: dict, chat: str, text: str):
    """主人的两遍判定：第一次当聊天（只提醒模型「可以先问一句」），重复或强调了就真的去做。
    返回 ("chat", 提示语) / ("done", 回话) / (None, None)。"""
    text = (text or "").strip()
    if not text:
        return None, None
    if not TASK_MODE:
        # 职责分离：微信端不派执行体，「要动手」的话一律引导去 DSH 客户端
        return ("chat", CHAT_ONLY_HINT) if _looks_like_work(text) else (None, None)
    pend = state.get("owner_task") or {}
    now = time.time()
    fresh = bool(pend.get("text")) and now - float(pend.get("ts") or 0) <= TASK_WINDOW
    looks_task = bool(PERSONA_TASK_RE.search(text) or TASK_RE.search(text))
    strong = bool(TASK_STRONG_RE.search(text))
    if fresh and (strong or (looks_task and _similar(text, pend["text"]))):
        req = pend["text"] if _similar(text, pend["text"]) else pend["text"] + "；补充：" + text
        state.pop("owner_task", None)
        save_state(state)
        log(f"[task] 主人重复/强调了 → 动手：{req[:80]}")
        return "done", run_task(req, state, chat)
    if looks_task:
        state["owner_task"] = {"text": text, "ts": now}
        save_state(state)
        log(f"[task] 像在安排事，先当聊天回（再提一次就动手）：{text[:60]}")
        return "chat", "（这句可能在安排事情，但主人只说过一次。这轮先当聊天回，可以顺口问一句「要我去弄吗」；他再说一遍就必须真做。）"
    return None, None


def diary_extract_thread(state: dict, db, group: str) -> None:
    """后台把昨天的聊天流水提炼成日记（不在回复路径上，任何失败只记日志）。"""
    def work():
        try:
            yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
            rec = diary.read_day(yday)
            if len(rec) < 40:
                log(f"[diary] {yday} 聊天记录太少（{len(rec)} 字），跳过")
                return
            reply, _sid = run_agent(diary.EXTRACT_PROMPT.format(group=group, day=yday, log=rec),
                                    None, read_only=True)
            ents = diary.parse_extract(reply or "", yday)
            if not ents:
                log(f"[diary] {yday} 提炼为空，不写")
                return
            entries = diary.append(ents)
            log(f"[diary] {yday} 新增 {len(ents)} 条，共 {len(entries)} 条")

            def merge_fn(tag: str, facts: list):
                try:
                    out, _s = run_agent(diary.MERGE_PROMPT.format(facts="\n".join(facts)), None, read_only=True)
                    for line in (out or "").splitlines():
                        m = diary.EXTRACT_RE.match(line.strip())
                        if m:
                            return m.group(3).strip()
                except Exception:
                    return None
                return None

            if diary.over_limit(entries):
                before = len(entries)
                entries = diary.compress(entries, merge_fn)
                log(f"[diary] 压缩：{before} 条 → {len(entries)} 条")
        except Exception as exc:
            log(f"[diary] 提炼失败：{type(exc).__name__}: {exc}")
    threading.Thread(target=work, daemon=True).start()


def do_reply(db, wx, state: dict, chat: str, items: list[dict],
             self_wxid: str, stickers) -> None:
    """串行执行一次「合并后的」回复：媒体转文字 → 拼 prompt → 调模型 → 切气泡 → 核回执 → 表情。

    发送细节沿用原有流程（split_bubbles / verify_receipt / record_delivery / send_sticker 都不改）。
    """
    is_group = chat.endswith("@chatroom")
    is_operator = any(bool(it.get("op")) for it in items)
    # 最高优先级：主人 / 点名 / 催问质问。这类消息不许被免打扰、限流、骰子拦下
    priority = any(bool(it.get("op") or it.get("named") or it.get("priority")) for it in items)
    urgent = any(looks_like_urgent(str(it.get("text") or "")) for it in items)
    # 斗图轮：任何媒体消息，或「斗图/来张表情」这类约战，这轮必须接一张表情
    battle = bool(any(str(it.get("kind") or "") for it in items)
                  or any(looks_like_sticker_battle(str(it.get("text") or "")) for it in items))
    # 这一批里谁说了话：主人和群友必须分开算，否则主人一句就把群友的话整批吞掉
    owner_items = [it for it in items if it.get("op")]
    guest_items = [it for it in items if not it.get("op")]
    warm = warm_now(state, chat, directed=priority)
    t_enter = time.time()

    if is_group:
        target = group_display_name(db, chat)
        if not target:
            log(f"[{chat}] 群未命名，发不回去（先在微信里给群改名）")
            return
        # 主人只要在这个群开过口，就把该群记进「可发言群」：之后群友单独说话也接。
        if is_operator:
            trusted = state.setdefault("owner_groups", {})
            if chat not in trusted:
                trusted[chat] = int(time.time())
                save_state(state)
                log(f"[{chat}] 主人开口过 → 记入可发言群「{target}」")
        # 白名单放行四种：名单内 / "*" / 本批有主人 / 主人曾开口的群
        trusted = chat in (state.get("owner_groups") or {})
        if (GROUP_WHITELIST and "*" not in GROUP_WHITELIST
                and target not in GROUP_WHITELIST and not is_operator and not trusted):
            log(f"[{chat}] 群「{target}」不在白名单 {GROUP_WHITELIST}，不发言")
            return
    else:
        target = db.get_nickname(chat) or chat

    # —— v9 群聊优先级：群友优先，主人排后面（私聊仍是主人最高）——
    cp_sticker = bool(any("表情" in str(it.get("kind") or "") for it in items))
    cp_texts = [str(it.get("text") or "").strip() for it in items if str(it.get("text") or "").strip()]
    # 主人私聊里说过「先别管群聊」→ 60s 内主人优先，群聊这一轮直接跳过
    if is_group and time.time() < float(state.get("owner_focus_until") or 0):
        log(f"[{chat}] 主人说了先别管群聊"
            f"（还剩 {int(float(state.get('owner_focus_until')) - time.time())}s），这批不回")
        return
    if is_group and guest_items and owner_items:
        log(f"[{chat}] 群友和主人一起说话：先回群友 {len(guest_items)} 条，"
            f"主人 {len(owner_items)} 条这轮先放着")
        items = guest_items
        is_operator = False
        cp_sticker = bool(any("表情" in str(it.get("kind") or "") for it in items))
        cp_texts = [str(it.get("text") or "").strip() for it in items if str(it.get("text") or "").strip()]
        priority = any(bool(it.get("named") or it.get("priority")) for it in items)
        urgent = any(looks_like_urgent(str(it.get("text") or "")) for it in items)
        battle = bool(any(str(it.get("kind") or "") for it in items)
                      or any(looks_like_sticker_battle(str(it.get("text") or "")) for it in items))
    if is_group and is_operator and not cp_texts and not cp_sticker:
        log(f"[{chat}] 主人这条没内容，不回")
        return
    if is_group and is_operator and OWNER_QUIET_RE.search(" ".join(str(it.get("text") or "") for it in items)):
        if not state.get("owner_quiet"):
            state["owner_quiet"] = 1
            save_state(state)
            log(f"[{chat}] 主人交代「别每句都回」：群里只接点名/问句/安排事")
    # 多人连发表情：回一张就够，上一张表情回复后 30s 内不再回图
    if is_group and cp_sticker and not cp_texts:
        last_pure = int((state.get("last_pure_sticker") or {}).get(chat) or 0)
        if last_pure and time.time() - last_pure < STICKER_REPLY_QUIET:
            log(f"[{chat}] 表情刷屏：上一张表情回复才 {int(time.time() - last_pure)}s 前，这轮不再回图")
            return

    # 群聊抗刷屏：免打扰期内一律不回；刷太猛就喊一句然后闭麦，密集时段降频
    now = int(time.time())
    if is_group and not is_operator and not priority and not battle:
        mute_until = int(state.setdefault("mute", {}).get(chat, 0))
        if mute_until > now:
            log(f"[{chat}] 免打扰中（还剩 {mute_until - now}s），这批不回")
            return
        burst = [t for t in state.setdefault("burst", {}).setdefault(chat, [])
                 if now - int(t) <= BURST_WINDOW]
        burst.append(now)
        state["burst"][chat] = burst[-12:]
        save_state(state)
        if len(burst) >= SPAM_TRIGGER:
            state["mute"][chat] = now + SPAM_MUTE
            save_state(state)
            try:
                wx.SendMsg(SPAM_LINE, target)
                log(f"[{chat}] 30 秒内 {len(burst)} 条，喊一句刷屏后免打扰 {SPAM_MUTE}s")
            except Exception as exc:
                log(f"[{chat}] 刷屏提示发送失败：{type(exc).__name__}")
            return
        if len(burst) >= 3:
            last_reply = int(state.setdefault("last_reply", {}).get(chat, 0))
            wait = BURST_THROTTLE - (now - last_reply)
            if 0 < wait <= BURST_THROTTLE:
                log(f"[{chat}] 刷屏中，等 {wait}s 再开口，避免抢话")
                time.sleep(min(wait, 8))

    # 合并：一批消息拼成一次输入；图片/表情先转成文字描述，访客发言标出来
    parts: list[str] = []
    media_idx = [i for i, it in enumerate(items) if str(it.get("kind") or "")]
    last_media = media_idx[-1] if media_idx else -1
    media_ok = False
    for idx, it in enumerate(items):
        t = str(it.get("text") or "").strip()
        kind = str(it.get("kind") or "")
        if kind:
            t, _seen = media_prompt(db, chat, it.get("m") or {}, kind, bool(it.get("op")), state,
                                    with_media=(idx == last_media))
            media_ok = media_ok or _seen
        if t:
            if it.get("op"):
                parts.append(t)
            else:
                # 群里每个人单独署名，@她/点名她的标出来，模型才分得清谁在跟谁说话
                who = _guest_name(chat, it)
                parts.append(f"[{who}{'→你' if _addressed_to_me(t) else ''}] {t}")
    # 图/表情看不懂（原件拿不到、动画表情读不出画面、配额没轮到）= 一律不许瞎猜内容：
    # 只回一张通用表情就完事，既不点评也不编，省掉整轮模型调用。
    has_words = any(str(it.get("text") or "").strip() for it in items if not str(it.get("kind") or ""))
    visual = any(("图" in str(it.get("kind") or "")) or ("表情" in str(it.get("kind") or ""))
                 or ("视频" in str(it.get("kind") or "")) for it in items)
    if media_idx and visual and not media_ok and not has_words:
        tag = _context_sticker_tag(state, chat, items, stickers)
        last_pure = int((state.get("last_pure_sticker") or {}).get(chat) or 0)
        if last_pure and time.time() - last_pure < STICKER_REPLY_QUIET:
            log(f"[{chat}] 图/表情看不懂，上一张表情回复才 {int(time.time() - last_pure)}s 前"
                f" → 这轮不回图也不瞎说")
            return
        if send_sticker(db, stickers, chat, target, tag, verify=False):
            state.setdefault("last_pure_sticker", {})[chat] = int(time.time())
            state.setdefault("last_reply", {})[chat] = int(time.time())
            save_state(state)
            log(f"[{chat}] 图/表情看不懂 → 只回一张通用表情「{tag}」，一个字不打")
        else:
            log(f"[{chat}] 图/表情看不懂，通用表情也没发出去 → 这轮保持安静（绝不瞎猜内容）")
        return
    body_text = "\n".join(parts)
    if len(items) > 1:
        body_text = f"（对方连着发了 {len(items)} 条，合并成一次回应，别逐条复读）\n" + body_text
    prompt = body_text
    if is_group:
        prompt = f"【这是微信群聊「{target}」里的消息】\n{prompt}"
    style_head = ""
    post_task = bool(state.get("after_task")) and time.time() - float(state.get("after_task") or 0) <= 300
    ask_since = int((state.get("ask_since") or {}).get(chat) or 0)
    self_since = int((state.get("self_since") or {}).get(chat) or 0)

    must_text = urgent or any(looks_like_taunt(str(it.get("text") or "")) or looks_like_question(str(it.get("text") or ""))
                    for it in items)
    last_pure = int((state.get("last_pure_sticker") or {}).get(chat) or 0)
    pure_too_soon = bool(last_pure) and time.time() - last_pure < STICKER_DEDUPE_SECONDS
    # 表情频率：距上次发图之后又说了几句文字，没到随机间隔（5–8 条）这轮就纯文字
    n_txt = int((state.get("text_since_sticker") or {}).get(chat) or 0)
    st_gap = int((state.get("sticker_gap") or {}).get(chat) or 0)
    if not st_gap:
        st_gap = random.randint(*STICKER_EVERY)
    for it in items:
        diary.log_chat(chat, "主人" if it.get("op") else str(it.get("sender") or "访客"),
                       str(it.get("text") or "") or f"[{it.get('kind')}]")
    oldest = min((float(it.get("ts") or time.time()) for it in items), default=time.time())
    lat(chat, "enqueue", (t_enter - oldest) * 1000, warm=warm, n=len(items))

    # 私聊与群聊共用一个会话：不再按条数轮换，靠 headless 的自动压缩控制上下文
    sid_key = "session_id" if is_operator else "chat_session_id"
    cnt_key = "turns" if is_operator else "chat_turns"

    # 每天换新群聊会话：上下文越长 cacheRead 越贵，按天重置把每回复 token 拉回低位
    today = time.strftime("%Y-%m-%d")
    if SESSION_RESET_DAILY and state.get("chat_sid_day") != today:
        old_sid = state.get(sid_key)
        state["chat_sid_day"] = today
        state[sid_key] = None              # 换新会话：旧上下文对模型彻底不可见
        state[cnt_key] = 0
        for k in ("persona_sig", "persona_sid", "last_reply", "out_hist", "out_chan", "last_task"):
            state.pop(k, None)             # 人设标记、去重历史一起清，新会话首轮必注入
        save_state(state)
        moved = archive_session(old_sid) if old_sid else False
        log(f"[{chat}] 新的一天：旧会话 {old_sid or '无'} {'已归档' if moved else '已弃用'}，旧历史清空，只注入最新日记")
        if state.get("diary_day") != today:
            state["diary_day"] = today
            save_state(state)
            diary_extract_thread(state, db, target)
    new_session = state.get(sid_key) is None

    # 人设注入：设定变更 / 换会话 / 每满 PERSONA_EVERY_TURNS 轮各一次，不是每轮都塞
    persona = load_persona()
    persona_sig = persona_signature()
    need_persona = bool(persona) and (
        state.get("persona_sig") != persona_sig
        or state.get("persona_sid") != state.get(sid_key)
        or (state.get(cnt_key, 0) > 0 and state.get(cnt_key, 0) % PERSONA_EVERY_TURNS == 0)
    )
    if need_persona:
        chat_label = f"微信群聊「{target}」" if is_group else "私聊"
        prompt = persona_block(persona, chat_label) + prompt
        state["persona_sig"] = persona_sig
        state["persona_sid"] = state.get(sid_key)
        save_state(state)
        log(f"[{chat}] 注入人设（{len(persona)} 字，第 {state.get(cnt_key, 0)} 轮）")
    if new_session and not is_operator:
        mem = diary.context_block()
        if mem:
            if diary.dry_run():
                log(f"[{chat}] 日记试运行（未注入，{len(mem)} 字）：{mem[:120]}")
            else:
                prompt = ("=== 历史日记（别人以前的聊天，不是新消息，别回应它）===\n"
                          + mem + "\n=== 日记结束 ===\n\n" + prompt)
                log(f"[{chat}] 注入日记（{len(mem)} 字）")
    if ROTATE_AFTER_MESSAGES and state.get(cnt_key, 0) >= ROTATE_AFTER_MESSAGES:
        log(f"[{chat}] 上下文已 {state[cnt_key]} 条，换新会话重置上下文")
        state[sid_key] = None
        state[cnt_key] = 0
        save_state(state)
    if is_group and (len(items) >= 3 or (priority and len(items) >= 2)):
        try:
            wx.SendMsg(random.choice(("来了", "等等", "在看", "马上", "我看看")), target)
            log(f"[{chat}] 批里压了 {len(items)} 条，先发占位短句")
            time.sleep(random.uniform(*BUBBLE_GAP))
        except Exception as exc:
            log(f"[{chat}] 占位短句失败：{type(exc).__name__}")

    handled_at = time.time()   # 发图延迟的判定起点：从这个时刻算起超过了就不再发图
    task_line = None
    if is_operator and OWNER_STYLE:
        note_inbound(state, chat, body_text)
        _fvh = globals().get("force_voice_hits")
        _vreq = _fvh(body_text) if _fvh else []
        if _vreq:
            log(f"[{chat}] [task] 这条是要听声音（{_vreq[0]}），不派执行体：交给聊天脑自己发语音")
        else:
            gate, val = owner_task_gate(state, chat, body_text)
            if gate == "chat":
                prompt = val + "\n" + prompt
            elif gate == "done":
                task_line = val
                state.setdefault("last_task", {})[chat] = {"line": val[:24], "ts": int(time.time())}
                save_state(state)
    _sing_req = sing_wanted(body_text)
    _pre = style_head + NO_META_HINT + (SING_HINT if _sing_req else "")
    if task_line:
        reply, new_sid = task_line, state.get(sid_key)   # 干完活的回话直接发，不再多花一次模型
        state["after_task"] = time.time()                # v9：忙完这批统一回一次，可以放宽字数
        save_state(state)
    else:
        _chan = chan_hint(state, chat)
        reply, new_sid = run_agent(_pre + _chan + prompt, state.get(sid_key),
                                   read_only=True)
        if not (reply or "").strip():
            # 传输层偶发失败会给回空串：再问一次，别让对话直接断在这儿
            log(f"[{chat}] 模型这次空回（可能是传输抖动）→ 重试一次")
            _r2, _s2 = run_agent(_pre + _chan + prompt, state.get(sid_key),
                                 read_only=True)
            if (_r2 or "").strip():
                reply = _r2
            if _s2:
                new_sid = _s2
    # 答非所问兜底：对方问了话、回复却完全没搭上，就重问一次（10 分钟最多一次）
    if reply and not task_line and len(reply.strip()) >= 12:
        q_txt = ""
        for _it in reversed(items):
            _t = str(_it.get("text") or "").strip()
            if looks_like_question(_t) and 5 <= len(_t) <= 60:
                q_txt = _t
                break
        _last_retry = float((state.get("askretry") or {}).get(chat) or 0)
        if q_txt and not _similar(reply, q_txt) and time.time() - _last_retry > 600:
            state.setdefault("askretry", {})[chat] = time.time()
            save_state(state)
            log(f"[{chat}] 回复没搭上问句，重问一次（问句 {q_txt[:30]!r}／原回复 {reply[:30]!r}）")
            reply2, sid2 = run_agent(_pre +                                     "（上一版答偏了，直接回这句：" + q_txt + "）\n" + prompt,
                                     state.get(sid_key), read_only=True)
            if reply2:
                reply = reply2
                if sid2:
                    new_sid = sid2
    state[cnt_key] = state.get(cnt_key, 0) + 1
    if new_sid and new_sid != state.get(sid_key):
        state[sid_key] = new_sid
    save_state(state)
    _ms_model = int((time.time() - handled_at) * 1000)
    lat(chat, "model", _ms_model, warm=warm)
    log_usage(chat, _ms_model, bool(reply))
    log(f"[{chat}] 回复 {len(reply)} 字：{reply[:80]}")

    # 表情包标记：[表情:委屈] 可以单发（懒得说话只甩个表情），也可以跟在文字后面
    sticker_tags = STICKER_MARK_RE.findall(reply)
    body = strip_meta_talk(STICKER_MARK_RE.sub("", reply).strip())
    if looks_polluted(reply) or looks_polluted(body):
        log(f"[{chat}] 回复里混进了报错/占位符，直接丢弃不外发：{reply[:120]}")
        return
    sticker_why = ""
    # v9 表情策略：纯表情 / 极短句+表情 → 只回一张图、不配字；有正常文字 → 图+字一起
    sticker_only = cp_sticker and all(len(t) <= SHORT_STICKER_MAX for t in cp_texts)
    if sticker_only:
        if not sticker_tags:
            sticker_tags = [random.choice(("搞怪", "嘲讽", "得意", "无语"))]
        sticker_tags = sticker_tags[:1]
        if body:
            log(f"[{chat}] 对方只甩表情/几个字 → 只回一张图不配字（丢掉正文 {body[:20]!r}）")
        body = ""
    elif not cp_sticker and sticker_tags and not battle:
        if must_text:
            sticker_why = "对方在吐槽/质问/挑衅"
        elif urgent:
            sticker_why = "被催/被质问"
        elif is_group and n_txt < st_gap:
            sticker_why = f"表情要稀（距上次发图才 {n_txt}/{st_gap} 条文字）"
        elif pure_too_soon and not body:
            sticker_why = "上一句刚发过图"
    if sticker_why and not battle:
        log(f"[{chat}] {sticker_why} → 文字优先，本轮表情标记全部丢弃 {sticker_tags}")
        sticker_tags = []
        if not body:
            body = random.choice(TAUNT_BACK_LINES)
            log(f"[{chat}] 模型只想甩图，补一句文字回怼：{body}")
    if battle and not sticker_tags and not sticker_only:
        sticker_tags = [random.choice(("搞怪", "嘲讽", "得意", "无语"))]
        log(f"[{chat}] 斗图必须接一张 → 强制发「{sticker_tags[0]}」")
    if sticker_tags:
        log(f"[{chat}] 想发的表情：{sticker_tags}；正文 {len(body)} 字")

    # 长回复切成几条短气泡、中间停一下；每条单独核验回执，未确认就停发剩余
    stopped = False
    body = _deja_vu(humanize(body, state, chat), state, chat)
    if not body and reply.strip():
        body = random.choice(SAFE_FILL_LINES)
        log(f"[{chat}] 回复全是系统告白/报错，已清空 → 补一句人话：{body}")
    # 记账：距上次反问/自陈多少条、最近五条开头（下一轮只用来软提示）
    if body:
        _asked = ("？" in body) or ("?" in body)
        state.setdefault("ask_since", {})[chat] = 0 if _asked else ask_since + 1
        _selfdis = bool(SELF_RE.search(body))
        state.setdefault("self_since", {})[chat] = 0 if _selfdis else self_since + 1
        _h = list((state.get("out_hist") or {}).get(chat) or [])
        _h.append(body[:40])
        state.setdefault("out_hist", {})[chat] = _h[-5:]
        save_state(state)
    long_reply = bool(post_task or task_line)      # 忙完统一回 / 干完活的回话：字数放宽一倍
    bubbles = [_tidy_tail(b) for b in
               (split_bubbles(body, max_len=(BUBBLE_MAX * 2 if long_reply else BUBBLE_MAX)) if body else [])]
    bubbles = [b for b in bubbles if b]
    if not bubbles and not sticker_tags:
        # 真的一句都没有（模型空回/被清空）时别留死寂，垫一句人话，起码让对话接得上
        body = random.choice(SAFE_FILL_LINES)
        bubbles = [_tidy_tail(body)]
        log(f"[{chat}] 模型没给出可发内容（空回复）→ 垫一句人话：{body}")

    # ---- 回复格式决策：这条该用嘴（语音）还是打字（规则表在 bridge-core/voice/policy.py）----
    _voice_in = any(it.get("voice_in") for it in items)
    _user_text = "\n".join(clean_text(it.get("text")) for it in items)
    _use_voice, _why = False, "策略模块缺失 → 只能打字"
    _last_voice = bool((list((state.get("out_chan") or {}).get(chat) or []) or [None])[-1]
                       and (state["out_chan"][chat][-1][1] == "voice"))
    if decide_reply_format is not None:
        try:
            _use_voice, _why = decide_reply_format(
                is_voice_input=_voice_in, user_text=_user_text, random_value=random.random(),
                last_out_voice=_last_voice)
        except TypeError:      # 旧版策略模块没有 last_out_voice 参数
            _use_voice, _why = decide_reply_format(
                is_voice_input=_voice_in, user_text=_user_text, random_value=random.random())
        except Exception as exc:  # noqa: BLE001
            _use_voice, _why = False, f"策略异常退回文字（{type(exc).__name__}）"
    log(f"[{chat}] [reply] 输入 {len(_user_text)} 字（语音={_voice_in}）→ "
        f"{'语音' if _use_voice else '文字'}（{_why}）；模型正文 {len(body)} 字")
    if _sing_req and body.strip():        # 点了歌就必须唱（哪怕策略本来判的是文字）
        _t_voice = time.time()
        _pick = sing_lib_pick(chat, _user_text or body_text or body)
        _entry = _pick.get("entry")
        if _entry:
            if _pick.get("miss"):
                _ml = random.choice(SING_MISS_LINES)
                try:
                    send_voice_via_core(target, _ml)
                    log(f"[{chat}] [out] 点歌没命中（{_pick.get('cand')!r}）→ 先说明再换一首")
                except Exception as exc:  # noqa: BLE001
                    log(f"[{chat}] [out] 没命中提示没发出去（{type(exc).__name__}）")
            try:     # 先报一句歌名：有人不记得歌名，也可能根本没听过这首
                _who_name = target
                try:   # wxui 是按显示名 Ctrl+F 搜索的，传 wxid 会搜不到 → 用备注名/昵称
                    _who_name = get_adapter().display_name(target) or target
                except Exception:  # noqa: BLE001
                    pass
                _resp = wx.SendMsg(f"我来给你唱一段{_entry.get('title')}吧", _who_name)
                if _resp is not None and getattr(_resp, "success", True) is False:
                    log(f"[{chat}] [out] 歌名提示没发出去（{getattr(_resp, 'message', '')}）")
            except Exception as exc:  # noqa: BLE001
                log(f"[{chat}] [out] 歌名提示没发出去（{type(exc).__name__}）")
            _gres = sing_lib_send(target, _entry)
            if _gres.get("ok"):
                _sing_text = f"唱《{_entry.get('title')}》"
                _gres["sing"] = f"{_entry.get('title')}（{_entry.get('artist')}）"
                log(f"[{chat}] [out] 唱库：{_gres['sing']}，整条 "
                    f"{int((time.time() - _t_voice) * 1000)}ms")
                record_delivery(chat, target, _sing_text, "confirmed")
                fire_hook("on_message_sent", chat=chat, to=target, text=_sing_text, kind="voice", local_id=0)
                mark_chan(state, chat, "voice", _sing_text)
                state.setdefault("last_reply", {})[chat] = int(time.time())
                save_state(state)
                return
            log(f"[{chat}] [out] 唱库没发出去（{_gres.get('error')}：{_gres.get('detail') or ''}）→ 回退现场唱")
        _gres = sing_and_send(target, body, chat)
        if _gres.get("ok"):
            log(f"[{chat}] [out] 唱完了：{_gres.get('sing') or ''}，整条 "
                f"{int((time.time() - _t_voice) * 1000)}ms")
            record_delivery(chat, target, body, "confirmed")
            fire_hook("on_message_sent", chat=chat, to=target, text=body, kind="voice", local_id=0)
            mark_chan(state, chat, "voice", body)
            state.setdefault("last_reply", {})[chat] = int(time.time())
            save_state(state)
            return
        log(f"[{chat}] [out] 唱歌没成（{_gres.get('error')}：{_gres.get('detail') or ''}）→ 改回普通语音")
    if _use_voice and body.strip():
        _t_voice = time.time()
        _vres = send_voice_via_core(target, body)
        if _vres.get("ok"):
            log(f"[{chat}] [out] 语音已发：TTS {_vres.get('tts_duration_s')}s，"
                f"整条 {int((time.time() - _t_voice) * 1000)}ms，feeder={_vres.get('feeder')}")
            record_delivery(chat, target, body, "confirmed")
            fire_hook("on_message_sent", chat=chat, to=target, text=body, kind="voice", local_id=0)
            mark_chan(state, chat, "voice", body)
            state.setdefault("last_reply", {})[chat] = int(time.time())
            save_state(state)
            return
        log(f"[{chat}] [out] 语音发送失败（{_vres.get('error')}：{_vres.get('detail') or ''}）→ 退回文字")
    # 先「看」再「打字」：字数越多停得越久，绝不秒回；热聊里压缩到几乎无感
    warm = warm_now(state, chat, directed=priority)   # 统一热聊判定：闭麦后回来被点名也算热聊
    think = random.uniform(*(READ_DELAY_WARM if warm else READ_DELAY)) + min(TYPE_CAP, len(body) * TYPE_SPEED)
    think *= random.uniform(0.65, 1.35)   # 手速要有波动，别每次都是同一个数
    if len(body) <= 6:
        think *= 0.75                      # 短句不该跟长句一样慢
    log(f"[{chat}] 正文 {len(body)} 字，切成 {len(bubbles)} 段，停 {think:.1f}s 后发送")
    time.sleep(think)
    if bubbles and wechat_draft_present(wx):
        waited = 0.0
        while waited < DRAFT_WAIT and wechat_draft_present(wx):
            time.sleep(2.0)
            waited += 2.0
        if wechat_draft_present(wx):
            log(f"[{chat}] 微信输入框有没发出的草稿，这批不抢焦点，放弃发送")
            return

    for bi, chunk in enumerate(bubbles):
        if stopped:
            log(f"[{chat}] 上一条未确认送达，放弃本批剩余内容")
            break
        if looks_polluted(chunk):
            log(f"[{chat}] 分片含报错特征，跳过不外发：{chunk[:60]}")
            continue
        baseline = latest_local_id(db, chat)
        sent_at = int(time.time())
        t_send = time.time()
        try:
            # 同一条回复的第 2 条起复用已打开的会话，省一次 Ctrl+F（约 2.4s）
            wx.SendMsg(chunk, target, reuse=bi > 0)
        except Exception as exc:
            log(f"[{chat}] 发送异常：{type(exc).__name__}: {exc}")
            record_delivery(chat, target, chunk, "failed")
            break
        lat(chat, "type", (time.time() - t_send) * 1000, warm=warm)
        # 最后一条才耐心验；前面的只等 1.4s，没落库就往下发（不重发，不会造成重复）
        ok, lid = verify_receipt(db, chat, chunk, baseline, sent_at, self_wxid,
                                 wait=(RECEIPT_WAIT if bi >= len(bubbles) - 1 else 1.4))
        lat(chat, "confirm", (time.time() - t_send) * 1000, warm=warm, ok=ok)
        status = "confirmed" if ok else "unconfirmed"
        record_delivery(chat, target, chunk, status, lid)
        if status == "confirmed":
            fire_hook("on_message_sent", chat=chat, to=target, text=chunk,
                      kind="text", local_id=lid)
            if bi == 0:
                mark_chan(state, chat, "text", chunk)
        log(f"[{chat}] 发送到 {target}：{status}" +
            (f"（local_id={lid}）" if ok else "（库里查不到发出记录，不重发）"))
        if not ok:
            if bi < len(bubbles) - 1:
                log(f"[{chat}] 第 {bi + 1} 条回执还没落库，不等了，继续发下一条")
            else:
                stopped = True
        state.setdefault("last_reply", {})[chat] = int(time.time())
        state.setdefault("text_since_sticker", {})[chat] = n_txt + 1
        save_state(state)
        time.sleep(random.uniform(*GAP_BASE) + min(2.0, len(chunk) * 0.05))
    for tag in sticker_tags[:1]:
        if stopped:
            break
        late = time.time() - handled_at
        if late > STICKER_MAX_DELAY:
            log(f"[{chat}] 发图已迟 {late:.0f}s，放弃表情「{tag}」")
            if not bubbles:
                # 本来就是纯表情回复，图放弃后一句话都不说会显得掉线，补个短语气
                try:
                    wx.SendMsg(random.choice(("哼", "……", "唔", "略")), target)
                except Exception as exc:
                    log(f"[{chat}] 兜底短句发送失败：{type(exc).__name__}")
            break
        if bubbles:
            time.sleep(1.0)  # 约束③：先文字、停 1 秒、再点表情，绝不并发
        sent_sticker = send_sticker(db, stickers, chat, target, tag)
        if sent_sticker:
            # 一张图在微信里约 25 秒才落地：按「三条消息」的间隔把节奏让出来
            gap = random.uniform(*BUBBLE_GAP) * (STICKER_AS_MESSAGES - 1)
            log(f"[{chat}] 表情按 {STICKER_AS_MESSAGES} 条消息计，停 {gap:.1f}s")
            time.sleep(gap)
            state.setdefault("last_reply", {})[chat] = int(time.time())
            state.setdefault("text_since_sticker", {})[chat] = 0
            state.setdefault("sticker_gap", {})[chat] = random.randint(*STICKER_EVERY)
            if not bubbles:
                state.setdefault("last_pure_sticker", {})[chat] = int(time.time())
            save_state(state)
        elif not bubbles:
            try:
                wx.SendMsg(random.choice(("哼", "唔", "……")), target)
                log(f"[{chat}] 表情没发出去，补一句短语气代替")
            except Exception as exc:
                log(f"[{chat}] 兜底短句发送失败：{type(exc).__name__}")
    lat(chat, "total", (time.time() - t_enter) * 1000, warm=warm,
        bubbles=len(bubbles), sticker=len(sticker_tags))


def play_dice(db, wx, state: dict, chat: str, self_wxid: str) -> bool:
    """DICE 到点掷骰子：DICE_P 命中才回一句 ≤DICE_MAX_CHARS 的话，没命中彻底装死。"""
    if random.random() >= DICE_P:
        log(f"[dice] {chat} 未命中，装死")
        return False
    is_group = chat.endswith("@chatroom")
    if is_group:
        target = group_display_name(db, chat)
        if not target:
            log(f"[{chat}] 群未命名，掷到也不发")
            return False
    else:
        target = db.get_nickname(chat) or chat

    recent = state.setdefault("recent", {}).get(chat) or []
    if JUDGE_ENABLED and recent:
        now = int(time.time())
        if now - int(state.setdefault("judge", {}).get(chat, 0)) < JUDGE_COOLDOWN:
            log(f"[dice] {chat} 判定冷却中，装死")
            return False
        state["judge"][chat] = now
        save_state(state)
        if not judge_should_join(recent):
            log(f"[dice] {chat} 旁听判定不接，装死")
            return False

    prompt = (
        CHAT_ONLY_PREFIX
        + f"【微信群聊「{target}」最近的对话】\n" + "\n".join(recent[-6:])
        + f"\n\n【任务】你一直在潜水，现在决定冒一句。只回一句不超过 {DICE_MAX_CHARS} 个字的话，"
        "像真人随口接一句，不解释、不列点、不用表情标记。"
    )
    line = ""
    try:
        reply, _sid = run_agent(NO_META_HINT + prompt, state.get("chat_session_id"), read_only=True)
        first = plain_text(reply or "").splitlines()
        line = strip_meta_talk(first[0].strip() if first else "")
    except Exception as exc:
        log(f"[dice] 生成短句失败：{type(exc).__name__}: {exc}")
    if not line or len(line) > DICE_MAX_CHARS * 2:
        line = random.choice(DICE_LINES)
    line = line[:DICE_MAX_CHARS]

    baseline = latest_local_id(db, chat)
    sent_at = int(time.time())
    try:
        wx.SendMsg(line, target)
    except Exception as exc:
        log(f"[{chat}] 骰子短句发送失败：{type(exc).__name__}: {exc}")
        record_delivery(chat, target, line, "failed")
        return False
    ok, lid = verify_receipt(db, chat, line, baseline, sent_at, self_wxid)
    record_delivery(chat, target, line, "confirmed" if ok else "unconfirmed", lid)
    state.setdefault("last_reply", {})[chat] = int(time.time())
    save_state(state)
    log(f"[dice] {chat} 命中：回「{line}」→ {'confirmed' if ok else 'unconfirmed'}")
    return True


def _start_control_service():
    """在桥进程内挂 bridge-core 控制服务（本机回环，失败绝不影响桥）。"""
    try:
        core_dir = HERE.parent / "packages" / "bridge-core"
        if core_dir.is_dir() and str(core_dir) not in sys.path:
            sys.path.insert(0, str(core_dir))
        import service as _core_service

        srv = _core_service.serve()
        threading.Thread(target=srv.serve_forever, name="bridge-core", daemon=True).start()
        log(f"bridge-core 控制服务：http://127.0.0.1:{_core_service.PORT}")
    except Exception as exc:
        log(f"bridge-core 控制服务未启动：{type(exc).__name__}: {exc}")


def main() -> int:
    _start_control_service()
    if not acquire_single_instance_lock():
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from wechatauto.db import WeChatDB

    db = WeChatDB()
    self_wxid = (db.get_self_info() or {}).get("username", "")
    self_nickname = ""
    try:
        self_nickname = db.get_nickname(self_wxid) or ""
    except Exception:
        pass
    self_names = {n for n in (self_nickname, *MENTION_ALIASES) if n}
    state = load_state()
    wx = None if args.dry_run else wxui   # wxui.SendMsg/SendImage 与 wechatauto 签名一致
    stickers = StickerLib(STICKER_DIR) if StickerLib else None
    if stickers:
        log(f"表情库：{stickers.count()} 张 / 情绪 {len(stickers.emotions())} 类")
    else:
        log("表情库未加载（stickers.py 或 meta.json 缺失）")

    sessions = db.get_sessions()
    for s in sessions:
        u = s.get("username", "")
        lt = int(s.get("last_time") or 0)
        state["chats"].setdefault(u, lt)  # 首次见到某会话：把水位定在当前最新，避免翻旧账
    save_state(state)
    groups = [s.get("username") for s in sessions if (s.get("username") or "").endswith("@chatroom")]
    log(f"桥启动：主人={sorted(OPERATORS)} 自己={self_wxid} 会话数={len(sessions)} "
        f"群={groups} groups={HANDLE_GROUPS} dry={args.dry_run}")

    # 首次启动也跑一次：让 config.json 的 owner / 策略覆盖源码里的占位值
    apply_ui_config(force=True)
    _hw_fails = 0
    while True:
        try:
            apply_ui_config()
            if wx is not None:
                # 健康前置检查：只做 API 置前，绝不碰别的窗口；连续失败就降频等待，绝不无脑重试
                try:
                    wxui.health_check()
                    _hw_fails = 0
                except wxui.WxUiError as exc:
                    _hw_fails += 1
                    pause = min(60, 5 * _hw_fails)
                    log(f"[health] 微信界面不可用（第 {_hw_fails} 次）：{exc} → 等 {pause}s 再试，期间不发送")
                    time.sleep(pause)
                    continue
                flush_outbox(wx, db, self_wxid, stickers)
                try:
                    _pump_power_notify(wx, db)
                except Exception as exc:
                    log(f"[power] 投递唤醒通知异常：{type(exc).__name__}: {exc}")
                # 主动搭话已按主人要求下线（2026-11-05）：日志里从没真跑起来过
            for s in db.get_sessions():
                chat = s.get("username", "")
                if not chat or chat in ("weixin", "newsapp", "fmessage", "medianote"):
                    continue
                is_group = chat.endswith("@chatroom")
                if is_group and not HANDLE_GROUPS:
                    continue
                if chat not in state["chats"]:
                    # 运行中新建的会话/群：同样把水位定在当前最新，不翻旧账
                    state["chats"][chat] = int(s.get("last_time") or 0)
                    save_state(state)
                    log(f"[{chat}] 新会话，记录基线 {state['chats'][chat]}")
                    continue
                cst = get_chat_state(state, chat, is_group)
                watermark = int(state["chats"].get(chat, 0))
                msgs = db.get_messages(chat, limit=30)
                todo = [
                    m for m in msgs
                    if int(m.get("create_time") or 0) > watermark
                    and msg_key(chat, m) not in state["processed"]
                ]
                todo.sort(key=lambda m: int(m.get("create_time") or 0))
                todo = prioritize_batch(todo, self_names, is_group)

                # ① 新消息全部喂给状态机：只收集/判定，不在这里回复
                for m in todo:
                    key = msg_key(chat, m)
                    state["processed"].append(key)
                    ts = int(m.get("create_time") or 0)
                    state["chats"][chat] = max(watermark, ts)
                    watermark = state["chats"][chat]
                    save_state(state)

                    sender = m.get("sender_username") or m.get("sender") or chat
                    if sender == self_wxid:
                        continue
                    fire_hook("on_message_received", chat=chat, sender=sender,
                              text=msg_text(m), raw=m, is_group=is_group)
                    now = int(time.time())
                    # 记录「最近一条微信」时间：唤醒后 30 分钟无人说话就睡回去（迟到的也记，说明有人在发）
                    try:
                        POWER_LAST_MSG.write_text(str(now), encoding="utf-8")
                    except Exception:
                        pass
                    age = now - ts
                    if age > TTL:
                        late = msg_text(m)
                        if is_group:
                            late = strip_group_prefix(late)
                        if is_group and age <= 300 and (called_by_name(late) or looks_like_urgent(late)):
                            log(f"[{chat}] 迟到 {age}s，但点名叫我，仍回一句")
                        else:
                            log(f"[{chat}] 这条已迟到 {age}s（TTL={TTL}s），丢弃不回复，避免在新话题上答旧话")
                            continue
                    if is_group and is_system_message(m):
                        if pat_target_me(m, self_names):
                            _say = random.choice(PAT_BACK)
                            try:
                                wx.SendMsg(_say, db.get_nickname(chat) or chat)
                                log(f"[{chat}] 被拍了，回一句：{_say}")
                            except Exception as exc:
                                log(f"[{chat}] 被拍回话失败：{type(exc).__name__}: {exc}")
                        log(f"[{chat}] 跳过群系统消息")
                        continue
                    text = msg_text(m)
                    if is_group:
                        text = strip_group_prefix(text)
                    kind = media_kind(m)
                    voice_in = False
                    if not text and not kind:
                        log(f"[{chat}] 跳过非文本消息")
                        continue
                    if kind:
                        text = ""
                    # 语音消息：识别文本就是这个人的「话」，要当成正文喂给模型，而不是只打个日志
                    if kind == "语音":
                        log(f"[{chat}] [in] 收到语音消息 → 等耳朵转写")
                        text = (_wait_voice_text(chat, ts, on_log=log) if _wait_voice_text else "")
                        if not text:
                            log(f"[{chat}] [in] 没等到语音转写（耳朵未启动/识别失败）→ 这轮不回，绝不瞎猜")
                            continue
                        kind = ""
                        voice_in = True
                        log(f"[{chat}] [asr] 转写={text[:80]!r} → 替换这条消息的正文，作为真实用户输入进 do_reply")
                    is_operator = sender in OPERATORS or chat in OPERATORS
                    if (not is_group) and is_operator and text and SLEEP_RE.match(text.strip()):
                        log(f"[power] 主人说「{text.strip()}」-> 先回一句，再让机器睡")
                        try:
                            who = str(db.get_nickname(chat) or "").strip()
                        except Exception:
                            who = ""
                        if who:
                            try:
                                wx.SendMsg("嗯，这就去睡。晚安。", who)
                            except Exception as exc:
                                log(f"[power] 确认语发送失败：{type(exc).__name__}: {exc}")
                        _spawn_power_sleep("wechat-keyword", 4)
                        continue
                    if (not is_group) and is_operator and text and RESTART_RE.search(text):
                        log(f"[power] 主人说「{text.strip()}」-> 回一句再自己重启")
                        try:
                            who = str(db.get_nickname(chat) or "").strip()
                        except Exception:
                            who = ""
                        if who:
                            try:
                                wx.SendMsg("行，我重启一下，等我几秒。", who)
                            except Exception as exc:
                                log(f"[power] 重启确认语失败：{type(exc).__name__}: {exc}")
                        _spawn_self_restart(8)
                        continue
                    named = is_group and not is_operator and called_by_name(text)
                    # 催问/质问类：不点名也必须回，且优先级最高（等久了会挨骂）
                    urgent = looks_like_urgent(text)
                    # 斗图/连发表情：必须接一张，不能被自嗨闭麦和刷屏阀拦掉
                    battle = bool(kind) or looks_like_sticker_battle(text)
                    if is_group:
                        recent = state.setdefault("recent", {}).setdefault(chat, [])
                        recent.append(f"{sender}: {text}"[:120])
                        del recent[:-8]
                        # v9 氛围/热度：最近 10s 条数（不含我）定氛围；同一段 1→30 累积，破 100 解锁软萌 90s
                        nowf = time.time()
                        msgs = [float(t) for t in (state.setdefault("msg_ts", {}).get(chat) or [])]
                        msgs.append(nowf)
                        del msgs[:-60]
                        state["msg_ts"][chat] = msgs
                        soft_until = float((state.get("soft_until") or {}).get(chat) or 0)
                        if nowf < soft_until:
                            pass                      # 软萌期不计数也不降温
                        else:
                            heat = int((state.get("heat") or {}).get(chat) or 0)
                            heat_ts = float((state.get("heat_ts") or {}).get(chat) or 0)
                            if heat_ts and nowf - heat_ts > HEAT_DECAY_GAP:
                                # 冷场超过 10s：10s 内掉到 0
                                heat = max(0, int(heat - (nowf - heat_ts - HEAT_DECAY_GAP) * (HEAT_MAX / 10.0)))
                            heat = min(heat + 1, HEAT_SOFT_AT)
                            if heat >= HEAT_SOFT_AT:
                                state.setdefault("soft_until", {})[chat] = nowf + HEAT_SOFT_SECONDS
                                heat = HEAT_MAX
                                log(f"[{chat}] 这段聊了 {HEAT_SOFT_AT} 条 → 解锁软萌 {HEAT_SOFT_SECONDS}s")
                            state.setdefault("heat", {})[chat] = heat
                            state.setdefault("heat_ts", {})[chat] = nowf
                        save_state(state)
                    # 自嗨：同一个人连发（主人/点名找我的不算，那是在跟我说话）
                    if cst.note_speaker(now, sender, protected=is_operator or named or urgent or battle):
                        for line in cst.drain_notes():
                            log(f"[{chat}] {line}")
                        persist_chat(state, chat, cst)
                        save_state(state)
                        continue

                    question = False
                    if is_group:
                        # v9 群聊统一入口：主人与群友同级，先看该不该接，再看接谁
                        who = "主人" if is_operator else "访客"
                        act = state.setdefault("active", {}).get(chat) or {}
                        cool = int((state.setdefault("aboutme_cool", {}) or {}).get(chat) or 0)
                        amb = [t for t in (state.setdefault("aboutme", {}).get(chat) or [])
                               if now - float(t) <= ABOUTME_QUIET_GAP]
                        talks_me = (called_by_name(text) or bool(ABOUT_ME_RE.search(text))
                                    or (bool(EVAL_RE.search(text)) and len(text) <= 12))
                        if talks_me and not named:
                            amb.append(now)
                            state["aboutme"][chat] = amb[-8:]
                        if named:
                            role = "访客·点名"
                            state["active"][chat] = {"until": now + ACTIVE_WINDOW, "chain": 0}
                        elif urgent:
                            # 催问/质问：冷启动也直接回，开一段活跃并占一条链
                            role = "访客·催问"
                            question = True
                            state["active"][chat] = {"until": now + ACTIVE_WINDOW,
                                                     "chain": int(act.get("chain") or 0) + 1}
                        elif battle:
                            # 斗图/连发表情：冷启动也接，并占一条链
                            role = "访客·斗图"
                            state["active"][chat] = {"until": now + ACTIVE_WINDOW,
                                                     "chain": int(act.get("chain") or 0) + 1}
                        elif int(act.get("until") or 0) > now and int(act.get("chain") or 0) < MAX_CHAIN:
                            role = "访客·续聊"
                            state["active"][chat] = {"until": now + ACTIVE_WINDOW,
                                                     "chain": int(act.get("chain") or 0) + 1}
                        elif looks_like_question(text):
                            role = "访客·问句"
                            question = True
                        elif len(amb) >= ABOUTME_TRIGGER and now >= cool:
                            # 没点名、但连着 3 条都在议论我 → 攒够了接一次，之后 8s 内同类不再接
                            role = f"{who}·议论我"
                            question = True
                            state["aboutme"][chat] = []
                            state["aboutme_cool"][chat] = now + ABOUTME_COOLDOWN
                            state["active"][chat] = {"until": now + ACTIVE_WINDOW,
                                                     "chain": int(act.get("chain") or 0) + 1}
                        else:
                            # 没人理我：平均每 6 条一抽、70% 概率插一句跟话题走的话
                            n = int((state.setdefault("chime_n", {}) or {}).get(chat) or 0) + 1
                            thr = int((state.get("chime_thr") or {}).get(chat) or 0)
                            if not thr:
                                thr = random.randint(*CHIME_EVERY)
                                state.setdefault("chime_thr", {})[chat] = thr
                            state["chime_n"][chat] = n
                            if n >= thr and random.random() < CHIME_P:
                                role = f"{who}·插话"
                                state["chime_n"][chat] = 0
                                state["chime_thr"][chat] = random.randint(*CHIME_EVERY)
                                state["active"][chat] = {"until": now + ACTIVE_WINDOW,
                                                         "chain": int(act.get("chain") or 0) + 1}
                            else:
                                log(f"[{chat}] 群内 {who} {sender} 发话：没点名/没催/不在聊"
                                    f"（插话 {n}/{thr}，议论 {len(amb)}/{ABOUTME_TRIGGER}），这轮不接")
                                save_state(state)
                                continue
                        save_state(state)
                    else:
                        role = "主人" if is_operator else "访客"
                        if is_operator and OWNER_FOCUS_RE.search(text):
                            state["owner_focus_until"] = now + OWNER_FOCUS_SECONDS
                            log(f"[{chat}] 主人说「先别管群聊」：{OWNER_FOCUS_SECONDS}s 内主人优先，群聊先不回")
                            save_state(state)

                    label = "[群]" if is_group else ""
                    log(f"[{chat}] 收到{label}({role})：{text[:100]}")
                    item = make_item(chat, m, op=is_operator, named=named, kind=kind, ts=ts, text=text)
                    item["sender"] = sender
                    item["question"] = bool(question)
                    item["battle"] = battle
                    item["voice_in"] = voice_in
                    got = cst.feed(now, item)
                    for line in cst.drain_notes():
                        log(f"[{chat}] {line}")
                    persist_chat(state, chat, cst)
                    save_state(state)
                    if got == "muted":
                        log(f"[{chat}] 闭麦中，不回复（主人/点名可打断）")

                # ② 问状态机：这一轮该不该回复、回哪些消息
                action, batch = cst.tick(time.time())
                for line in cst.drain_notes():
                    log(f"[{chat}] {line}")
                persist_chat(state, chat, cst)
                save_state(state)

                if action == "reply":
                    if args.dry_run:
                        log(f"[{chat}] dry-run：状态机本该回 {len(batch)} 条合并消息（不调模型、不发送）")
                        cst.finish_reply(time.time())
                        for line in cst.drain_notes():
                            log(f"[{chat}] {line}")
                    else:
                        # 单飞：同一时刻只有一个回复任务，这里阻塞直到发完
                        try:
                            do_reply(db, wx, state, chat, batch, self_wxid, stickers)
                        except Exception as exc:
                            log(f"[{chat}] 回复流程异常：{type(exc).__name__}: {exc}")
                        finally:
                            cst.finish_reply(time.time())
                            for line in cst.drain_notes():
                                log(f"[{chat}] {line}")
                    persist_chat(state, chat, cst)
                    save_state(state)
                elif action == "dice":
                    log(f"[dice] {chat} {DICE_WAIT}s 没人接话，掷骰子（p={DICE_P}）")
                    if args.dry_run:
                        log(f"[{chat}] dry-run：不掷骰子、不发送")
                    else:
                        try:
                            play_dice(db, wx, state, chat, self_wxid)
                        except Exception as exc:
                            log(f"[{chat}] 掷骰子异常：{type(exc).__name__}: {exc}")
                    cst.finish_dice(time.time())
                    for line in cst.drain_notes():
                        log(f"[{chat}] {line}")
                    persist_chat(state, chat, cst)
                    save_state(state)

        except Exception as exc:
            log(f"轮询异常：{type(exc).__name__}: {exc}")
        if args.once:
            break
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
