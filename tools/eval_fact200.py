# -*- coding: utf-8 -*-
"""确定性事实层离线评测（不调大模型）。

对 200 题库逐题调用 fact_table.lookup：
- 命中直答 → 用与 run200.py 相同的三态判分（must/forbid 锚点）
- 未命中   → 记为 RAG（交回检索+生成通道，由 run200.py 评测）

用途：改完事实层先跑这个，5 秒内看出"新增覆盖"与"回归"。
"""
import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "backend"))

from bank_200 import BANK            # noqa: E402
import fact_table as FT              # noqa: E402
from run200 import judge             # noqa: E402

# run200 导入时已把 stdout 重定向为 utf-8，这里不要再包一层（会关闭底层句柄）

OUT = os.path.join(_HERE, "eval_fact200.json")


def main():
    rows, fail, suspect, rag = [], [], [], []
    for i, (dom, q, must, forbid, grade, note) in enumerate(BANK, 1):
        r = FT.lookup(q)
        if not r:
            rag.append((i, q))
            rows.append(dict(idx=i, domain=dom, question=q, status="RAG",
                             fact_hit=None, must=must, forbid=forbid, note=note))
            continue
        status, miss, bad = judge(r["answer"], must, forbid)
        rows.append(dict(idx=i, domain=dom, question=q, status=status,
                         fact_hit=r.get("fact"), miss=miss, forbid_hit=bad,
                         answer=r["answer"], must=must, forbid=forbid, note=note))
        if status == "FAIL":
            fail.append((i, q, miss, r.get("fact")))
        elif status == "SUSPECT":
            suspect.append((i, q, bad, r.get("fact")))

    n = len(BANK)
    fact_n = n - len(rag)
    print(f"共 {n} 题：事实层直答 {fact_n}，走 RAG {len(rag)}")
    print(f"直答中 PASS {fact_n - len(fail) - len(suspect)} / SUSPECT {len(suspect)} / FAIL {len(fail)}")

    if fail:
        print("\n=== FAIL（事实层答错或缺锚点）===")
        for i, q, miss, f in fail:
            print(f"{i:03d} {f:<16} {q}\n    缺: {miss}")
    if suspect:
        print("\n=== SUSPECT（可能含错误表述）===")
        for i, q, bad, f in suspect:
            print(f"{i:03d} {f:<16} {q}\n    命中: {bad}")

    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(rows, fp, ensure_ascii=False, indent=1)
    print(f"\n明细已写入 {OUT}")


if __name__ == "__main__":
    main()
