"""实测：针对“63岁能开大客车(A1)”这类问题，看 BGE-M3 检索到了哪些片段。
用于定位“63岁初次申领A1”错误答案是 检索 锅 还是 生成 锅。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

KEY = os.getenv("API_KEY", "")
BASE = "https://api.siliconflow.cn/v1"
PERSIST = os.path.join(os.path.dirname(__file__), "..", "chroma_db")

from vector_store import VectorStore

store = VectorStore(PERSIST, api_key=KEY, api_base=BASE)

queries = [
    "63岁能开大型客车(A1)吗",
    "63岁能开大客车吗",
    "63周岁驾驶大型客车年龄条件",
]
for q in queries:
    print("=" * 80)
    print("查询:", q)
    print("=" * 80)
    res = store.search(q, top_k=8)
    for i, r in enumerate(res):
        c = r["content"]
        print(f"\n[片段{i+1}] score={r['score']:.3f} 来源={r['metadata'].get('title','')}")
        print("   ", c[:240].replace("\n", " "))
    # 关键判断：检索结果里有没有“A1只能增驾/初次申领不含A1”的正确约束
    blob = "\n".join(r["content"] for r in res)
    has_constraint = any(k in blob for k in ["只能通过增驾获得", "初次申领", "不能初次申领", "增驾A1"])
    has_63 = "63" in blob
    print(f"\n   >>> 检索上下文含'63周岁'信息: {has_63} | 含A1增驾/初次申领约束: {has_constraint}")
