# -*- coding: utf-8 -*-
"""检索质量验证：只跑向量+关键词混合检索，不调用生成模型"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

from vector_store import VectorStore

store = VectorStore(persist_dir=r"C:\RAG\rag-vehicle-system\chroma_db", embed_backend="local")
print("库内片段数:", store.count())

QUERIES = [
    "63岁能开大客车吗",
    "A1大型客车驾驶证申请年龄上限是多少",
    "超过63周岁还能继续开大型客车吗",
]

for q in QUERIES:
    print("\n" + "=" * 78)
    print("问题:", q)
    for i, r in enumerate(store.search(q, top_k=6), 1):
        snippet = r["content"].replace("\n", " ")[:120]
        print(f"  [{i}] score={r['score']:.3f} 来源={r['metadata'].get('title','')[:24]}")
        print(f"      {snippet}")
