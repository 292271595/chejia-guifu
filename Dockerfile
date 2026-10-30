# 车驾管知识库问答 —— 微信云托管镜像
# 构建说明：云托管「代码包/仓库构建」会读本文件，无需本地装 Docker。
#
# 关键决策：嵌入走云端 API（EMBED_BACKEND=cloud），镜像里**不装 torch**。
#   · 旧方案（本地 sentence-transformers）：镜像 1.5GB、常驻内存 500MB+、
#     构建期还要下载 95MB 模型，冷启动 20s+，只能上 1 核 2G（约 86 元/月）。
#   · 现方案：镜像约 300MB、常驻内存 130MB、启动 3~5s，0.5 核 1G（约 21 元/月）即可。
# 代价是查询时多一次嵌入 HTTP 往返（150~400ms），且嵌入服务不可用时靠 BM25 降级兜住
# （降级逻辑在 vector_store.VectorStore.search，不是靠运维盯着）。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBED_BACKEND=cloud \
    GEN_BACKEND=zhipu \
    QUERY_TIMEOUT=12

WORKDIR /app

# 只需要 curl（健康检查用）；libgomp1 是 torch 的运行期依赖，本镜像已不需要
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app/backend/
COPY chroma_db/ /app/chroma_db/
COPY frontend/ /app/frontend/

WORKDIR /app/backend
EXPOSE 80

# 云托管用这个判断实例是否可用。没有模型加载后启动很快，start-period 给 30s 足够宽裕。
# 注意 /health 不查知识库、不调外部 API，避免探针把冷启动或嵌入抖动误判成实例不可用。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:80/health || exit 1

# 单 worker：多 worker 会各自建一份 BM25 索引，内存翻倍，且 0.5 核下并发收益为负
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--workers", "1"]
