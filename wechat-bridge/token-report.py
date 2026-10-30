import json, glob, sys, os
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:
    zstd = None

SESS = Path(r"C:\Users\one\.dsh\sessions")

def read_session(path: Path) -> str:
    data = path.read_bytes()
    if zstd is None:
        return ""
    dctx = zstd.ZstdDecompressor()
    out = []
    buf = data
    while buf:
        try:
            dobj = dctx.decompressobj()
            out.append(dobj.decompress(buf))
            rest = dobj.unused_data
            if not rest or len(rest) == len(buf):
                break
            buf = rest
        except Exception:
            break
    return b"".join(out).decode("utf-8", "replace")

def summarize(sid: str):
    hits = list(SESS.glob(f"*/{sid}/session.v4.jsonl.zstd"))
    if not hits:
        print(f"{sid}: not found")
        return
    text = read_session(hits[0])
    turns, steps = 0, 0
    tot = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0, "totalTokens": 0}
    per_turn = []
    cur = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "status":
            ph = ev.get("phase")
            if ph == "turn_start":
                turns += 1
                cur = dict.fromkeys(tot, 0)
                cur["turn"] = ev.get("turn")
            elif ph == "step_end" and "usage" in ev and cur is not None:
                steps += 1
                for k in tot:
                    cur[k] += int(ev["usage"].get(k) or 0)
                    tot[k] += int(ev["usage"].get(k) or 0)
            elif ph == "turn_end" and cur is not None:
                per_turn.append(cur)
                cur = None
    print(f"\n=== {sid} ===")
    print(f"turns={turns} steps={steps}")
    print("total:", tot)
    for t in per_turn:
        print(f"  turn {t['turn']}: in={t['inputTokens']} out={t['outputTokens']} cacheRead={t['cacheReadTokens']} cacheWrite={t['cacheWriteTokens']} total={t['totalTokens']}")

for sid in sys.argv[1:]:
    summarize(sid)
