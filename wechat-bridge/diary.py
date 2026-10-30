"""日记本：从聊天记录提炼事实 → 按评分压缩淘汰 → 重置会话时注入。

存储：diary.md（全部条目）、diary-core.md（5 分核心，永不淘汰）、chatlog.jsonl（桥写的聊天流水）。
行格式：[YYYY-MM-DD] [评分] [标签] 事实
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIARY = HERE / "diary.md"
CORE = HERE / "diary-core.md"
CHATLOG = HERE / "chatlog.jsonl"

MAX_ENTRIES = 30          # 超过就压缩
MAX_CHARS = 2000          # 字数上限
KEEP_RECENT = 20          # 兜底：无论如何保留最近 20 条
INJECT_DAYS = 1           # 只注入最近 1 天（重置后只带最新精简日记）
INJECT_CHARS = 600        # 注入上限（字）

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]\s*\[([1-5])\]\s*\[([^\]]{1,12})\]\s*(.+?)\s*$")
EXTRACT_RE = re.compile(r"^\s*\[([1-5])\]\s*\[([^\]]{1,12})\]\s*(.+?)\s*$")

EXTRACT_PROMPT = """下面是微信群「{group}」{day} 的聊天记录。只提炼【事实】，不要形容词，不要编造，不要写"今天很可爱"这种评价。
输出 3–5 行，每行严格格式：[评分] [标签] 事实
评分：5=核心（主人身份/偏好、群底线规矩、核心人设）；4=重要（群友固定称呼、长期爱好、长线项目）；3=普通（当天话题、接的梗）；1-2=闲聊杂项（天气、闲篇）。
标签写对象：主人 / 群 / 某个人的固定称呼。
没有值得记的就只输出「无」。

聊天记录：
{log}
"""

MERGE_PROMPT = """把下面几条同类的日记合并成一条，保留全部事实、去掉重复、不要加形容词。
只输出一行，严格格式：[4] [标签] 事实

{facts}
"""


def dry_run() -> bool:
    return os.environ.get("MEMORY_DRYRUN", "1") != "0"


def log_chat(chat: str, who: str, text: str) -> None:
    """聊天流水（提炼日记的唯一事实来源）。"""
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return
    row = {"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "chat": chat, "who": who, "text": text[:300]}
    try:
        with CHATLOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_day(day: str, limit: int = 400) -> str:
    """取某天聊天记录（最多 400 行，够提炼了）。"""
    if not CHATLOG.exists():
        return ""
    out = []
    try:
        for line in CHATLOG.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("t", ""))[:10] == day:
                out.append(f"{row.get('who')}: {row.get('text')}")
    except Exception:
        return ""
    return "\n".join(out[-limit:])


def parse_extract(text: str, day: str) -> list:
    """解析模型输出 → 条目列表；不合格的行直接丢弃（绝不瞎编）。"""
    out = []
    for line in (text or "").splitlines():
        m = EXTRACT_RE.match(line.strip())
        if not m:
            continue
        score, tag, fact = int(m.group(1)), m.group(2).strip(), m.group(3).strip()
        if not fact or fact in ("无", "无。"):
            continue
        out.append({"day": day, "score": score, "tag": tag, "fact": fact[:200]})
    return out


def load() -> list:
    if not DIARY.exists():
        return []
    entries = []
    for line in DIARY.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            entries.append({"day": m.group(1), "score": int(m.group(2)), "tag": m.group(3), "fact": m.group(4)})
    return entries


def _render(entries: list) -> str:
    return "".join(f"[{e['day']}] [{e['score']}] [{e['tag']}] {e['fact']}\n" for e in entries)


def _chars(entries: list) -> int:
    return sum(len(e["fact"]) for e in entries)


def save(entries: list, note: str = "") -> None:
    try:
        DIARY.write_text(_render(entries), encoding="utf-8")
        CORE.write_text(_render([e for e in entries if e["score"] >= 5]), encoding="utf-8")
    except Exception:
        pass


def over_limit(entries: list) -> bool:
    return len(entries) >= MAX_ENTRIES or _chars(entries) >= MAX_CHARS


def append(new_entries: list) -> list:
    """追加并去重（同标签同事实不重复入库）。"""
    entries = load()
    seen = {(e["tag"], e["fact"]) for e in entries}
    added = 0
    for e in new_entries:
        if (e["tag"], e["fact"]) in seen:
            continue
        seen.add((e["tag"], e["fact"]))
        entries.append(e)
        added += 1
    save(entries)
    return entries


def compress(entries: list, merge_fn=None) -> list:
    """先合并同类 3 分，再按评分从低到高淘汰；5 分永不删；失败兜底留最近 KEEP_RECENT。"""
    try:
        # 1) 同标签的 3 分 ≥3 条 → 合并成一条 4 分
        if merge_fn:
            groups = {}
            for e in entries:
                if e["score"] == 3:
                    groups.setdefault(e["tag"], []).append(e)
            for tag, grp in groups.items():
                if len(grp) < 3:
                    continue
                merged = merge_fn(tag, [g["fact"] for g in grp])
                if not merged:
                    continue
                keep = {"day": grp[-1]["day"], "score": 4, "tag": tag, "fact": merged[:300]}
                entries = [e for e in entries if e not in grp] + [keep]
        # 2) 仍超限：先删 1 分、再删 2 分、最后按评分升序删（5 分不参与）
        if over_limit(entries):
            recent = entries[-KEEP_RECENT:]
            rest = entries[:-KEEP_RECENT]
            for floor in (1, 2):
                if not over_limit(entries):
                    break
                rest = [e for e in rest if not (e["score"] >= 5 and False)]
                drop = [e for e in rest if e["score"] == floor]
                for e in drop:
                    rest.remove(e)
                    if not over_limit(rest + recent):
                        break
                entries = rest + recent
            guard = 0
            while over_limit(entries) and guard < 200:
                guard += 1
                cand = sorted([e for e in entries[:-KEEP_RECENT] if e["score"] < 5],
                              key=lambda e: (e["score"], e["day"]))
                if not cand:
                    break
                entries.remove(cand[0])
        save(entries)
        return entries
    except Exception:
        keep = sorted(load(), key=lambda e: (e["score"] >= 5, e["day"]))[-KEEP_RECENT:]
        save(keep)
        return keep


def context_block(max_chars: int = INJECT_CHARS) -> str:
    """5 分核心 + 最近 3 天（按评分倒序）+ 长线话题，拼成 ≤800 字的一段。"""
    entries = load()
    if not entries:
        return ""
    since = (datetime.now() - timedelta(days=INJECT_DAYS)).strftime("%Y-%m-%d")
    core = [e for e in entries if e["score"] >= 5]
    recent = sorted([e for e in entries if e["day"] >= since and e["score"] == 3],
                    key=lambda e: (-e["score"], e["day"]))
    lines = ["【群里的事（不是新消息，别当成有人在说话）】"]
    if core:
        lines.append("核心：" + "；".join(f"{e['tag']}·{e['fact']}" for e in core))
    if recent:
        lines.append("最近：" + "；".join(f"{e['tag']}·{e['fact']}" for e in recent))
    longline = [e for e in entries if e["score"] == 4]
    if longline:
        lines.append("长线：" + "；".join(f"{e['tag']}·{e['fact']}" for e in longline[-4:]))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    return text
