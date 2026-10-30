"""
一次性构建知识库脚本
运行后，将所有 markdown 文档加载、分块、向量化，存入 ChromaDB

用法（Windows 命令提示符）：
    set API_KEY=你的智谱Key
    python build_kb.py

嵌入后端默认 zhipu（embedding-3，零下载、赠送额度充足）；
如需切回硅基流动：set EMBED_BACKEND=siliconflow
"""
import os
import sys

# 将 backend 目录加入 Python 路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

from vector_store import build_knowledge_base


def main():
    knowledge_dir = os.path.join(PROJECT_ROOT, "data", "knowledge_base")
    persist_dir = os.path.join(PROJECT_ROOT, "chroma_db")

    EMBED_BACKEND = os.getenv("EMBED_BACKEND", "zhipu").strip().lower()
    API_KEY = os.getenv("API_KEY", "").strip()

    if EMBED_BACKEND == "siliconflow":
        API_BASE = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
    else:
        API_BASE = os.getenv("API_BASE", "https://open.bigmodel.cn/api/paas/v4")

    if EMBED_BACKEND != "local" and not API_KEY:
        print("[build_kb] 错误：该嵌入后端需要 API_KEY 环境变量")
        sys.exit(1)

    print(f"[build_kb] 知识库目录: {knowledge_dir}")
    print(f"[build_kb] 向量存储目录: {persist_dir}")
    print(f"[build_kb] 嵌入后端: {EMBED_BACKEND} (base={API_BASE})")

    result = build_knowledge_base(knowledge_dir, persist_dir, api_key=API_KEY,
                                  api_base=API_BASE, embed_backend=EMBED_BACKEND)
    if result:
        print("\n知识库构建成功！")
        print(f"   总文档块: {result.count()}")
    else:
        print("\n知识库构建失败，请检查目录是否正确")


if __name__ == "__main__":
    main()
