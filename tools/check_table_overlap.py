# -*- coding: utf-8 -*-
"""知识点表查重（正式工具，长期保留）

为什么需要这个脚本：

    2026-11-16 加第 9 行知识点「驾驶证有效期」时，写答案只花了 3 分钟，
    但真正的工作量在后面——先撞了 4 条已有问句（学习驾驶证明有效期、
    A/B/C/D 证几年一审），收紧守卫后才发现：**这条知识本来就有处理器在答**
    （RENEW:VALIDITY、RENEW:TEN_YEAR），而且答得比新写的更细。

    也就是说，白干了。违反的是约束一「同一事实只存一处」——不是答案错了，
    是同一个事实现在有两个来源，将来法规一改必然漂移。

    抢答能被断言抓到（答案对不上就 FAIL），重复覆盖抓不到：新行接了以后
    答案看着没错，只是悄悄盖住了原来那条更好的。所以必须单独查。

检查方式：

    对每一行，把这一行从调度里摘掉，再用它自己的 verify.questions 问一遍。
    有别的处理器还能答上来，就再判一步：那条旧答案**是否已经包含本行的
    verify.must 要素**。

      · 包含 → 真重复。两处说的是同一件事，将来必然漂移，必须处理。
      · 不包含 → 修正性覆盖。旧答案命中了问句但没答到关键点（这正是上一轮
        那 6 条错路由的形态：问审验答罚款、问能不能骑答初次申领），新行是
        在纠错，属于正当覆盖，不算重复。

    一开始这个脚本只做了前半步，把 8 行里的 7 行全判成重复，全是误报——
    因为「另一个处理器也匹配这个问句」本来就是错路由的定义。加上要素比对
    之后才分得清「重复」和「纠错」，这个区分是整个脚本的关键。

用法：
    python tools/check_table_overlap.py             # 全表检查
    python tools/check_table_overlap.py --domain edge
    python tools/check_table_overlap.py --json      # 机器可读，给 CI 用
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import fact_table as FT          # noqa: E402
import knowledge_table as KT     # noqa: E402


def handler_name(row_id: str) -> str:
    """与 knowledge_table.make_handler 里的命名保持一致。"""
    return "table_" + row_id.replace(":", "_").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="edge")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = KT.load(args.domain)
    if not rows:
        print(f"[FAIL] 表里没有可用的行：{args.domain}")
        return 1

    original = list(FT._HANDLERS)
    # 三类结果：真重复（旧答案已含本行要素）、修正性覆盖（旧答案答偏了）、唯一覆盖
    dup, corrective, unique, no_question = [], [], [], []

    for row in rows:
        rid = row["id"]
        verify = row.get("verify") or {}
        questions = verify.get("questions") or []
        must = verify.get("must") or []
        if not questions:
            no_question.append(rid)
            continue

        name = handler_name(rid)
        stripped = [h for h in original if getattr(h[0], "__name__", "") != name]
        if len(stripped) == len(original):
            print(f"[警告] 调度里没找到 {rid} 对应的处理器（{name}），查重结果不可信")

        FT._HANDLERS = stripped
        same, partial = {}, {}
        for q in questions:
            try:
                r = FT.lookup(q)
            except Exception as e:      # 单行出错不该让整轮失败
                r = None
                print(f"[警告] {rid} 的问句「{q}」查询异常：{e}")
            if not r:
                continue
            old = r.get("answer") or ""
            # 旧答案是否已经把本行的关键要素都说了——都说了就是同一件事
            covered = all(m in old for m in must) if must else False
            bucket = same if covered else partial
            bucket.setdefault(r.get("fact") or "?", []).append(q)

        if same:
            dup.append((rid, same))
        elif partial:
            corrective.append((rid, partial))
        else:
            unique.append((rid, len(questions)))

    FT._HANDLERS = original

    def _flatten(items):
        out = []
        for rid, m in items:
            for fact, qs in m.items():
                out.append({"id": rid, "old_handler": fact, "questions": qs})
        return out

    if args.json:
        print(json.dumps({
            "duplicate": _flatten(dup),
            "corrective": _flatten(corrective),
            "unique": [rid for rid, _ in unique],
            "no_verify_questions": no_question,
        }, ensure_ascii=False, indent=2))
        return 1 if dup else 0

    print(f"== 查重：{args.domain}.json，{len(rows)} 行 ==")
    if unique:
        prints = "、".join(f"{rid}({n}问)" for rid, n in unique)
        print(f"\n[唯一覆盖] {len(unique)} 行，摘掉自己就没别人答：")
        print("  " + prints)

    if no_question:
        print(f"\n[无问法] {len(no_question)} 行没写 verify.questions，无法查重：{no_question}")

    if corrective:
        print(f"\n[修正性覆盖] {len(corrective)} 行：旧处理器也命中，但没答到关键要素。")
        print("  这是错路由被纠正的痕迹，属于正当覆盖：")
        for rid, m in corrective:
            for fact, qs in m.items():
                print(f"  {rid}  ← {fact} 也命中，但缺本行关键要素")
                for q in qs[:2]:
                    print(f"        「{q}」")

    if dup:
        print(f"\n[真重复] {len(dup)} 行：旧答案**已经包含本行全部关键要素**。")
        for rid, m in dup:
            for fact, qs in m.items():
                print(f"  {rid}  ← 已由 {fact} 完整覆盖")
                for q in qs:
                    print(f"        「{q}」")
        print("\n  同一个事实现在有两个来源。不是答案错了，是将来必然漂移：")
        print("  法规一改，改一处忘一处，两条就打架。处理方式二选一——")
        print("  · 删掉多余的那行（旧答案更好就删新行）")
        print("  · 让旧处理器退休（status=retired），并把它的问法并进本行")
        return 1

    print("\n[结论] 无真重复")
    return 0


if __name__ == "__main__":
    sys.exit(main())
