# -*- coding: utf-8 -*-
"""扫出"同一问句有两个相反答案"的隐患点。

【为什么需要这个脚本】
事实层按关键词命中决定答哪一问。中文里一个词经常跨两个制度，而这两个制度的结论
可能正好相反——2026-11-04 的真实事故就是这么来的：

    「63 岁 A2 能申请延期继续开吗」
      → 命中第七十五条「延期换证」→ 答"延期期间不得驾驶机动车"
      → 实际该走第六十五条「驾驶资格延期」→ 延长期间可以继续驾驶

调度只取第一个命中的处理器，选错就直接答反，而且答案读起来很像对的，
用户不追问就发现不了。这类错误靠"发现一题修一题"永远追不上。

【做法】
对同一问句把所有处理器都跑一遍（不只第一个），收集全部非空答案，
若其中同时存在"肯定结论"和"否定结论"，标为冲突候选。
调度选中的那个是否正确，仍需人工看一眼——但清单从几千条缩到几十条，看得完。

用法：
    python tools/audit_fact_conflicts.py
    python tools/audit_fact_conflicts.py --all      # 连非冲突的命中分布一起打
"""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import fact_table as FT  # noqa: E402

# 结论极性判定：只看答案开头，够用且不会被正文里的"但…可以"带偏
_NEG = re.compile(r"不得|不能|不可以|不予|无需|不需要|不允许|不再")
_POS = re.compile(r"能|可以|需要|应当|必须|允许")


def polarity(answer: str) -> int:
    head = (answer or "").replace("*", "").replace("⚠️", "")[:90]
    if _NEG.search(head):
        return -1
    if _POS.search(head):
        return 1
    return 0


def all_hits(q: str):
    """不短路地跑完所有处理器，返回 [(处理器名, fact, 答案)]"""
    out = []
    for fn, pat, cov in FT._HANDLERS:
        if isinstance(pat, str):
            if not re.search(pat, q):
                continue
        try:
            r = fn(q)
        except Exception:
            continue
        if r and r.get("answer"):
            out.append((fn.__name__, r.get("fact") or "-", r["answer"]))
    return out


# 高频跨制度词：同一个词在两部法规里是两件事，优先扫这些
EDGE_QUESTIONS = [
    "驾驶证能延期吗", "63岁A2可以延期继续开吗", "62岁A2可以申请延长吗",
    "延期换证期间能开车吗", "驾驶证过期1年还能换证吗", "驾驶证注销了怎么恢复",
    "驾驶证被吊销还能重新考吗", "醉酒驾驶吊销几年", "实习期记满12分会怎样",
    "满分学习要学多久", "变更登记和变更备案有什么区别", "车身颜色改了要变更登记吗",
    "车架号锈蚀了怎么办", "车辆过户要本人去吗", "抵押期间能过户吗",
    "年检可以提前多久办", "免检车第6年要上线吗", "70岁C1还能开吗",
    "70岁还能开摩托车吗", "A2到63岁必须降级吗", "C1能开摩托车吗",
    "实习期能上高速吗", "驾驶证暂扣期间开车怎么处理", "无证驾驶怎么处罚",
]


def load_questions():
    qs = []
    for fn in ("eval_100_part1.json", "eval_100_part2.json"):
        p = os.path.join(ROOT, "data", fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("questions") if isinstance(data, dict) else data
        for row in rows or []:
            if isinstance(row, dict) and row.get("q"):
                qs.append(row["q"])
    qs.extend(EDGE_QUESTIONS)
    seen, uniq = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


def main():
    show_all = "--all" in sys.argv
    qs = load_questions()
    conflicts = []
    for q in qs:
        hits = all_hits(q)
        if len(hits) < 2:
            continue
        pols = {pol for _, _, a in hits if (pol := polarity(a)) != 0}
        if len(pols) < 2:
            continue
        chosen = FT.lookup(q)
        conflicts.append((q, chosen.get("fact") if chosen else "（走RAG）", hits))

    print(f"扫描 {len(qs)} 个问句，发现 {len(conflicts)} 个「多处理器结论相反」的隐患点\n")
    for q, chosen, hits in conflicts:
        print(f"■ {q}")
        print(f"   调度选中：{chosen}")
        for name, fact, ans in hits:
            p = polarity(ans)
            mark = "否定" if p < 0 else ("肯定" if p > 0 else "中性")
            first = next((l.strip() for l in ans.split("\n") if l.strip()), "")[:78]
            flag = "  ← 被选中" if fact == chosen else ""
            print(f"   [{mark}] {fact:<22} {first}{flag}")
        print()

    if show_all:
        print("=" * 80)
        for q in qs:
            hits = all_hits(q)
            print(f"{q} -> {[h[1] for h in hits]}")

    out = os.path.join(ROOT, "tools", "_fact_conflicts.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump([{"q": q, "chosen": c,
                    "hits": [{"fact": h[1], "polarity": polarity(h[2]), "head": h[2][:120]}
                             for h in hs]}
                   for q, c, hs in conflicts], f, ensure_ascii=False, indent=2)
    print(f"清单已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
