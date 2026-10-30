"""端到端复现：用真实 RAG 管线跑用户原问题，看 GLM-Z1-9B 是否仍会输出“初次申领A1”。
目的：隔离“检索锅”还是“生成锅”。检索结果已证正确，这里验证生成侧。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

KEY = os.getenv("API_KEY", "")
BASE = "https://api.siliconflow.cn/v1"
PERSIST = os.path.join(os.path.dirname(__file__), "..", "chroma_db")
MODEL = "THUDM/GLM-Z1-9B-0414"

from rag_engine import create_rag_engine

engine = create_rag_engine(PERSIST, BASE, KEY, MODEL)
q = "63岁能开大客车吗"
print("用户问题:", q)
print("生成模型:", MODEL)
print("=" * 70)
res = engine.query(q, top_k=8)
ans = res["answer"]
print("【最终回答】\n", ans)
print("=" * 70)
print("【是否出现错误表述“初次申领A1”】", "初次申领" in ans and "A1" in ans)
# 列出实际喂给模型的来源标题，证明检索侧给的是正确的
print("\n【实际检索到的来源(前8)】")
for i, s in enumerate(res["sources"]):
    print(f"  {i+1}. {s['metadata'].get('title','')}  score={s['score']:.3f}")
