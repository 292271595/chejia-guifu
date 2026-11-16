# -*- coding: utf-8 -*-
"""离线测试确定性护栏：只跑正则后处理，不调用任何模型"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

from rag_engine import RAGEngine

e = object.__new__(RAGEngine)

results = []

CASES = [
    ("错误·原故障句", "1. 初次申领A1：年龄需在22-63周岁之间。2. 已持有A1驾驶证：需通过能力测试延长，最长不超过3年。",
     "应把'初次申领A1'改掉"),
    ("正确·已含否定", "A1不能初次申领，只能通过增驾取得。", "不得改动"),
    ("正确·法条清单", "初次申领可申请的准驾车型为A3、B2、C1、C2、C3、C4、C5、D、E、F、M、N、P，不含A1、A2、B1、C6。", "不得改动"),
    ("错误·多车型可初次申领", "A1、A2可以初次申领，年龄22-63岁。", "两个都应改"),
    ("错误·C6", "初次申领C6需满足年龄20-70岁。", "应改为增驾"),
    ("正确·增驾C6", "增驾C6需已持C1或C2满1年。", "不得改动"),
    ("正确·A3可初次申领", "A3城市公交车可以初次申领。", "不得改动"),
]

for name, text, expect in CASES:
    out = e._apply_guardrails(text, "")
    changed = "已改写" if out != text else "未改动"
    # 2026-11-16：这里原来是纯打印，没有断言，脚本永远返回 0——
    # 「护栏全过」这句话一直是空的，因为什么也没检查。
    # 期望值本来就在用例名里（「错误·」必须改，「正确·」不许改），
    # 只是没人把它变成判定。现在按名字取期望，错了就红。
    should_change = name.startswith("错误")
    good = (out != text) == should_change
    results.append((name, good, expect, changed, text, out))
    print("=" * 70)
    print(f"[{'OK  ' if good else 'FAIL'}] {name}  期望：{expect} → 实际：{changed}")
    print("  原文:", text)
    print("  输出:", out.replace("\n", " ⏎ "))

bad_cases = [r for r in results if not r[1]]
print("=" * 70)
print(f"护栏用例 {len(results) - len(bad_cases)}/{len(results)} 符合预期")
if bad_cases:
    print("\n不符合期望的：")
    for name, _, expect, changed, text, out in bad_cases:
        print(f"  {name}  期望：{expect}  实际：{changed}")
        print(f"    原文：{text}")
        print(f"    输出：{out}")
    sys.exit(1)
sys.exit(0)
