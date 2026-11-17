# -*- coding: utf-8 -*-
"""全量问句频次与质量报告。

输入是 query_log.py 写的 JSONL，或从云托管日志导出的带 `[QL] ` 前缀的文本。
输出真实分母：总提问、层级分布、领域分布、事实 key、降级、耗时、点踩原因。

用法：
    python tools/query_log_report.py
    python tools/query_log_report.py --day 2026-11-18
    python tools/query_log_report.py --file tools/_ql_export.txt
    python tools/query_log_report.py --feedback data/feedback/2026-11-18.jsonl
    python tools/query_log_report.py --json

注意：这是“观察工具”，不参与判分。真实频次只用于排优先级，
不能拿它替代独立评测，也不能因为某个问题没被点踩就认为它答对了。
"""
import argparse
import glob
import io
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOMAIN = {
    "SCORE": "违法记分",
    "VEHICLE": "车辆检验/登记",
    "EXAM": "考试",
    "RENEW": "换证",
    "REVOKE": "吊销/禁驾",
    "RECOVER": "注销恢复",
    "INTERNSHIP": "实习期",
    "ADD": "增驾",
    "SCOPE": "准驾范围",
    "REVIEW": "审验",
    "INS": "保险",
    "REG": "登记业务",
    "EDGE": "边界知识",
    "FULL": "满分学习",
    "PHYSICAL": "身体条件",
    "AGE": "年龄条件",
    "MULTI": "复合问句",
}


def _domain(fact: str) -> str:
    if not fact:
        return "未命中事实层"
    first = str(fact).split(":", 1)[0].upper()
    return DOMAIN.get(first, first or "未知")


def load_records(paths):
    records = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[QL]"):
                    line = line[4:].strip()
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("q"):
                    records.append(rec)
    return records


def discover_files(args):
    if args.file:
        return list(args.file)
    directory = args.dir or os.getenv("QUERY_LOG_DIR", "").strip() \
        or os.path.join(ROOT, "data", "query_log")
    pattern = os.path.join(directory, f"{args.day}.jsonl" if args.day else "*.jsonl")
    return sorted(glob.glob(pattern))


def load_feedback(path):
    """反馈是可选的旁证，不是分母。没有反馈不等于没有错误。"""
    if not path:
        return []
    paths = [path]
    if os.path.isdir(path):
        paths = sorted(glob.glob(os.path.join(path, "*.jsonl")))
    rows = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("question"):
                    rows.append(rec)
    return rows


def _pct(values, p):
    if not values:
        return 0
    arr = sorted(values)
    idx = min(len(arr) - 1, max(0, int(round((len(arr) - 1) * p))))
    return arr[idx]


def summarize(records, feedback=None):
    feedback = feedback or []
    layers = Counter(r.get("layer") or "unknown" for r in records)
    facts = Counter(r.get("fact") or "" for r in records)
    domains = Counter(_domain(r.get("fact") or "") for r in records)
    degraded = Counter(str(r.get("degraded")) for r in records if r.get("degraded"))
    questions = Counter((r.get("q") or "").strip() for r in records if r.get("q"))
    resolved = sum(1 for r in records if r.get("resolved_q"))
    multi = sum(1 for r in records if int(r.get("history_turns") or 0) > 0)
    errors = sum(1 for r in records if r.get("layer") == "error")
    latencies = [int(r.get("ms") or 0) for r in records]

    reasons = Counter()
    fq = set()
    for f in feedback:
        reasons[f.get("reason_label") or f.get("reason") or "未填"] += 1
        fq.add((f.get("question") or "").strip()[:300])
    matched = questions.keys() & fq
    matched_n = sum(questions[q] for q in matched)

    return {
        "total": len(records),
        "unique_questions": len(questions),
        "multi_turn": multi,
        "resolved": resolved,
        "errors": errors,
        "layers": layers,
        "domains": domains,
        "facts": facts,
        "degraded": degraded,
        "latency_ms": {
            "avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "p50": _pct(latencies, 0.50),
            "p90": _pct(latencies, 0.90),
            "p99": _pct(latencies, 0.99),
            "max": max(latencies) if latencies else 0,
        },
        "feedback_total": len(feedback),
        "feedback_matched_records": matched_n,
        "feedback_reasons": reasons,
        "top_questions": questions,
        "feedback_questions": fq,
    }


def print_report(s, top=15):
    print("=" * 72)
    print(f"全量问句：{s['total']} 次｜去重问法 {s['unique_questions']} 种｜"
          f"多轮 {s['multi_turn']} 次｜改写生效 {s['resolved']} 次｜错误 {s['errors']} 次")
    lat = s["latency_ms"]
    print(f"耗时：平均 {lat['avg']}ms｜P50 {lat['p50']}ms｜P90 {lat['p90']}ms｜"
          f"P99 {lat['p99']}ms｜最大 {lat['max']}ms")

    print("\n命中层级：")
    for name, n in s["layers"].most_common():
        print(f"  {name:22s} {n}")

    print("\n业务领域：")
    for name, n in s["domains"].most_common():
        print(f"  {name:22s} {n}")

    print("\n降级/拦截：")
    if not s["degraded"]:
        print("  无")
    for name, n in s["degraded"].most_common():
        print(f"  {name:22s} {n}")

    print("\n事实 key Top：" if s["facts"] else "\n事实 key：")
    for name, n in s["facts"].most_common(top):
        if not name:
            continue
        print(f"  {name:34s} {n}")

    print(f"\n用户点踩：{s['feedback_total']} 条｜可精确关联到问句的记录 {s['feedback_matched_records']} 条")
    if s["feedback_total"]:
        for name, n in s["feedback_reasons"].most_common():
            print(f"  {name:22s} {n}")
        print("  注意：点踩只是分子旁证，真实分母仍是上面的全量问句。")

    print("\n高频问法 Top：")
    for q, n in s["top_questions"].most_common(top):
        print(f"  {n:4d}  {q}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", help="JSONL 或含 [QL] 行的日志文件，可多次")
    ap.add_argument("--dir", default="", help="日志目录，默认 QUERY_LOG_DIR 或 data/query_log")
    ap.add_argument("--day", default="", help="只读某一天，如 2026-11-18")
    ap.add_argument("--feedback", default="", help="反馈 JSONL 或目录，可选")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    files = discover_files(args)
    records = load_records(files)
    if not records:
        print("没有找到问句日志。")
        print("检查 QUERY_LOG_DIR，或把云托管导出的含 [QL] 行的日志传给 --file。")
        return 2

    s = summarize(records, load_feedback(args.feedback))
    if args.json:
        out = dict(s)
        out["layers"] = dict(s["layers"])
        out["domains"] = dict(s["domains"])
        out["facts"] = dict(s["facts"])
        out["degraded"] = dict(s["degraded"])
        out["feedback_reasons"] = dict(s["feedback_reasons"])
        out["top_questions"] = dict(s["top_questions"].most_common(args.top))
        out.pop("feedback_questions", None)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"日志文件：{len(files)} 个")
        print_report(s, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
