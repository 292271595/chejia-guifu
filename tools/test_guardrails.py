# -*- coding: utf-8 -*-
"""离线测试确定性护栏：只跑正则后处理，不调用任何模型"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

from rag_engine import RAGEngine

e = object.__new__(RAGEngine)

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
    print("=" * 70)
    print(f"[{name}] 期望：{expect} → 实际：{changed}")
    print("  原文:", text)
    print("  输出:", out.replace("\n", " ⏎ "))
