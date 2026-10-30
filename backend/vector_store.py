"""
向量数据库模块 —— 基于 ChromaDB
使用混合检索：向量相似度 + 中文关键词匹配，确保中文语义检索质量
嵌入模型：BAAI/bge-m3（通过硅基流动 SiliconFlow API，免费云端推理）
"""
import os

# 离线优先：必须在导入 chromadb/transformers 之前设置，避免加载模型时联网校验
# （无外网或代理受限环境下，联网校验会直接抛 502 导致服务起不来）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import re
import time
import chromadb
from chromadb.config import Settings
from typing import List, Dict
from chromadb import EmbeddingFunction, Documents, Embeddings
from openai import OpenAI


class CloudEmbeddingFunction(EmbeddingFunction):
    """云端嵌入（OpenAI 兼容接口）—— 硅基流动 BAAI/bge-m3（免费、1024 维、国内直连）。

    为什么把嵌入搬到云端（而不是继续用本地 sentence-transformers）：
      · 云托管 0.5 核 1G 跑不动 torch：镜像 1.5GB、常驻内存 500MB+、冷启动 20s+；
      · 云端嵌入把「模型常驻内存」换成「一次 HTTP 往返」（实测 150~400ms），规格可降到 1G 以内。

    风险与对策（这是放弃本地模型必须付的账）：
      · 网络抖动 / 额度用尽 → 检索会整体失效。对策：VectorStore.search 内置纯 BM25 降级，
        嵌入不可用时检索仍可用，只是语义召回变弱，绝不返回 500；
      · 查询期超时必须短且**禁用 SDK 自动重试**：openai SDK 的 max_retries 会把一次 8s
        超时变成 3 次重试共 24s，直接吃光 15s 预算。
    """
    BATCH_SIZE = 32  # 每批最多 32 条，避免单次请求过大被拒

    def __init__(self, api_key: str, api_base: str = "https://api.siliconflow.cn/v1",
                 model: str = "BAAI/bge-m3", dimension: int = 1024, timeout: float = 8.0):
        if not api_key:
            raise ValueError("云端嵌入需要 API Key（EMBED_API_KEY 或 API_KEY）")
        # max_retries=0：重试必须由调用方决定，SDK 静默重试会放大延迟
        self.client = OpenAI(base_url=api_base, api_key=api_key,
                             timeout=timeout, max_retries=0)
        self.model = model
        self.dimension = dimension
        self.api_base = api_base
        print(f"[CloudEmbedding] {model} (dim={dimension}, timeout={timeout}s) via {api_base}")

    def __call__(self, texts: Documents) -> Embeddings:
        texts = list(texts)
        if not texts:
            return []
        all_embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i:i + self.BATCH_SIZE]
            # 空串会被部分服务端直接 400，替换成一个空格（语义上等价于无内容）
            batch = [t if (t and t.strip()) else " " for t in batch]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            all_embeddings.extend(d.embedding for d in sorted(resp.data, key=lambda x: x.index))
            if len(texts) > self.BATCH_SIZE:
                print(f"  [Embedding] batch {i // self.BATCH_SIZE + 1}: {len(batch)} texts")
        return all_embeddings


# 向后兼容：旧代码/配置里写的 siliconflow 后端名仍指向同一实现
SiliconFlowEmbeddingFunction = CloudEmbeddingFunction


class ZhipuEmbeddingFunction(EmbeddingFunction):
    """使用智谱 AI 的 embedding-3 模型（OpenAI 兼容接口）。
    零下载、零余额门槛：注册赠送 2000 万 token，本库 725 片段仅消耗约 25 万 token。
    维度固定 1024，与 bge-m3 一致，便于统一配置。"""
    BATCH_SIZE = 32

    def __init__(self, api_key: str, api_base: str = "https://open.bigmodel.cn/api/paas/v4",
                 model: str = "embedding-3", dimensions: int = 1024):
        if not api_key:
            raise ValueError("api_key is required for Zhipu embedding API")
        self.client = OpenAI(base_url=api_base, api_key=api_key, timeout=120.0, max_retries=2)
        self.model = model
        self.dimensions = dimensions
        self.dimension = dimensions  # 统一属性名，供 VectorStore 做维度校验
        print(f"[ZhipuEmbedding] Using {model} (dim={dimensions}) via {api_base}")

    def __call__(self, texts: Documents) -> Embeddings:
        all_embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = list(texts[i:i + self.BATCH_SIZE])
            resp = self.client.embeddings.create(
                model=self.model, input=batch, dimensions=self.dimensions
            )
            batch_embs = [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
            all_embeddings.extend(batch_embs)
            if len(texts) > self.BATCH_SIZE:
                print(f"  [Embedding] batch {i // self.BATCH_SIZE + 1}: {len(batch)} texts")
        return all_embeddings


class LocalSTEmbeddingFunction(EmbeddingFunction):
    """本地 sentence-transformers 中文嵌入（零 API、零充值）。
    默认 bge-small-zh-v1.5（约 95MB，512 维，下载快、CPU 编码快）；
    可通过环境变量 EMBED_MODEL 换成 bge-base-zh-v1.5 / BAAI/bge-m3（更强，但更大更慢）。
    注意：换模型必须用 build_kb.py 重建索引，不同模型向量空间不兼容。"""
    def __init__(self, model_name: str = None):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError("本地嵌入需要依赖，请先运行：pip install sentence-transformers torch")
        # 离线模式：模型权重已缓存到本地，禁止联网校验（避免代理/断网导致 502 启动失败）
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        self.model_name = model_name or os.getenv("LOCAL_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
        print(f"[LocalEmbed] 正在加载本地模型 {self.model_name} ...")
        self.model = SentenceTransformer(self._resolve_local_path(self.model_name))
        try:
            dim = self.model.get_embedding_dimension()
        except AttributeError:
            dim = self.model.get_sentence_embedding_dimension()
        self.dimension = dim  # 统一属性名，供 VectorStore 做维度校验
        print(f"[LocalEmbed] 加载完成，维度={dim}")

    @staticmethod
    def _resolve_local_path(model_name: str) -> str:
        """优先用本地已缓存的模型目录，彻底避免联网校验；未缓存则回退为仓库名（会自动下载）"""
        p = os.getenv("EMBED_MODEL_PATH")
        if p and os.path.isdir(p):
            return p
        if os.path.isdir(model_name):
            return model_name
        try:
            from huggingface_hub import snapshot_download
            local = snapshot_download(model_name, local_files_only=True)
            print(f"[LocalEmbed] 命中本地缓存: {local}")
            return local
        except Exception as e:
            print(f"[LocalEmbed] 本地缓存未命中（{type(e).__name__}），将尝试联网下载 {model_name}")
            return model_name

    def __call__(self, texts: Documents) -> Embeddings:
        embs = self.model.encode(list(texts), batch_size=16, normalize_embeddings=True)
        return embs.tolist()


class LocalBGEM3EmbeddingFunction(EmbeddingFunction):
    """本地 BAAI/bge-m3 嵌入（sentence-transformers），零 API、零成本。
    与 SiliconFlow 部署的 bge-m3 同源官方权重、同为 1024 维，向量空间一致，
    可直接复用既有 chroma_db，无需重建。"""
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError("本地嵌入需要依赖，请先运行：pip install sentence-transformers torch")
        print(f"[LocalBGE] 正在加载本地模型 {model_name}（首次会从 HuggingFace 下载约 2.3GB）...")
        self.model = SentenceTransformer(model_name)
        self.dimension = 1024  # bge-m3 固定 1024 维
        print("[LocalBGE] 本地模型加载完成")

    def __call__(self, texts: Documents) -> Embeddings:
        embs = self.model.encode(list(texts), batch_size=32)
        return embs.tolist()


def _tokenize_chinese(text: str) -> List[str]:
    """简易中文分词：按字符 bigram + 中文关键词切分 + 保留英文/数字标记，无需额外依赖"""
    text = text.lower().strip()
    
    # 收集所有 token
    tokens = set()
    
    # 1. 提取中文 + 字母数字混合 token
    # 提取连续的字母数字序列（如 C6, C1, A1, B2, 172号令, GA801 等）
    for match in re.findall(r'[a-zA-Z0-9]+', text):
        if len(match) >= 1:
            tokens.add(match)
    
    # 2. 提取纯中文字符做 bigram 分词
    cleaned = ''.join(c for c in text if '\u4e00' <= c <= '\u9fff')
    if cleaned:
        # bigram 切分
        for i in range(len(cleaned) - 1):
            tokens.add(cleaned[i:i+2])
        # 保留完整词
        tokens.add(cleaned)
        # 也加入单字
        for c in cleaned:
            tokens.add(c)
    
    return list(tokens)


def _bm25_tokenize(text: str) -> List[str]:
    """BM25 专用分词：中文 bigram + 字母数字串（不加单字/整段，避免长文档虚高）"""
    text = text.lower()
    toks = re.findall(r'[a-z0-9]+', text)
    cleaned = ''.join(c for c in text if '\u4e00' <= c <= '\u9fff')
    toks.extend(cleaned[i:i + 2] for i in range(len(cleaned) - 1))
    return toks


def _keyword_score(query: str, document: str) -> float:
    """计算查询与文档的关键词匹配得分
    使用查询词在文档中的命中率（precision），不惩罚长文档"""
    query_tokens = _tokenize_chinese(query)
    doc_tokens = set(_tokenize_chinese(document))

    if not query_tokens or not doc_tokens:
        return 0.0

    # 精确匹配：查询词在文档中命中的比例
    hits = sum(1 for t in query_tokens if t in doc_tokens)
    precision = hits / len(query_tokens)

    # 也计算 recall-like 指标避免极短文档得分过高
    recall = hits / max(len(doc_tokens), 1)

    # 综合得分：偏重 precision
    return 0.8 * precision + 0.2 * recall


# ============================================================
# 查询扩展：口语 → 法条术语
# ============================================================
# 典型失败："醉驾后多久可以继续考驾驶证"。
# 这句话里没有"吊销""不得重新取得""禁驾"任何一个法律术语，
# 向量和 BM25 都会退回去匹配字面相近的"学习驾驶证明有效期3年"
# "注销未超过2年恢复驾驶资格"，真正的第十五条反而进不了 top-8，
# 于是模型拿着"2年恢复"的片段编出了一个"吊销→注销→2年恢复"的答案。
# 做法：只对高区分度的处罚类口语补上法条术语，不改写原句（原句仍参与打分）。
_QUERY_EXPANSION = [
    (re.compile(r"(醉驾|醉酒|酒驾|饮酒后驾驶|酒后驾驶)"),
     "醉酒驾驶机动车 吊销机动车驾驶证 不得重新取得 禁驾"),
    (re.compile(r"(毒驾|吸毒|注射毒品)"),
     "吸食注射毒品 不得申请机动车驾驶证 三年内"),
    (re.compile(r"(肇事逃逸|事故逃逸|逃逸)"),
     "造成交通事故后逃逸 终生不得重新取得机动车驾驶证"),
]


def expand_query(query: str) -> str:
    """在口语问句后追加法条术语，提升处罚类问题的召回（幂等，可重复调用）。"""
    extra = [terms for pat, terms in _QUERY_EXPANSION if pat.search(query)]
    if not extra:
        return query
    tail = " ".join(extra)
    if tail in query:
        return query
    return f"{query} {tail}"


# ============================================================
# 嵌入后端统一入口
# ============================================================
def create_embedding_function(backend: str = "cloud", api_key: str = "", api_base: str = "",
                              model: str = "", timeout: float = 8.0) -> EmbeddingFunction:
    """唯一的嵌入后端工厂。

    为什么必须收口成一个函数：换嵌入模型最容易出的事故是「建库用一个、查询用另一个」
    （build_kb.py 与 main.py 各写一套判断、维度写死两处），结果库里 1024 维、查询 512 维，
    chroma 要么抛看不懂的维度错误，要么静默返回垃圾结果。后端选择只在这里做一次。

    可选值：cloud / siliconflow（硅基流动 bge-m3，1024 维）、zhipu（embedding-3）、local（本地模型）
    """
    backend = (backend or "cloud").strip().lower()
    if backend in ("cloud", "siliconflow"):
        return CloudEmbeddingFunction(
            api_key=api_key,
            api_base=api_base or os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1"),
            model=model or os.getenv("EMBED_MODEL", "BAAI/bge-m3"),
            dimension=int(os.getenv("EMBED_DIM", "1024")),
            timeout=timeout,
        )
    if backend == "zhipu":
        return ZhipuEmbeddingFunction(
            api_key=api_key,
            api_base=api_base or "https://open.bigmodel.cn/api/paas/v4",
            model=model or os.getenv("EMBED_MODEL", "embedding-3"),
            dimensions=int(os.getenv("EMBED_DIM", "1024")),
        )
    if backend == "local":
        # 注意：本地模型名走 LOCAL_EMBED_MODEL，**故意不读 EMBED_MODEL**。
        # EMBED_MODEL 是云端模型名（如 BAAI/bge-m3），混用会让 local 后端去下载
        # 一个没缓存的模型，启动直接失败——配置隔离比"少一个变量"重要。
        return LocalSTEmbeddingFunction(
            model_name=model or os.getenv("LOCAL_EMBED_MODEL") or None)
    raise ValueError(f"未知的嵌入后端: {backend}（可选 cloud / siliconflow / zhipu / local）")


class _NoEmbeddingFunction(EmbeddingFunction):
    """none 模式的占位嵌入：不产生向量，只用于类型占位，运行期绝不会被调用。

    为什么要保留这个"空嵌入"后端：它让整套代码在**没有任何嵌入服务**时仍能跑通——
    chroma 退化为纯文档仓库，检索全走 BM25。实测本项目 735 片段库纯 BM25 top-8 命中 94%，
    混合 100%，差距只在语义改写类问句。把嵌入做成可选增强，就不存在"key 过期 = 服务挂掉"。
    """
    dimension = 0

    def __call__(self, input):  # noqa: A002 - 与 EmbeddingFunction 签名一致
        raise RuntimeError("EMBED_BACKEND=none 不提供嵌入能力")


class VectorStore:
    """
    ChromaDB 封装，支持混合检索：
    1. 向量相似度检索（云端或本地嵌入）
    2. 中文关键词匹配（BM25 + bigram）
    3. 合并结果：向量分 0.55 + 关键词分 0.45

    可用性设计（重要）：向量通道是**可失败**的。嵌入服务抖动、额度用尽、网络不通时，
    search() 自动退化为纯 BM25，而不是把异常抛给上层变成 500/超时。
    法规问答里 BM25 本身召回不弱（条文术语高度集中），退化的代价是语义召回变弱，不是不可用。
    """

    def __init__(self, persist_dir: str, api_key: str = None,
                 api_base: str = "https://api.siliconflow.cn/v1",
                 collection_name: str = "vehicle_knowledge", embed_backend: str = "cloud",
                 embed_timeout: float = 8.0, verify_dim: bool = True):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.no_vector = (embed_backend or "").strip().lower() == "none"
        print(f"[VectorStore] 嵌入后端: {embed_backend}"
              + ("（纯 BM25 模式，不产生/不使用向量）" if self.no_vector else ""))
        if self.no_vector:
            self.embedding_fn = _NoEmbeddingFunction()
        else:
            self.embedding_fn = create_embedding_function(
                backend=embed_backend, api_key=api_key or "", api_base=api_base,
                timeout=embed_timeout,
            )

        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False)
        )

        if self.no_vector:
            # 复用已有 chroma 库当"文档仓库"：文档内容与向量维度无关，
            # 所以切到 none 模式**不需要重建库**，直接把 735 个片段读出来做 BM25 即可。
            try:
                self.collection = self.client.get_collection(name=collection_name)
            except Exception:
                raise RuntimeError(
                    f"未找到知识库 {collection_name}，纯 BM25 模式需要先建库：\n"
                    f"    set EMBED_BACKEND=cloud & set EMBED_API_KEY=***\n"
                    f"    python build_kb.py"
                )
        else:
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_fn,
                metadata={"hnsw:space": "cosine"}
            )

        self.stored_dim = None if self.no_vector else self._stored_dim()
        if verify_dim and not self.no_vector:
            self._verify_embedding_dimension()
        self._bm25 = None          # BM25 统计缓存（首次检索时构建）
        self._vec_fail_until = 0.0  # 向量通道熔断截止时间（失败后 60s 内直接走 BM25）
        self.vector_degraded = self.no_vector

    # ── 维度自检：换嵌入模型的头号事故就在这里 ──
    def _stored_dim(self):
        """读取库中已有向量维度（优先读 sqlite 元数据，避免把向量读进内存）"""
        import sqlite3
        db = os.path.join(self.persist_dir, "chroma.sqlite3")
        try:
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "select dimension from collections where name=?", (self.collection_name,)
                ).fetchone()
                if row and row[0]:
                    return int(row[0])
        except Exception:
            pass
        try:
            got = self.collection.get(limit=1, include=["embeddings"])
            embs = got.get("embeddings")
            if embs is not None and len(embs):
                return len(embs[0])
        except Exception:
            pass
        return None

    def _verify_embedding_dimension(self):
        """库维度 ≠ 当前嵌入维度时，启动即失败并给出可执行的重建命令。

        不做这件事的后果：服务正常起来，第一个用户提问时才在 chroma 内部抛维度错误，
        日志里只有一行 C++ 断言，排查成本极高。
        """
        if self.collection.count() == 0:
            return
        stored = self.stored_dim
        want = getattr(self.embedding_fn, "dimension", None)
        if stored and want and stored != want:
            raise RuntimeError(
                f"嵌入维度不匹配：向量库为 {stored} 维，当前嵌入后端输出 {want} 维。\n"
                f"请用同一后端重建知识库：\n"
                f"    set EMBED_BACKEND=cloud & set EMBED_API_KEY=***\n"
                f"    python build_kb.py"
            )
        if stored and want:
            print(f"[VectorStore] 维度自检通过：{stored} 维")

    def add_documents(self, texts: List[str], metadatas: List[Dict], ids: List[str]):
        """批量添加文档到向量库"""
        if not texts:
            return
        self.collection.add(
            documents=texts,
            metadatas=metadatas,
            ids=ids
        )
        print(f"[VectorStore] 成功添加 {len(texts)} 条文档到向量库")

    def _get_all_documents(self) -> List[Dict]:
        """获取全部文档用于关键词检索"""
        results = self.collection.get(include=["documents", "metadatas"])
        docs = []
        if results["ids"]:
            for i in range(len(results["ids"])):
                docs.append({
                    "id": results["ids"][i],
                    "content": results["documents"][i],
                    "metadata": results["metadatas"][i],
                })
        return docs

    def _vector_search(self, query: str, top_k: int = 10) -> List[Dict]:
        """纯向量相似度检索（异常不在此吞掉，交给 search() 统一熔断）"""
        if self.no_vector:
            return []
        total = self.collection.count()
        if total == 0:
            return []
        results = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, total),
            include=["documents", "metadatas", "distances"]
        )

        docs = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                docs.append({
                    "id": results["ids"][0][i],
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                    "score": 1 - results["distances"][0][i]
                })
        return docs

    def _build_bm25_index(self):
        """构建 BM25 倒排统计（首次检索时构建，库内容变化后自动重建）"""
        docs = self._get_all_documents()
        n = len(docs)
        df = {}
        freqs = {}
        lengths = {}
        for d in docs:
            toks = _bm25_tokenize(d["content"])
            lengths[d["id"]] = len(toks)
            c = {}
            for t in toks:
                c[t] = c.get(t, 0) + 1
            freqs[d["id"]] = c
            for t in c:
                df[t] = df.get(t, 0) + 1
        self._bm25 = {
            "n": n,
            "df": df,
            "freqs": freqs,
            "lengths": lengths,
            "avgdl": (sum(lengths.values()) / n) if n else 0.0,
            "ids": [d["id"] for d in docs],
        }
        print(f"[BM25] 索引就绪：{n} 个片段，词表 {len(df)} 项")

    def _bm25_scores(self, query: str) -> Dict[str, float]:
        """计算查询对全部片段的 BM25 得分（k1=1.5, b=0.75），罕见词（如“色盲”）权重更高"""
        import math
        if getattr(self, "_bm25", None) is None or self._bm25.get("n") != self.collection.count():
            self._build_bm25_index()
        idx = self._bm25
        n, df, avgdl = idx["n"], idx["df"], idx["avgdl"] or 1.0
        k1, b = 1.5, 0.75
        # 查询词去重计权
        q_terms = set(_bm25_tokenize(query))
        if not q_terms:
            return {}
        idf = {}
        for t in q_terms:
            d = df.get(t, 0)
            idf[t] = math.log(1 + (n - d + 0.5) / (d + 0.5))
        scores = {}
        for doc_id in idx["ids"]:
            f = idx["freqs"][doc_id]
            dl = idx["lengths"][doc_id]
            s = 0.0
            for t in q_terms:
                tf = f.get(t, 0)
                if tf:
                    s += idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
            scores[doc_id] = s
        return scores

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """混合检索：向量语义（0.55）+ BM25 关键词（0.45）"""
        # Step 0.5: 口语 → 法条术语扩展（见 expand_query 注释）
        query = expand_query(query)

        # Step 1: 向量检索（扩大候选集，避免正确片段被截断）
        # 云端嵌入意味着这一步是「网络调用」，必须可失败：失败后熔断 60 秒，
        # 期间所有查询走纯 BM25。宁可召回略弱，也不能让用户看到 500 / 等到超时。
        vec_map = {}
        if time.time() >= self._vec_fail_until:
            try:
                vec_results = self._vector_search(query, top_k=max(20, top_k * 3))
                vec_map = {d["id"]: d for d in vec_results}
            except Exception as e:
                self._vec_fail_until = time.time() + 60
                self.vector_degraded = True
                print(f"[VectorStore] 向量检索失败，60s 内降级纯 BM25 —— {type(e).__name__}: {e}")

        # Step 2: BM25 关键词检索（带 IDF，罕见术语权重更高）
        all_docs = self._get_all_documents()
        if not all_docs:
            return []
        bm25 = self._bm25_scores(query)
        max_bm25 = max(bm25.values()) if bm25 else 0.0
        idx = self._bm25 or {}

        # 罕见术语（全库命中 ≤5 个片段）：法律场景中“色盲/辨色力/醉驾”这类词区分度极高，
        # 一旦查询含此类词且片段命中，直接加权提升，避免被向量相似度稀释
        rare_terms = {
            t for t in set(_bm25_tokenize(query))
            if len(t) >= 2 and idx.get("df", {}).get(t, 0) <= 5
        }

        # 权重自适应：向量通道不可用时把权重整体让给 BM25，
        # 否则所有片段的 hybrid 上限只有 0.45，会被下面的 0.05 阈值正常放行、
        # 但排序基准与向量模式不可比，阈值也失去意义。
        w_vec, w_kw = (0.55, 0.45) if vec_map else (0.0, 1.0)

        for doc in all_docs:
            vec_score = vec_map.get(doc["id"], {}).get("score", 0.0)
            kw_score = (bm25.get(doc["id"], 0.0) / max_bm25) if max_bm25 > 0 else 0.0
            doc["score"] = vec_score
            doc["keyword_score"] = kw_score
            hybrid = w_vec * vec_score + w_kw * kw_score
            if rare_terms:
                freqs = idx.get("freqs", {}).get(doc["id"], {})
                if any(freqs.get(t, 0) for t in rare_terms):
                    hybrid += 0.25
            doc["hybrid_score"] = hybrid

        all_docs.sort(key=lambda x: x["hybrid_score"], reverse=True)

        filtered = [d for d in all_docs if d["hybrid_score"] > 0.05][:top_k]

        for d in filtered:
            d["score"] = d["hybrid_score"]

        return filtered

    def count(self) -> int:
        return self.collection.count()


def build_knowledge_base(knowledge_dir: str, persist_dir: str, api_key: str = None,
                         api_base: str = "https://api.siliconflow.cn/v1",
                         embed_backend: str = "cloud"):
    """一次性构建知识库（换嵌入后端时必须重跑，不同模型的向量空间不通用）"""
    from document_loader import DocumentLoader, chunks_to_texts_metadatas

    loader = DocumentLoader(knowledge_dir)
    chunks = loader.load_all()

    if not chunks:
        print("[build] 未找到任何知识文档！")
        return None

    texts, metadatas, ids = chunks_to_texts_metadatas(chunks)

    # 顺序很关键：必须"先构造（关闭维度自检）→ 再删旧 collection → 重新构造"。
    # 若开着自检直接构造，旧库 512 维 / 新后端 1024 维会立刻抛错，
    # 重建流程永远走不到第一步——这就是"换模型后建库脚本自己报错"的经典死锁。
    store = VectorStore(persist_dir=persist_dir, api_key=api_key, api_base=api_base,
                        embed_backend=embed_backend, verify_dim=False)

    if store.count() > 0:
        print(f"[build] 检测到已有 {store.count()} 条数据（{store.stored_dim} 维），正在重建...")
        store.client.delete_collection(store.collection_name)
        store = VectorStore(persist_dir=persist_dir, api_key=api_key, api_base=api_base,
                            embed_backend=embed_backend, verify_dim=False)

    store.add_documents(texts, metadatas, ids)
    dim = store._stored_dim()
    print(f"[build] 知识库构建完成，共 {store.count()} 条向量，维度 {dim}")
    return store