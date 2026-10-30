# -*- coding: utf-8 -*-
"""修复后的检索链路冒烟验证：确认问答能取到正确的现行依据"""
import os, sys
sys.path.insert(0, r'C:\RAG\rag-vehicle-system\backend')
from vector_store import VectorStore

API_KEY = os.getenv("API_KEY", "")
PERSIST = r'C:\RAG\rag-vehicle-system\chroma_db'

store = VectorStore(persist_dir=PERSIST, api_key=API_KEY)

QUESTIONS = [
    "申请大型客车驾驶证的年龄上限是多少？",
    "63周岁以上还能开大货车吗？怎么办？",
    "驾驶与准驾车型不符的一次记几分？",
    "机动车驾驶人考试场地应该怎么设置？",
    "C1驾驶证可以开哪些车？",
    "增驾B2有什么要求？",
]

for q in QUESTIONS:
    print("=" * 70)
    print("问:", q)
    hits = store.search(q, top_k=2)
    if not hits:
        print("  (无结果)")
        continue
    for i, h in enumerate(hits, 1):
        text = h.get("document") or h.get("content") or str(h)
        meta = h.get("metadata", {})
        score = h.get("score", h.get("distance", ""))
        src = meta.get("source", "")
        print(f"  [{i}] 来源={src}  分={score}")
        print("      ", str(text).replace("\n", " ")[:230])
