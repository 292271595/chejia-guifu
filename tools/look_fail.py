# -*- coding: utf-8 -*-
"""导出非 PASS 题目的完整答案，供人工逐条定性。"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(BASE, "run200_result.json"), encoding="utf-8"))

want = sys.argv[1:] if len(sys.argv) > 1 else None
for x in data:
    if want and str(x["idx"]) not in want:
        continue
    if not want and x["status"] == "PASS":
        continue
    print("=" * 78)
    print(f"[Q{x['idx']:03d}] {x['status']} | {x['domain']} | {x['question']}")
    print(f"  期望: {x['note']}   锚点缺失: {x['miss']}   forbid: {x['forbid_hit']}")
    print(f"  fact={x['fact_hit']} src={x['n_src']} {x['elapsed']}s")
    print("-" * 78)
    print(x["answer"])
