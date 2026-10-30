# -*- coding: utf-8 -*-
"""200 题结果汇总分析。"""
import io
import json
import os
import sys
from collections import Counter, defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "run200_result.json")

data = json.load(open(PATH, encoding="utf-8"))
N = len(data)
st = Counter(x["status"] for x in data)

print(f"===== 总览（{N} 题）=====")
for k in ("PASS", "SUSPECT", "FAIL"):
    print(f"  {k:<8} {st[k]:>3}  {st[k]/N*100:5.1f}%")
print(f"  接口异常  {sum(1 for x in data if not x['ok'])}")

fact = [x for x in data if x["fact_hit"]]
rag = [x for x in data if not x["fact_hit"]]
print(f"\n===== 通道分布 =====")
print(f"  确定性查表 {len(fact):>3} 题 ({len(fact)/N*100:.0f}%)  平均 {sum(x['elapsed'] for x in fact)/max(1,len(fact)):.2f}s")
print(f"  RAG 生成   {len(rag):>3} 题 ({len(rag)/N*100:.0f}%)  平均 {sum(x['elapsed'] for x in rag)/max(1,len(rag)):.1f}s")
if rag:
    print("  RAG 题目：", [x["idx"] for x in rag])

print(f"\n===== 按业务域 =====")
by = defaultdict(lambda: Counter())
for x in data:
    by[x["domain"]][x["status"]] += 1
print(f"  {'域':<8}{'题数':>5}{'PASS':>6}{'SUSPECT':>9}{'FAIL':>6}  通过率")
for d, c in sorted(by.items(), key=lambda kv: -(kv[1]["FAIL"] + kv[1]["SUSPECT"])):
    t = sum(c.values())
    print(f"  {d:<8}{t:>5}{c['PASS']:>6}{c['SUSPECT']:>9}{c['FAIL']:>6}   {c['PASS']/t*100:5.1f}%")

print(f"\n===== 按题型 =====")
for g in ("strict", "open", "gap"):
    sub = [x for x in data if x["grade"] == g]
    if not sub:
        continue
    c = Counter(x["status"] for x in sub)
    print(f"  {g:<7} {len(sub):>3} 题  PASS {c['PASS']:>3}  SUSPECT {c['SUSPECT']:>2}  FAIL {c['FAIL']:>2}")

print(f"\n===== FAIL 明细（锚点缺失）=====")
for x in data:
    if x["status"] == "FAIL":
        print(f"\n[Q{x['idx']:03d}] {x['domain']} | {x['question']}")
        print(f"  缺失锚点: {x['miss']}   期望: {x['note']}")
        print(f"  fact={x['fact_hit']} src={x['n_src']} {x['elapsed']}s")
        print("  答案:", x["answer"][:300].replace("\n", " | "))

print(f"\n===== SUSPECT 明细（命中禁止表述，需人工看）=====")
for x in data:
    if x["status"] == "SUSPECT":
        print(f"\n[Q{x['idx']:03d}] {x['question']}")
        print(f"  命中: {x['forbid_hit']}")
        print("  答案:", x["answer"][:300].replace("\n", " | "))
