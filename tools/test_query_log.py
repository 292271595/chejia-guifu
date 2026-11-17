# -*- coding: utf-8 -*-
"""全量问句埋点的离线自检。

这类旁路最容易“看起来记了、其实字段全空”：接口正常返回，日志也能打一行，
但 layer/fact/degraded 都没进去，等真要排频次时才发现分母不可用。
所以这里不测 HTTP，直接测记录结构与统计口径，并检查敏感字段没有落盘。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import query_log  # noqa: E402
import query_log_report  # noqa: E402


def _payload(**kw):
    base = {"success": True, "answer": "答案正文", "sources": [{"title": "依据"}],
            "fact_hit": None, "degraded": ""}
    base.update(kw)
    return base


def main():
    errors = []
    cases = [
        ("fact", _payload(fact_hit="VEHICLE:CYCLE"), 200, "fact"),
        ("rag", _payload(), 200, "rag"),
        ("fallback", _payload(degraded=True), 200, "rag_fallback"),
        ("fact_review", _payload(fact_hit="SCORE:12", degraded=True), 200,
         "fact_review_fallback"),
        ("rewrite", _payload(degraded="ctx_unresolved"), 200, "rewrite"),
        ("refused", _payload(degraded="off_topic", refused=True), 200, "refused"),
        ("error", {"success": False, "message": "坏了"}, 500, "error"),
    ]
    for name, payload, status, want in cases:
        got = query_log.classify(payload, status)
        if got != want:
            errors.append(f"{name}: 层级应为 {want}，实际 {got}")

    with tempfile.TemporaryDirectory() as tmp:
        recs = []
        for i, (name, payload, status, _) in enumerate(cases):
            recs.append(query_log.log_query(
                question=f"测试问句{i}", payload=payload, status_code=status,
                elapsed_ms=100 + i, history_turns=1 if i == 4 else 0,
                emit=False, force_dir=tmp,
            ))
        path = os.path.join(tmp, recs[0]["ts"][:10] + ".jsonl")
        if not os.path.isfile(path):
            errors.append("JSONL 未落盘")
        else:
            raw = open(path, encoding="utf-8").read()
            rows = [json.loads(x) for x in raw.splitlines() if x.strip()]
            if len(rows) != len(cases):
                errors.append(f"落盘条数应为 {len(cases)}，实际 {len(rows)}")
            if "答案正文" in raw:
                errors.append("答案正文被写进日志，违反最小化原则")
            if "openid" in raw or "127.0.0.1" in raw:
                errors.append("日志里出现了用户身份或 IP")
            for key in ("q", "resolved_q", "layer", "fact", "degraded", "ms"):
                if key not in rows[0]:
                    errors.append(f"记录缺字段 {key}")

        summary = query_log_report.summarize(rows)
        if summary["total"] != len(cases):
            errors.append("统计分母不等于原始记录数")
        if summary["layers"].get("fact") != 1:
            errors.append("统计没有正确识别 fact 层")
        if summary["multi_turn"] != 1:
            errors.append("统计没有正确识别多轮")

    if errors:
        print("全量问句埋点自检失败：")
        for e in errors:
            print("  - " + e)
        return 1
    print(f"全量问句埋点自检：{len(cases)} 类返回结构全部记账，敏感字段未落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
