"""按天汇总 vision-log.jsonl：读图次数、耗时、token。

桥每次让模型“看一眼图”都会写一行 vision-log.jsonl；这里只做统计，不改数据。
用法：python vision_report.py [天数=7]
"""
import json, sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "vision-log.jsonl"


def main(days: int = 7) -> None:
    if not LOG.exists():
        print(f"没有 {LOG.name}，还没读过图")
        return
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        print("vision-log.jsonl 是空的")
        return
    per_day = OrderedDict()
    for r in rows[-max(1, days) * 200:]:
        per_day.setdefault(str(r.get("day") or "?"), []).append(r)
    print(f"读图统计（最近 {len(per_day)} 天，共 {len(rows)} 次）")
    print(f"{'日期':<12}{'次数':>5}{'成功':>5}{'均耗时':>8}{'输入tok':>9}{'输出tok':>9}{'缓存读':>9}{'合计tok':>10}")
    for day, rs in per_day.items():
        n = len(rs)
        ok = sum(1 for x in rs if x.get("ok"))
        ms = sum(int(x.get("ms") or 0) for x in rs) / n
        tin = sum(int(x.get("tok_in") or 0) for x in rs)
        tout = sum(int(x.get("tok_out") or 0) for x in rs)
        tc = sum(int(x.get("tok_cache") or 0) for x in rs)
        tot = sum(int(x.get("tok_total") or 0) for x in rs)
        print(f"{day:<12}{n:>5}{ok:>5}{ms:>7.0f}ms{tin:>9}{tout:>9}{tc:>9}{tot:>10}")
    bad = [r for r in rows if not r.get("ok")]
    if bad:
        print(f"\n失败 {len(bad)} 次，最近一次：{bad[-1].get('t')} {bad[-1].get('tag')}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
