# -*- coding: utf-8 -*-
"""问法变体扩增：同一个考点，换个说法还答得对不。

为什么不是「从 100 题扩到 1000 题」：
  coverage_audit 跑出来的数字是——知识库 163 条含硬事实的条款里，116 条没被
  事实层直接引用，但其中 90 条是「同一事实的另一部法源」（实施条例讲有效期 6 年，
  172 号令第十三条早就覆盖了），真缺口只有 26 条，而且多数是考试员培训 40 小时、
  考试计划天数 60% 这种内部工作规范，用户不会问。

  也就是说：**考点数量本来就不多，能扩的是问法，不是条款。**
  真正决定「精准」的不是「题库里有多少题」，而是「同一个考点换个说法还能不能答对」。

做法：
  拿现成的 100 题 + 200 题，对每道题做保守改写（只换同义虚词、去掉礼貌前缀、
  倒装、加减句末语气词），不改变问题指向。然后跑事实层，看
     · 是否命中同一个 fact key
     · 答案是否仍满足原题的判分要点
  命中率低的那一类改写，要么是改写规则造了病句，要么是事实层守卫太窄——
  两种情况都得人看一眼，但至少不用人从零想。

  这一步不需要模型，纯 fact_table.lookup，快且稳。

用法：
    python tools/expand_questions.py            # 全量，输出按变体类型汇总
    python tools/expand_questions.py --show 30  # 多列未命中样例
"""
import io
import re
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def _strip_polite(q):
    v = re.sub(r"^(?:请问|我想问一下|想问下|问一下)[，,、]?\s*", "", q)
    return v if v != q else None


def _drop_tail(q):
    v = re.sub(r"(吗|呢|啊|呀)[？?]?$", "", q) + "？"
    return v if v != q else None


def _num_swap(q):
    if "多少年" in q:
        return q.replace("多少年", "几年")
    if "几年" in q:
        return q.replace("几年", "多少年")
    if "多少钱" in q:
        return q.replace("多少钱", "罚多少")
    return None


def _modal_swap(q):
    """可以 ↔ 能。只换第一次出现，避免整句读起来别扭。"""
    if "可以" in q:
        return q.replace("可以", "能", 1)
    if "能" in q:
        return q.replace("能", "可以", 1)
    return None


def _need_swap(q):
    if "需要" in q:
        return q.replace("需要", "要", 1)
    if "要" in q:
        return q.replace("要", "需要", 1)
    return None


def _what_swap(q):
    if "什么" in q:
        return q.replace("什么", "啥", 1)
    return None


def _subject_ellipsis(q):
    """去掉开头的准驾车型代号后的「驾驶证/驾照/本」，口语里常省。"""
    v = re.sub(r"^([A-Z]\s*\d?)\s*(?:驾驶证|驾照|本)\s*", r"\1", q)
    return v if v != q else None


def _prefix_polite(q):
    return "请问" + q.lstrip("？?")


RULES = [
    ("去礼貌前缀", _strip_polite),
    ("去句末语气词", _drop_tail),
    ("多少年↔几年", _num_swap),
    ("可以↔能", _modal_swap),
    ("需要↔要", _need_swap),
    ("什么→啥", _what_swap),
    ("省略「驾驶证/驾照」", _subject_ellipsis),
    ("加礼貌前缀", _prefix_polite),
]


def key_of(r):
    return (r or {}).get("fact")


def load_cases():
    """统一成 (id, q, must_patterns) 三元组。两个题库结构不同，各自适配。"""
    cases = []
    try:
        from web100_bank import as_dicts
        for d in as_dicts():
            cases.append((d["id"], d["q"], d.get("must") or []))
    except Exception as e:
        print(f"[警告] 100 题库读取失败：{e}")
    try:
        from bank_200 import BANK
        for i, row in enumerate(BANK):
            dom, q, must, forbid, grade, note = row[:6]
            # 200 题库的 must 是「组间 AND、组内 OR」，压成一条正则来判断要点在不在
            pats = []
            for group in must:
                pats.append("|".join(group))
            cases.append((f"B{i:03d}", q, pats))
    except Exception as e:
        print(f"[警告] 200 题库读取失败：{e}")
    return cases


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    show = 20
    if "--show" in sys.argv:
        show = int(sys.argv[sys.argv.index("--show") + 1])

    import fact_table as FT
    cases = load_cases()
    print(f"考点 {len(cases)} 条（100 题库 + 200 题库）")

    # 基线：原问句自己先跑一遍，只有原句答得上的才纳入变体统计，
    # 否则「变体没命中」可能是原题本来就没命中，混在一起看不出问题。
    base = {}
    for cid, q, must in cases:
        r = FT.lookup(q)
        ok_must = all(re.search(p, (r or {}).get("answer") or "") for p in must)
        base[cid] = {"q": q, "must": must, "key": key_of(r), "ok": bool(r) and ok_must,
                     "has_fact": bool(r)}

    usable = [c for c in cases if base[c[0]]["ok"]]
    print(f"其中原句事实层能答上且满足要点的：{len(usable)} 条（变体统计只用这批）\n")

    stat = {}
    misses = []
    total = hit = 0
    for rule_name, fn in RULES:
        s_total = s_key = s_must = 0
        for cid, q, must in usable:
            v = fn(q)
            if not v:
                continue
            s_total += 1
            r = FT.lookup(v)
            same = key_of(r) == base[cid]["key"]
            ok_must = all(re.search(p, (r or {}).get("answer") or "") for p in must)
            if same:
                s_key += 1
            if ok_must:
                s_must += 1
            if not (same and ok_must):
                misses.append({"rule": rule_name, "id": cid, "q": q, "v": v,
                               "want": base[cid]["key"], "got": key_of(r),
                               "must_ok": ok_must})
        if s_total:
            stat[rule_name] = (s_total, s_key, s_must)
            total += s_total
            hit += s_must

    print("按变体类型看（同 key = 命中同一个事实层处理器；要点 = 答案仍满足判分要点）")
    for name, (t, k, m) in sorted(stat.items(), key=lambda x: x[1][2] / x[1][0]):
        print(f"  {name:16s} 样本 {t:3d}  同key {k:3d} ({k / t:5.1%})  要点达 {m:3d} ({m / t:5.1%})")

    pct = hit / total if total else 0
    print(f"\n合计 {total} 个变体，要点达 {hit}（{pct:.1%}）")
    print(f"未达标 {len(misses)} 个")

    if misses:
        print(f"\n未达标样例（前 {min(show, len(misses))} 个）：")
        for m in misses[:show]:
            print(f"  [{m['rule']}] {m['id']}")
            print(f"      原：{m['q']}")
            print(f"      变：{m['v']}")
            print(f"      期望 {m['want'] or '走检索层'}  实际 {m['got'] or '走检索层'}  "
                  f"要点{'过' if m['must_ok'] else '缺'}")

    print("\n解读：同 key 但要点缺 = 命中了对的处理器但守卫把细节丢了；"
          "key 不同 = 被别的处理器抢答或掉回检索层。")
    print("　两类都要人看一眼，但先看「key 不同」——那是答非所问的源头。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
