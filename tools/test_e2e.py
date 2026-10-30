# -*- coding: utf-8 -*-
"""端到端测试：智谱 GLM-4-Flash 生成 + 本地嵌入检索 + 自我验证 + 确定性护栏"""
import io
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

# 配置统一从项目根 .env 读：start_backend.bat 里的明文 Key 已移除，
# 脚本若继续从 bat 里抠 Key，会静默拿到空字符串，最后报一个跟配置无关的错。
ENV_PATH = r"C:\RAG\rag-vehicle-system\.env"


def _load_env(path):
    cfg = {}
    if not os.path.isfile(path):
        return cfg
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


CFG = _load_env(ENV_PATH)
key = CFG.get("API_KEY", "") or os.getenv("API_KEY", "")
EMBED_BACKEND = CFG.get("EMBED_BACKEND", "cloud")
EMBED_KEY = CFG.get("EMBED_API_KEY", "") or key

from rag_engine import create_rag_engine

engine = create_rag_engine(
    r"C:\RAG\rag-vehicle-system\chroma_db",
    "https://open.bigmodel.cn/api/paas/v4",
    key,
    "glm-4-flash",
    embed_backend=EMBED_BACKEND,
    embed_api_key=EMBED_KEY,
    embed_api_base=CFG.get("EMBED_API_BASE", ""),
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
