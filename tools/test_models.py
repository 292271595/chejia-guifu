"""对比候选生成模型：在同一套检索结果+同一 system prompt 下，跑用户原问题，
检查哪个模型不再输出“初次申领A1”错误。仅改 MODEL，其余管线不变。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

KEY = os.getenv("API_KEY", "")
BASE = "https://api.siliconflow.cn/v1"
PERSIST = os.path.join(os.path.dirname(__file__), "..", "chroma_db")

from rag_engine import create_rag_engine

CANDIDATES = [
    "deepseek-ai/DeepSeek-V3",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "Qwen/Qwen3-32B",
    "THUDM/GLM-4-32B-0414",
]

q = "63岁能开大客车吗"
for model in CANDIDATES:
    print("=" * 78)
    print("模型:", model)
    try:
        engine = create_rag_engine(PERSIST, BASE, KEY, model)
        res = engine.query(q, top_k=8)
        ans = res["answer"]
        bad = ("初次申领" in ans) and ("A1" in ans)
        # 也检查是否出现“初次申领A1/A2/B1”这类更广义的错
        import re
        bad2 = bool(re.search(r"初次申领.{0,6}(A1|A2|B1)", ans))
        print("  是否含错误表述[初次申领A1/A2/B1]:", bad or bad2)
        print("  回答摘要:", ans[:260].replace("\n", " "))
    except Exception as e:
        print("  调用失败:", type(e).__name__, str(e)[:160])
