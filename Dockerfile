# 车驾管知识库问答 —— 微信云托管镜像
# 构建说明：云托管「代码包/仓库构建」会读本文件，无需本地装 Docker。
#
# 两个容易踩的坑，写在这里免得后面返工：
# 1) torch 必须显式装 CPU 版。直接 pip install sentence-transformers 会拉 CUDA 版
#    torch（镜像 2GB+，且云托管没有 GPU，纯属浪费），所以单独一行用 CPU 源。
# 2) 嵌入模型必须在**构建期**下载落盘。vector_store 里为防联网校验失败，
#    运行时强制 HF_HUB_OFFLINE=1，镜像里没有缓存就会启动即失败。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.hf \
    HF_ENDPOINT=https://hf-mirror.com \
    EMBED_BACKEND=local \
    EMBED_MODEL=BAAI/bge-small-zh-v1.5 \
    GEN_BACKEND=zhipu \
    QUERY_TIMEOUT=12

WORKDIR /app

# libgomp1 是 sentence-transformers/torch 的运行期依赖，slim 镜像默认没有
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ① 先装 CPU 版 torch（版本与本地一致，独立一步便于利用层缓存）
RUN pip install --no-cache-dir torch==2.12.0 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ② 构建期把嵌入模型固化进镜像（约 95MB）
RUN python -c "from huggingface_hub import snapshot_download; \
    print('model cached:', snapshot_download('BAAI/bge-small-zh-v1.5'))"

COPY backend/ /app/backend/
COPY chroma_db/ /app/chroma_db/
COPY frontend/ /app/frontend/

WORKDIR /app/backend
EXPOSE 80

# 云托管会用它判断实例是否可用（冷启动要加载 735 条向量 + 嵌入模型，start-period 给足）
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:80/health || exit 1

# 单 worker：多 worker 会各自加载一份模型，内存直接翻倍（0.5G 规格必 OOM）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--workers", "1"]
