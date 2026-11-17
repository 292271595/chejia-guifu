# -*- coding: utf-8 -*-
"""入口词准入检查。

规则不是“加个词顺手跑一道题”，而是：
  1. 至少两条正向同义问法，证明接住的是一类；
  2. 至少一条反向守卫问法，证明没抢别的处理器；
  3. 正反向都必须在登记表里，不能只在提交说明里写“测过了”。

这个脚本只检查已登记的词。新增入口词仍要人来登记——但一旦登记，
以后每次回归都会替你把正向和反向都跑一遍，避免“当时测过、后来改坏”。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

REGISTRY = os.path.join(ROOT, "data", "evaluation", "entry_terms.json")


def _hit(fact, prefix):
    return bool(fact) and str(fact).startswith(prefix)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    import fact_table as FT

    with open(REGISTRY, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries") or []
    errors = []
    pos_n = neg_n = 0

    if not entries:
        errors.append("登记表为空：新增入口词必须登记")
    terms = [e.get("term") for e in entries]
    for term in set(terms):
        if terms.count(term) > 1:
            errors.append(f"入口词重复登记：{term}")

    for e in entries:
        term = e.get("term") or "<未命名>"
        if not e.get("handler"):
            errors.append(f"{term}: 缺 handler，无法知道它属于哪个处理器")
        if not e.get("why"):
            errors.append(f"{term}: 缺 why，无法判断是否真有必要新增")
        pos = e.get("positive") or []
        neg = e.get("negative") or []
        if len(pos) < 2:
            errors.append(f"{term}: 正向问法少于 2 条，不能证明覆盖一类")
        if len(neg) < 1:
            errors.append(f"{term}: 没有反向守卫，禁止登记")

        for item in pos:
            q, expect = item.get("q"), item.get("expect")
            if not q or not expect:
                errors.append(f"{term}: 正向项缺 q/expect")
                continue
            pos_n += 1
            fact = (FT.lookup(q) or {}).get("fact")
            if not _hit(fact, expect):
                errors.append(f"{term}: 正向失败 {q!r} 期望 {expect}，实际 {fact or 'RAG'}")

        for item in neg:
            q = item.get("q")
            if not q:
                errors.append(f"{term}: 反向项缺 q")
                continue
            fact = (FT.lookup(q) or {}).get("fact")
            neg_n += 1
            if item.get("expect_not"):
                if _hit(fact, item["expect_not"]):
                    errors.append(f"{term}: 反向守卫失败 {q!r} 被 {fact} 抢走 "
                                  f"（禁止前缀 {item['expect_not']}）")
            elif item.get("expect"):
                if not _hit(fact, item["expect"]):
                    errors.append(f"{term}: 反向期望失败 {q!r} 期望 {item['expect']}，"
                                  f"实际 {fact or 'RAG'}")
            else:
                errors.append(f"{term}: 反向项必须写 expect 或 expect_not")

    if errors:
        print("入口词准入检查失败：")
        for err in errors:
            print("  - " + err)
        return 1

    print(f"入口词准入：{len(entries)} 个词｜正向 {pos_n} 条｜反向守卫 {neg_n} 条，全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
