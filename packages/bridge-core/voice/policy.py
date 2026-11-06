"""回复格式决策：这一轮用小肥鱼自己的嘴（TTS + /voice/send）还是打字。

规则（用户 2026-11 定，勿擅自改顺序）：
1. 对方发文字          -> 默认文字回
2. 对方发语音          -> 优先语音回（语音对语音）
3. 文字里带强制语音词  -> 必须语音回（发语音/唱首歌/说句话…）
4. 纯文字场景          -> VOICE_RANDOM_P（10%）概率随机语音回

只做纯决策，不碰网络/音频，方便单测与日志复现。
"""
import re

VOICE_RANDOM_P = 0.10

# 命中任何一个就当「对方点名要听声音」，必须语音回。刻意只收明确要声音的说法，
# 不收「唱歌」这种日常闲话词（对方说「今天唱歌去了」不该触发语音）。
VOICE_FORCE_WORDS = (
    "发语音", "发条语音", "发个语音", "回语音", "回条语音", "回个语音", "语音回",
    "用语音", "语音说", "说句话", "说一句", "念一遍", "读出来", "念出来",
    "唱首歌", "唱一句", "唱一段", "来一段", "唱给我听", "说给我听", "出声",
    # 2026-11 补：用户「我想听听你的声音了／那你给我发呗」曾被判成文字回，他会觉得语音那半聋了
    "你的声音", "听你说话", "听你说", "让我听", "听听你", "想听你",
    "说两句", "来一句", "来两句", "念给我", "读给我",
    # 2026-11-05 补：主人说「你再发句语音试试」「我说你发句语音来给我」被判成文字回——
    # 换量词（句/段/条）和换词序的口语说法列表收不全，另外配模式兜底。
    "句语音", "段语音", "条语音", "语音发来", "语音过来", "发个声", "出个声",
)

# 口语换量词/换词序的说法（「发句语音」「来段语音」「语音发过来」）：列表只收固定说法，
# 这里按「动词 + 最多 4 个字 + 语音」兜住，命中同样算点名要听声音。
VOICE_FORCE_PATTERNS = (
    re.compile(r"(?:发|来|回|说|听|唱|念|读)[^，。！？,.!?]{0,4}语音"),
    re.compile(r"语音[^，。！？,.!?]{0,4}(?:发|来|回|说|听)"),
)

# 上一句刚用嘴说过，这一句是「那就再来一条」式的短追问 -> 续语音（文字里不会出现强制词）
VOICE_FOLLOWUP_WORDS = (
    "发呗", "发啊", "发呀", "你发", "那就发", "快发", "发过来", "发一段",
    "再说一句", "再来一句", "说啊", "说呀", "说呗", "没听见", "听不清",
)


def follow_up_hits(text: str) -> list:
    """短追问里点名「再发一条」的词；只在上一句已经是语音时才生效。"""
    t = (text or "").strip()
    if len(t) > 14:
        return []
    return [w for w in VOICE_FOLLOWUP_WORDS if w in t]


def force_voice_hits(text: str) -> list:
    """返回命中的强制语音词，按词长从长到短（日志里直接引用第一个）。"""
    t = text or ""
    hits = [w for w in VOICE_FORCE_WORDS if w in t]
    for rx in VOICE_FORCE_PATTERNS:
        hits.extend(m.group(0) for m in rx.finditer(t))
    hits.sort(key=len, reverse=True)
    return hits


def decide_reply_format(*, is_voice_input: bool, user_text: str, random_value: float,
                        last_out_voice: bool = False):
    """返回 (是否用语音回, 中文理由)。random_value 由调用方传入，便于复现与测试。"""
    if is_voice_input:
        return True, "对方发的是语音 → 语音对语音"
    hits = force_voice_hits(user_text)
    if hits:
        return True, "命中强制语音词『%s』" % hits[0]
    if last_out_voice:
        fu = follow_up_hits(user_text)
        if fu:
            return True, "接着上一句语音的短追问『%s』→ 续语音" % fu[0]
    if random_value < VOICE_RANDOM_P:
        return True, "文字场景 10%% 随机语音（r=%.2f < %.2f）" % (random_value, VOICE_RANDOM_P)
    return False, "文字场景 → 默认文字回"
