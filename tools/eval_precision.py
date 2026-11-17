# -*- coding: utf-8 -*-
"""独立精准评测：把路由、首段结论、必要细节和错误类型分开统计。

与旧题库的区别：
- dev 用于发现和修复；holdout 只在阶段验收时运行，不接进每日一键回归。
- 同一 group 不得跨 split，避免同一考点换种说法后发生泄漏。
- must_head 只查首段，防止「首句答错、后文找补」仍被判通过。
- 每题保留多个错误标签，同时给一个主因，便于按错误切片统计公共根因。
- 依据带 file/quote 时会到知识库逐字核对；没有 quote 的只算人工依据。

用法：
    python tools/eval_precision.py                     # 开发集，事实层快速链路
    python tools/eval_precision.py --split holdout     # 阶段验收隐藏集
    python tools/eval_precision.py --split all         # 全部（不用于日常调参）
    python tools/eval_precision.py --full              # 线上完整链路，需要模型
    python tools/eval_precision.py --tag baseline      # 保留一份带标签的结果

退出码：0 表示结构、依据、答案和尺子自检全过；1 表示存在失败。
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

BANK = os.path.join(ROOT, "data", "evaluation", "precision_cases.json")
KB_DIR = os.path.join(ROOT, "data", "knowledge_base")
ABSTAIN = re.compile(r"(暂无|未收录|无法确定|不在.{0,6}范围|没有相关|建议咨询|无法回答)")

LABEL_ORDER = (
    "exception", "no_answer", "route_miss", "route_wrong", "unexpected_fact",
    "forbidden_conclusion", "missing_head", "missing_detail", "unexpected_abstain",
)


def load_bank():
    with open(BANK, encoding="utf-8") as f:
        return json.load(f)


def validate_bank(data):
    """先校验题库本身，防止错题、错正则和 split 泄漏制造假结果。"""
    errors = []
    ids = set()
    group_splits = defaultdict(set)
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["cases 必须是非空列表"]
    for i, c in enumerate(cases):
        cid = c.get("id") or f"第{i + 1}条"
        for key in ("id", "split", "group", "domain", "style", "severity", "q", "basis"):
            if not c.get(key):
                errors.append(f"{cid}: 缺字段 {key}")
        if cid in ids:
            errors.append(f"{cid}: id 重复")
        ids.add(cid)
        if c.get("split") not in ("dev", "holdout"):
            errors.append(f"{cid}: split 只能是 dev/holdout")
        group_splits[c.get("group")].add(c.get("split"))
        hist = c.get("history")
        if hist is not None:
            if not isinstance(hist, list) or not hist:
                errors.append(f"{cid}: history 必须是非空列表")
            else:
                for j, h in enumerate(hist):
                    if not isinstance(h, dict) or h.get("role") not in ("user", "assistant") \
                            or not (h.get("content") or "").strip():
                        errors.append(f"{cid}: history[{j}] 需要 role(user/assistant) 和非空 content")
                    elif h.get("role") == "user" and h["content"].strip() == (c.get("q") or "").strip():
                        errors.append(f"{cid}: history 末轮 user 句与当前问句相同（改写层会摘掉，但题面不该这么写）")
        if not (c.get("expected_fact") or c.get("expected_fact_prefix") or c.get("expected_route")):
            errors.append(f"{cid}: 必须声明 expected_fact、expected_fact_prefix 或 expected_route")
        for field in ("must_head", "must_anywhere", "must_not_head"):
            pats = c.get(field)
            if not isinstance(pats, list):
                errors.append(f"{cid}: {field} 必须是列表")
                continue
            for pat in pats:
                try:
                    re.compile(pat)
                except re.error as e:
                    errors.append(f"{cid}: {field} 正则无效 {pat!r}: {e}")
        if c.get("quote") and not c.get("file"):
            errors.append(f"{cid}: 有 quote 但没有 file")
    for group, splits in group_splits.items():
        if len(splits) > 1:
            errors.append(f"group {group} 同时出现在 {sorted(splits)}，发生考点泄漏")
    return errors


def check_basis(cases):
    """只校验声明了原文摘录的依据；其他条目明确计入人工依据。"""
    from check_golden import normalize, repair_ocr

    errors = []
    cache = {}
    anchored = 0
    for c in cases:
        if not c.get("quote"):
            continue
        anchored += 1
        fname = c["file"]
        if fname not in cache:
            path = os.path.join(KB_DIR, fname)
            if not os.path.isfile(path):
                cache[fname] = None
            else:
                with open(path, encoding="utf-8") as f:
                    cache[fname] = normalize(repair_ocr(f.read()))
        body = cache[fname]
        if body is None:
            errors.append(f"{c['id']}: 依据文件不存在 {fname}")
            continue
        for seg in c["quote"].split("……"):
            if normalize(seg) not in body:
                errors.append(f"{c['id']}: 依据摘录未在 {fname} 找到：{seg[:50]}")
    return errors, anchored


def content_errors(c, answer):
    """内容判分。禁词只看首段，避免正确答案引用错误说法做消歧时误伤。"""
    from check_golden import head_zone

    answer = answer or ""
    head = head_zone(answer)
    miss_head = [p for p in c.get("must_head", []) if not re.search(p, head)]
    miss_anywhere = [p for p in c.get("must_anywhere", []) if not re.search(p, answer)]
    bad_head = [p for p in c.get("must_not_head", []) if re.search(p, head)]
    labels = []
    if bad_head:
        labels.append("forbidden_conclusion")
    if miss_head:
        labels.append("missing_head")
    if miss_anywhere:
        labels.append("missing_detail")
    return labels, miss_head, miss_anywhere, bad_head, head


def route_errors(c, actual_fact):
    labels = []
    exact = c.get("expected_fact")
    prefix = c.get("expected_fact_prefix")
    expected_route = c.get("expected_route", "fact")
    if exact:
        if not actual_fact:
            labels.append("route_miss")
        elif actual_fact != exact:
            labels.append("route_wrong")
    elif prefix:
        if not actual_fact:
            labels.append("route_miss")
        elif not actual_fact.startswith(prefix):
            labels.append("route_wrong")
    elif expected_route == "fact" and not actual_fact:
        labels.append("route_miss")
    elif expected_route == "rag" and actual_fact:
        labels.append("unexpected_fact")
    return labels


def _fast_query(c, FT):
    """快速链路的多轮题：先走规则补全（ctx_rules，毫秒级不调模型），补不上再问原句。

    与线上链路的差别要写清楚：线上在规则层之后还有模型补全兜底；快速链路
    没有模型，规则没接住的追问只按原句查表。所以多轮题要挑规则层接得住的
    形态出题；规则接不住的留给 --full 阶段验收。
    返回 (result, resolved_question)。
    """
    q, hist = c["q"], c.get("history")
    if not hist:
        return FT.lookup(q), ""
    try:
        import ctx_rules
        rr = ctx_rules.rule_resolve(q, hist)
        if rr.get("status") == "resolved":
            return FT.lookup(rr["question"]), rr["question"]
    except Exception:
        pass
    return FT.lookup(q), ""


def evaluate(c, result, err=""):
    answer = (result or {}).get("answer") or ""
    actual_fact = (result or {}).get("fact") or (result or {}).get("fact_hit") or ""
    labels = []
    if err:
        labels.append("exception")
    if not answer.strip():
        labels.append("no_answer")
    labels.extend(route_errors(c, actual_fact))
    content, miss_head, miss_anywhere, bad_head, head = content_errors(c, answer)
    labels.extend(content)
    if ABSTAIN.search(answer) and not c.get("allow_abstain"):
        labels.append("unexpected_abstain")
    labels = [x for x in LABEL_ORDER if x in labels]
    primary = next((x for x in (
        "exception", "route_miss", "route_wrong", "unexpected_fact", "no_answer",
        "forbidden_conclusion", "missing_head", "missing_detail", "unexpected_abstain",
    ) if x in labels), "")
    return {
        "id": c["id"], "split": c["split"], "group": c["group"],
        "domain": c["domain"], "style": c["style"], "severity": c["severity"],
        "q": c["q"], "expected_fact": c.get("expected_fact") or c.get("expected_fact_prefix"),
        "actual_fact": actual_fact, "match": (result or {}).get("match") or "",
        "layer": "fact" if actual_fact else "rag", "answer": answer, "head": head,
        "ok": not labels, "errors": labels, "primary": primary,
        "miss_head": miss_head, "miss_anywhere": miss_anywhere, "bad_head": bad_head,
        "degraded": bool((result or {}).get("degraded")), "err": err,
    }


def selfcheck(cases, rows):
    """确认尺子不是永远绿：删必须要素、替换首段错话，都必须被判失败。"""
    by_id = {r["id"]: r for r in rows}
    failures = []
    total = 0
    for c in cases:
        row = by_id.get(c["id"])
        answer = (row or {}).get("answer") or ""
        if not answer:
            continue
        for pat in c.get("must_head", []):
            if not re.search(pat, answer):
                continue
            total += 1
            damaged = re.sub(pat, "", answer)
            labels, *_ = content_errors(c, damaged)
            if not labels:
                failures.append(f"{c['id']}: 删除首段必须项 {pat!r} 后仍通过")
            break
        if c.get("wrong_head"):
            from check_golden import head_zone
            total += 1
            head = head_zone(answer)
            damaged = c["wrong_head"] + answer[len(head):]
            labels, *_ = content_errors(c, damaged)
            if not labels:
                failures.append(f"{c['id']}: 注入错误首段后仍通过")
    return failures, total


def summarize(rows):
    total = len(rows)
    passed = sum(r["ok"] for r in rows)
    print("=" * 76)
    print(f"精准评测：{passed}/{total} 通过" + (f"（{passed / total:.1%}）" if total else ""))
    print("注意：总分只用于概览，修方向看下面的错误切片，不拿通过率掩盖结构。")

    checks = {
        "理解/路由": [r for r in rows if not any(x.startswith("route_") or x == "unexpected_fact" for x in r["errors"])],
        "首段主结论": [r for r in rows if not any(x in ("missing_head", "forbidden_conclusion") for x in r["errors"])],
        "必要细节完整": [r for r in rows if "missing_detail" not in r["errors"]],
        "答案可用": [r for r in rows if not any(x in ("exception", "no_answer", "unexpected_abstain") for x in r["errors"])],
    }
    print("\n质量维度：")
    for name, good in checks.items():
        print(f"  {name:10s} {len(good):2d}/{total}")

    primary = Counter(r["primary"] for r in rows if not r["ok"])
    if primary:
        print("\n主因分布：")
        for name, n in primary.most_common():
            print(f"  {name:22s} {n}")
    high_bad = [r for r in rows if not r["ok"] and r["severity"] == "high"]
    print(f"\n高严重度失败：{len(high_bad)} 条")
    print(f"降级结果：{sum(r['degraded'] for r in rows)} 条（单列观察，不自动等同于答错）")


def print_failures(rows, show):
    bad = [r for r in rows if not r["ok"]]
    if not bad:
        return
    print("\n失败清单：")
    for r in bad[:show]:
        print(f"  {r['id']} [{r['primary']}] {r['q']}")
        if r.get("resolved_q"):
            print(f"      规则补全 → {r['resolved_q']}")
        print(f"      期望 {r['expected_fact']}｜实际 {r['actual_fact'] or '走检索层'}")
        if r["miss_head"]:
            print(f"      首段缺：{r['miss_head']}")
        if r["miss_anywhere"]:
            print(f"      细节缺：{r['miss_anywhere']}")
        if r["bad_head"]:
            print(f"      首段错：{r['bad_head']}")
        print("      首段：" + (r["head"][:150].replace("\n", " | ") or "<空>"))
    if len(bad) > show:
        print(f"  ……另有 {len(bad) - show} 条，见结果文件")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    ap.add_argument("--full", action="store_true", help="走完整 RAG 链路；默认只跑事实层")
    ap.add_argument("--tag", default="", help="结果文件标签，例如 baseline")
    ap.add_argument("--show", type=int, default=30)
    args = ap.parse_args()

    data = load_bank()
    structural = validate_bank(data)
    if structural:
        print("题库结构不合法：")
        for e in structural:
            print("  " + e)
        return 1

    all_cases = data["cases"]
    cases = all_cases if args.split == "all" else [c for c in all_cases if c["split"] == args.split]
    basis_errors, anchored = check_basis(cases)
    if basis_errors:
        print("依据校验失败：")
        for e in basis_errors:
            print("  " + e)
        return 1
    print(f"题库 {len(cases)} 条｜依据原文机器核验 {anchored} 条｜"
          f"人工依据 {len(cases) - anchored} 条｜模式 {'完整链路' if args.full else '事实层快速链路'}")

    engine = None
    if args.full:
        from eval_web100 import default_config, ensure_chroma_copy
        import rag_engine
        cfg = default_config()
        engine = rag_engine.create_rag_engine(
            ensure_chroma_copy(), cfg["api_base"], cfg["api_key"], cfg["model"],
            cfg["embed_backend"], cfg["embed_api_key"], cfg["embed_api_base"])
    else:
        import fact_table as FT

    rows = []
    for i, c in enumerate(cases, 1):
        err = ""
        resolved_q = ""
        try:
            if engine:
                result = engine.query(c["q"], top_k=6, budget=12, history=c.get("history"))
            else:
                result, resolved_q = _fast_query(c, FT)
        except Exception as e:
            result, err = None, f"{type(e).__name__}: {e}"[:240]
        row = evaluate(c, result, err)
        if resolved_q:
            row["resolved_q"] = resolved_q
        rows.append(row)
        tail = f" -> {resolved_q}" if resolved_q else ""
        print(f"[{i:2d}/{len(cases)}] {'OK  ' if row['ok'] else 'FAIL'} {c['id']} "
              f"{row['actual_fact'] or 'RAG'} {c['q']}{tail}")

    summarize(rows)
    print_failures(rows, args.show)
    self_bad, self_total = selfcheck(cases, rows)
    print(f"\n尺子自检：{self_total - len(self_bad)}/{self_total} 次破坏被拦住")
    for e in self_bad:
        print("  [尺子漏洞] " + e)

    safe_tag = re.sub(r"[^0-9A-Za-z_-]", "", args.tag)
    suffix = ("_" + safe_tag) if safe_tag else ""
    result_path = os.path.join(ROOT, "tools", f"_precision_{args.split}_{'full' if args.full else 'fast'}{suffix}.json")
    with open(BANK, "rb") as f:
        bank_hash = hashlib.sha256(f.read()).hexdigest()
    payload = {
        "meta": {"created_at": dt.datetime.now().isoformat(timespec="seconds"),
                 "split": args.split, "mode": "full" if args.full else "fast",
                 "bank_sha256": bank_hash},
        "rows": rows,
        "selfcheck_failures": self_bad,
    }
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"结果：{result_path}")

    failed = sum(not r["ok"] for r in rows)
    if failed or self_bad:
        print(f"未通过：答案失败 {failed} 条，尺子漏洞 {len(self_bad)} 处")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
