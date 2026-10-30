"""latency.jsonl -> 各阶段 P50/P90，热聊/冷启动分开。用法: python latency_report.py [最近N条]"""
import json, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
path = HERE / "latency.jsonl"

def pct(vals, p):
    if not vals:
        return 0.0
    v = sorted(vals)
    k = max(0, min(len(v) - 1, int(round((p / 100.0) * (len(v) - 1)))))
    return v[k]

rows = []
if not path.exists():
    print("还没有 latency.jsonl（新版桥还没跑过消息）")
    sys.exit(0)
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        rows.append(json.loads(line))
    except Exception:
        pass
if len(sys.argv) > 1:
    rows = rows[-int(sys.argv[1]):]
buckets = defaultdict(lambda: defaultdict(list))
for r in rows:
    key = "warm" if r.get("warm") else "cold"
    buckets[r.get("stage") or "?"][key].append(float(r.get("ms") or 0))

stages = ["enqueue", "model", "type", "confirm", "total"]
print("样本 %d 条（%s）" % (len(rows), path.name))
print("%-8s %10s %10s %10s %10s %8s" % ("阶段", "热P50", "热P90", "冷P50", "冷P90", "n热/冷"))
for st in stages:
    b = buckets.get(st, {})
    w, c = b.get("warm", []), b.get("cold", [])
    print("%-8s %8.0fms %8.0fms %8.0fms %8.0fms %8s" % (st, pct(w, 50), pct(w, 90), pct(c, 50), pct(c, 90), "%d/%d" % (len(w), len(c))))
