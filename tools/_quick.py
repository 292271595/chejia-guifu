# -*- coding: utf-8 -*-
"""小样本冒烟验证：确认接口字段与三态判分逻辑。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run200  # noqa: E402  （内部已设置 utf-8 stdout）
from bank_200 import BANK  # noqa: E402

for i in (1, 21, 84, 4):
    dom, q, must, forbid, grade, note = BANK[i - 1]
    r = run200.ask(q)
    status, miss, bad = run200.judge(r["answer"], must, forbid)
    print("=" * 70)
    print(f"[{i}] {q} -> {status} fact={r['fact_hit']} {r['elapsed']}s err={r['err']}")
    print("   miss:", miss, " forbid:", bad)
    print("   ans:", r["answer"][:240].replace("\n", " | "))
