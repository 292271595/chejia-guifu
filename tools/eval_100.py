# -*- coding: utf-8 -*-
"""100 道真实问题压测：判分 + 分层归因。

判分口径
--------
PASS  = 所有 must 正则命中，且没有任何 must_not 命中
FAIL  = 否则

归因口径（关键：错在哪一层，而不是只说"错了"）
--------
degraded    生成超时被降级，用户看到的是检索原文堆砌
fact_wrong  答案由事实层直答，但答错 —— 模板/分支问题
cite_fake   引用对账发现答案编造了条号
model_lost  检索已召回依据条文，但模型没用上 —— 上下文利用问题
retrieval   依据条文没进 top-k —— 检索/术语问题

用法
----
python tools/eval_100.py --offline     只跑事实层（秒级，看覆盖率）
python tools/eval_100.py               全链路（含模型，约 15 分钟）
python tools/eval_100.py --cat 记分     只跑某一类
"""
import argparse
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"C:\RAG\rag-vehicle-system"
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import fact_table  # noqa: E402

PARTS = ["data/eval_100_part1.json", "data/eval_100_part2.json"]


def load_questions(cat=None):
    qs = []
    for p in PARTS:
        with io.open(os.path.join(ROOT, p), encoding="utf-8") as f:
            data = json.load(f)
        for q in data["questions"]:
            if cat and q["cat"] != cat:
                continue
            qs.append(q)
    return qs


def judge(answer: str, must, must_not):
    """返回 (是否通过, 缺的要点, 踩中的错法)"""
    miss = [p for p in must if not re.search(p, answer)]
    bad = [p for p in must_not if re.search(p, answer)]
    return (not miss and not bad), miss, bad


def basis_recalled(basis: str, titles):
    """依据里的条号是否出现在召回片段的标题里"""
    arts = re.findall(r"第[一二三四五六七八九十百千零]+条", basis)
    if not arts:
        return None  # 依据不含条号，无法判断
    for t in titles:
        if not t:
            continue
        for a in arts:
            if a in t:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="只跑事实层，不调模型")
    ap.add_argument("--cat", default=None, help="只跑某一类")
    ap.add_argument("--verbose", action="store_true", help="打印每题答案片段")
    ap.add_argument("--via-engine", action="store_true", dest="via_engine",
                    help="事实层也走 rag_engine.query（含大模型复核），测的是线上真实路径，慢约 3 倍")
    args = ap.parse_args()

    questions = load_questions(args.cat)
    print(f"题库 {len(questions)} 题" + (f"（类别：{args.cat}）" if args.cat else ""))

    engine = None
    if not args.offline:
        from rag_engine import create_rag_engine

        engine = create_rag_engine(
            os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"), os.getenv("API_KEY"),
            os.getenv("MODEL"), os.getenv("EMBED_BACKEND", "cloud"),
            os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
        )

    rows = []
    blocked = []
    t_all = time.time()
    for i, q in enumerate(questions, 1):
        qid, text = q["id"], q["q"]
        t0 = time.time()
        review = None
        fact = fact_table.lookup(text)

        # 默认直连事实层（快，用于离线快速回归）。
        # --via-engine 时走 rag_engine.query，与线上完全同一条路：
        # 只有这条路才会经过「事实层答案大模型复核」，也才能发现复核误伤。
        if fact and not (args.via_engine and not args.offline):
            layer = "fact"
            answer = fact["answer"]
            titles = [(s.get("title") or "") for s in fact.get("sources", [])]
            degraded = False
            review = None
        elif args.offline:
            rows.append({"id": qid, "cat": q["cat"], "q": text, "layer": "skip",
                         "ok": None, "miss": [], "bad": [], "sec": 0,
                         "answer": "", "note": "离线模式未跑"})
            print(f"[{i:3d}/{len(questions)}] SKIP {qid} {text}")
            continue
        else:
            layer = "fact" if fact else "rag"
            r = engine.query(text, top_k=6, budget=12)
            answer = r["answer"]
            titles = [((s.get("metadata") or {}).get("title") or s.get("title") or "")
                      for s in r.get("sources", [])]
            degraded = bool(r.get("degraded"))
            review = (r.get("fact_review") or {}).get("verdict")
            if review == "WRONG":
                blocked.append((qid, text, (r.get("fact_review") or {}).get("reason")))

        ok, miss, bad = judge(answer, q["must"], q["must_not"])
        sec = time.time() - t0

        # ── 归因 ──
        if ok:
            cause = ""
        elif degraded:
            cause = "degraded"
        elif layer == "fact":
            cause = "fact_wrong"
        else:
            rec = basis_recalled(q.get("basis", ""), titles)
            cause = "model_lost" if rec else ("retrieval" if rec is False else "unknown")

        rows.append({"id": qid, "cat": q["cat"], "q": text, "layer": layer,
                     "ok": ok, "miss": miss, "bad": bad, "sec": round(sec, 1),
                     "answer": answer, "cause": cause, "titles": titles,
                     "degraded": degraded, "basis": q.get("basis", ""),
                     "hint": q.get("layer_hint", ""), "review": review})

        flag = "PASS" if ok else "FAIL"
        tag = f" <- {cause}" if cause else ""
        print(f"[{i:3d}/{len(questions)}] {flag} ({sec:4.1f}s) {layer:4s} {qid} {text}{tag}")
        if not ok and args.verbose:
            print(f"       缺要点: {miss}")
            print(f"       踩错法: {bad}")
            print(f"       答: {answer[:200].replace(chr(10), ' ')}")

    # ── 汇总 ──
    scored = [r for r in rows if r["ok"] is not None]
    passed = [r for r in scored if r["ok"]]
    print("\n" + "=" * 74)
    print(f"总耗时 {time.time() - t_all:.0f}s")

    if not scored:
        print("无有效用例")
        return

    print(f"总正确率：{len(passed)}/{len(scored)} = {len(passed) / len(scored) * 100:.1f}%")

    # 分层正确率
    for lay in ("fact", "rag"):
        sub = [r for r in scored if r["layer"] == lay]
        if sub:
            p = sum(1 for r in sub if r["ok"])
            print(f"  {lay:4s} 层：{p}/{len(sub)} = {p / len(sub) * 100:.0f}%"
                  f"（占 {len(sub) / len(scored) * 100:.0f}%）")

    deg = [r for r in scored if r["degraded"]]
    print(f"  降级 {len(deg)} 次")
    if args.via_engine:
        wr = [r for r in rows if r.get("review") == "WRONG"]
        sk = [r for r in rows if r.get("review") == "SKIP"]
        print(f"  大模型复核：判错 {len(wr)} 条、跳过 {len(sk)} 条、判对 "
              f"{sum(1 for r in rows if r.get('review') == 'OK')} 条")
        for r in wr:
            print(f"    复核拦截 {r['id']} {r['q']}"
                  f"｜{'仍判对（误伤已降级但断言宽松）' if r['ok'] else '该题判分未过'}")

    # 归因分布
    causes = {}
    for r in scored:
        if not r["ok"]:
            causes[r.get("cause", "unknown")] = causes.get(r.get("cause", "unknown"), 0) + 1
    if causes:
        print("\n错因分布：")
        names = {"fact_wrong": "事实层直答但答错（分支/模板）",
                 "model_lost": "依据已召回但模型没用上（上下文利用）",
                 "retrieval": "依据没进 top-6（检索/术语）",
                 "degraded": "生成超时降级",
                 "unknown": "无法归因"}
        for k, v in sorted(causes.items(), key=lambda x: -x[1]):
            print(f"  {v:3d}  {k:12s} {names.get(k, '')}")

    # 分类正确率
    print("\n分类正确率：")
    cats = {}
    for r in scored:
        c = cats.setdefault(r["cat"], [0, 0])
        c[1] += 1
        if r["ok"]:
            c[0] += 1
    for k, (p, n) in sorted(cats.items(), key=lambda x: x[1][0] / x[1][1]):
        bar = "█" * int(p / n * 20) + "░" * (20 - int(p / n * 20))
        print(f"  {k:8s} {bar} {p}/{n}")

    # 失败清单
    fails = [r for r in scored if not r["ok"]]
    if fails:
        print("\n失败清单：")
        for r in fails:
            print(f"  [{r['id']}] ({r['cat']}) {r['q']}")
            print(f"        层={r['layer']} 因={r.get('cause')} 缺={r['miss']} 错={r['bad']}")
            if r["basis"]:
                print(f"        依据={r['basis'][:70]}")
            print(f"        答={r['answer'][:160].replace(chr(10), ' ')}")

    out = os.path.join(ROOT, "tools", "_eval100_result.json")
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写入 {out}")


if __name__ == "__main__":
    main()
