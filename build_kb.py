"""
一次性构建知识库脚本
运行后，将所有 markdown 文档加载、分块、向量化，存入 ChromaDB

用法（Windows 命令提示符）：
    set EMBED_BACKEND=cloud
    set EMBED_API_KEY=你的硅基流动Key
    python build_kb.py

依赖注意：
    本项目默认嵌入走**云端**（硅基流动 BAAI/bge-m3，1024 维，免费），
    因此不需要 torch / sentence-transformers，装 requirements.txt 就能建库。
    若确实要用本地模型（EMBED_BACKEND=local），另外执行：
        pip install -r requirements-local.txt

换嵌入后端 = 换向量空间，必须重跑本脚本；不同后端的向量不能混用。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))


def _load_dotenv(path: str) -> None:
    """把项目根 .env 读进环境变量（真实环境变量优先），与 main.py 行为保持一致"""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)


def main():
    _load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    knowledge_dir = os.path.join(PROJECT_ROOT, "data", "knowledge_base")
    persist_dir = os.path.join(PROJECT_ROOT, "chroma_db")

    EMBED_BACKEND = os.getenv("EMBED_BACKEND", "cloud").strip().lower()
    # 嵌入 Key 与生成 Key 分开：生成走智谱免费额度，嵌入走云端嵌入服务免费额度
    API_KEY = (os.getenv("EMBED_API_KEY", "") or os.getenv("API_KEY", "")).strip()

    if EMBED_BACKEND in ("cloud", "siliconflow"):
        API_BASE = os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1")
    elif EMBED_BACKEND == "zhipu":
        API_BASE = os.getenv("EMBED_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
    else:  # local
        API_BASE = ""

    if EMBED_BACKEND != "local" and not API_KEY:
        print(f"[build_kb] 错误：{EMBED_BACKEND} 后端需要 EMBED_API_KEY（或 API_KEY）环境变量")
        sys.exit(1)

    print(f"[build_kb] 知识库目录: {knowledge_dir}")
    print(f"[build_kb] 向量存储目录: {persist_dir}")
    print(f"[build_kb] 嵌入后端: {EMBED_BACKEND} (base={API_BASE or 'local'})")

    from vector_store import build_knowledge_base

    result = build_knowledge_base(knowledge_dir, persist_dir, api_key=API_KEY,
                                  api_base=API_BASE, embed_backend=EMBED_BACKEND)
    if result:
        print("\n知识库构建成功！")
        print(f"   总文档块: {result.count()}")
        print(f"   向量维度: {result._stored_dim()}")
    else:
        print("\n知识库构建失败，请检查目录是否正确")


if __name__ == "__main__":
    main()
