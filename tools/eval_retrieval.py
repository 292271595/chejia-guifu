# -*- coding: utf-8 -*-
"""检索基准测试：对比「纯向量」「纯BM25」「混合」三种模式的 top-8 命中率
不调用任何生成模型，秒级完成。用于判断是否需要升级嵌入模型。
"""
import io
import os
import re
import sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

from vector_store import VectorStore, _bm25_tokenize

# 后端跟随 .env：换嵌入模型后本基准要能测新库，不能写死 local
STORE = VectorStore(persist_dir=r"C:\RAG\rag-vehicle-system\chroma_db",
                    embed_backend=os.getenv("EMBED_BACKEND", "local"))

# (问题, 期望命中片段中的锚点关键词)
CASES = [
    ("63岁能开大客车吗", "63周岁"),
    ("增驾A1需要什么条件", "大型客车（A1）"),
    ("B2能增驾摩托车吗", "普通三轮摩托车"),
    ("红绿色盲能考驾照吗", "色盲"),
    ("C6驾驶证怎么考", "轻型牵引挂车"),
    ("醉驾被吊销几年能重考", "醉酒驾驶"),
    ("A1身高要求多少", "155厘米"),
    ("70岁以上还能开车吗", "70周岁"),
    ("驾驶证审验怎么规定的", "审验"),
    ("科目二考哪些项目", "科目二"),
    ("车辆年检多久一次", "检验"),
    ("C1增驾B1要什么条件", "中型客车（B1）"),
    ("记满12分会怎么样", "12分"),
    ("驾驶证换证需要什么材料", "换证"),
    ("摩托车增驾考试考哪几科", "科目二"),
    ("大中型客货车驾驶证申请年龄上限", "63周岁"),
]

K = 8
docs = STORE._get_all_documents()
doc_by_id = {d["id"]: d for d in docs}


def hit(doc_list, anchor):
    return sum(1 for d in doc_list if anchor in d["content"])


def rank_vector(q):
    return STORE._vector_search(q, top_k=K)


def rank_bm25(q):
    bm = STORE._bm25_scores(q)
    order = sorted(docs, key=lambda d: bm.get(d["id"], 0.0), reverse=True)[:K]
    return order


def rank_hybrid(q):
    return STORE.search(q, top_k=K)


MODES = [("纯向量(bge-small)", rank_vector), ("纯BM25", rank_bm25), ("混合(当前线上)", rank_hybrid)]

stats = defaultdict(lambda: {"hit": 0, "detail": []})

for q, anchor in CASES:
    for name, fn in MODES:
        try:
            res = fn(q)
            ok = hit(res, anchor) > 0
            # 正确片段排名
            rank = next((i + 1 for i, d in enumerate(res) if anchor in d["content"]), None)
        except Exception as e:
            ok, rank = False, None
        stats[name]["hit"] += 1 if ok else 0
        stats[name]["detail"].append((q, ok, rank))

print("=" * 78)
for name, _ in MODES:
    s = stats[name]
    print(f"{name:20s} top-{K} 命中率: {s['hit']}/{len(CASES)} = {s['hit']/len(CASES)*100:.0f}%")
print("=" * 78)

print("\n逐题明细（✓=命中，✗=未命中；数字=正确片段排名）：")
for i, (q, anchor) in enumerate(CASES):
    row = []
    for name, _ in MODES:
        _, ok, rank = stats[name]["detail"][i]
        row.append(f"{'✓' if ok else '✗'}{rank if rank else '-'}")
    print(f"  {q:26s} 向量={row[0]:>5s}  BM25={row[1]:>5s}  混合={row[2]:>5s}")
