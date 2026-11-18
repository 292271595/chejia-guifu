# -*- coding: utf-8 -*-
"""全量问句埋点的离线自检。

这类旁路最容易“看起来记了、其实字段全空”：接口正常返回，日志也能打一行，
但 layer/fact/degraded 都没进去，等真要排频次时才发现分母不可用。
所以这里不测 HTTP，直接测记录结构与统计口径，并检查敏感字段（openid/IP）没有落盘。

回答正文是另一个曾在测试里被写成“不许记”的字段。标准现在反过来了：
只记问句看不出答得差，所以正文必须落盘，但长度要有上限、要能被开关关掉。
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
            if "答案正文" not in raw:
                # 这条以前是反过来的：早先刻意不记答案，结果只能看出"问得多"，
                # 看不出"答得差"。标准改为——答案必须落盘，靠开关控制而不是靠不写。
                errors.append("回答正文没有落盘（只看问句判断不了答得对不对）")
            if "openid" in raw or "127.0.0.1" in raw:
                errors.append("日志里出现了用户身份或 IP")
            for key in ("q", "resolved_q", "layer", "fact", "degraded", "ms", "answer"):
                if key not in rows[0]:
                    errors.append(f"记录缺字段 {key}")

        # blocks 模式（小程序走这条路）：返回体里没有 answer 字段，
        # 必须能把结构化块还原成文字，否则小程序侧的问答全是"没答案"的记录。
        blk = {"success": True, "sources": [], "blocks": [
            {"type": "h", "text": "换证流程"},
            {"type": "p", "text": "到期前九十天内办理"},
            {"type": "list", "items": ["体检", "拍照"]},
            {"type": "table", "header": ["材料", "份数"], "rows": [["身份证", "1"]]},
        ]}
        rec_blk = query_log.build_record("块模式问句", blk)
        for want in ("换证流程", "到期前九十天内办理", "体检", "身份证"):
            if want not in rec_blk["answer"]:
                errors.append(f"blocks 还原丢了内容：{want}")
        if rec_blk["answer_chars"] < 15:
            errors.append(f"blocks 模式字数统计偏小：{rec_blk['answer_chars']}")

        # 超长答案要按上限截断，字数仍报真实长度（不然 1500 字以上的会齐刷刷显示 1500）
        os.environ["QUERY_LOG_ANSWER_MAX"] = "20"
        rec_long = query_log.build_record("长答案", _payload(answer="啊" * 500))
        os.environ.pop("QUERY_LOG_ANSWER_MAX", None)
        if len(rec_long["answer"]) > 21:
            errors.append(f"超长答案没有被截断：{len(rec_long['answer'])}")
        if rec_long["answer_chars"] != 500:
            errors.append(f"字数应按全文算，实际 {rec_long['answer_chars']}")

        # 开关 off 时只记元数据，正文一个字节都不留
        os.environ["QUERY_LOG_ANSWER"] = "off"
        rec_off = query_log.build_record("关掉正文", _payload())
        os.environ.pop("QUERY_LOG_ANSWER", None)
        if rec_off["answer"]:
            errors.append("QUERY_LOG_ANSWER=off 时仍然记了回答正文")
        if rec_off["answer_chars"] != len("答案正文"):
            errors.append("关掉正文后字数统计不该受影响")

        # stdout 那一路必须瘦身：云日志单行过长会被截断，截断后整行 JSON 解析不出来
        fat = {"q": "问", "answer": "句子。" * 400}
        if len(query_log._stdout_view(fat)["answer"]) > query_log.MAX_ANSWER_STDOUT + 1:
            errors.append("stdout 预览没有压到上限内")

        # 导出能力：没这两件，日志只能在云端留 7 天，"积累"是句空话
        os.environ["QUERY_LOG_DIR"] = tmp
        days = query_log.list_days()
        if days != [recs[0]["ts"][:10]]:
            errors.append(f"导出日期列表不对：{days}")
        if len(query_log.load(days[0])) != len(cases):
            errors.append("按天导出条数与落盘不一致")
        if query_log.load("../etc/passwd") or query_log.load("2026-1-1"):
            errors.append("非法日期没有被挡住（存在路径穿越风险）")

        # 重复拉取不能翻倍
        import pull_query_logs  # noqa: E402
        target = os.path.join(tmp, "pull.jsonl")
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        first = pull_query_logs.merge_into(target, text)
        second = pull_query_logs.merge_into(target, text)
        if (first, second) != (len(rows), 0):
            errors.append(f"合并去重失效：首次 {first} 条、重复 {second} 条")

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
    print(f"全量问句埋点自检：{len(cases)} 类返回结构全部记账，答案正文可还原、可截断、可关闭，身份与 IP 未落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
