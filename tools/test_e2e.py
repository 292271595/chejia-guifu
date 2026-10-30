# -*- coding: utf-8 -*-
"""端到端测试：智谱 GLM-4-Flash 生成 + 本地嵌入检索 + 自我验证 + 确定性护栏"""
import io
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

BAT = r"C:\RAG\start_backend.bat"
key = ""
for line in open(BAT, "r", encoding="utf-8", errors="ignore"):
    m = re.match(r"\s*set\s+API_KEY=(.+)", line.strip(), re.I)
    if m:
        key = m.group(1).strip()
        break

from rag_engine import create_rag_engine

engine = create_rag_engine(
    r"C:\RAG\rag-vehicle-system\chroma_db",
    "https://open.bigmodel.cn/api/paas/v4",
    key,
    "glm-4-flash",
    embed_backend="local",
)

QUESTIONS = [
    "63岁能开大客车吗",
    "B2驾驶证能增驾摩托车吗",
    "增驾A1有哪些途径",
]

for q in QUESTIONS:
    print("\n" + "#" * 78)
    print("问题:", q)
    t0 = time.time()
    try:
        r = engine.query(q)
        print(f"耗时: {time.time() - t0:.1f}s")
        print("-" * 78)
        print(r["answer"])
        print("-" * 78)
        print("命中来源:", "、".join(sorted({s["metadata"].get("title", "")[:20] for s in r["sources"]})))
    except Exception as e:
        print(f"出错: {type(e).__name__}: {e}")
