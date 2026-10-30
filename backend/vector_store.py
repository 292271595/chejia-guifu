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
import chromadb
from chromadb.config import Settings
from typing import List, Dict
from chromadb import EmbeddingFunction, Documents, Embeddings
from openai import OpenAI


class SiliconFlowEmbeddingFunction(EmbeddingFunction):
    """使用硅基流动 SiliconFlow API 调用 BAAI/bge-m3，免费云端推理，无需下载模型"""
    BATCH_SIZE = 32  # 每批最多 32 条，避免 API 超时

    def __init__(self, api_key: str, api_base: str = "https://api.siliconflow.cn/v1", model: str = "BAAI/bge-m3"):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        print(f"[SiliconFlowEmbedding] Using {model} via {api_base}")

    def __call__(self, texts: Documents) -> Embeddings:
        all_embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i:i + self.BATCH_SIZE]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            batch_embs = [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
            all_embeddings.extend(batch_embs)
            if len(texts) > self.BATCH_SIZE:
                print(f"  [Embedding] batch {i // self.BATCH_SIZE + 1}: {len(batch)} texts")
        return all_embeddings


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
        self.model_name = model_name or os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
        print(f"[LocalEmbed] 正在加载本地模型 {self.model_name} ...")
        self.model = SentenceTransformer(self._resolve_local_path(self.model_name))
        try:
            dim = self.model.get_embedding_dimension()
        except AttributeError:
            dim = self.model.get_sentence_embedding_dimension()
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


class VectorStore:
    """
    ChromaDB 封装，支持混合检索：
    1. 向量相似度检索（ChromaDB ONNX 嵌入）
    2. 中文关键词匹配（bigram 重叠）
    3. 合并结果：向量检索候选 + 关键词检索候选，按混合分排序
    """

    def __init__(self, persist_dir: str, api_key: str = None, api_base: str = "https://api.siliconflow.cn/v1", collection_name: str = "vehicle_knowledge", embed_backend: str = "local"):
        self.persist_dir = persist_dir
        self.collection_name = collection_name

        if embed_backend == "local":
            print("[VectorStore] 使用本地中文嵌入模型（零 API、零充值）")
            self.embedding_fn = LocalSTEmbeddingFunction()
        elif embed_backend == "zhipu":
            print("[VectorStore] 使用智谱 embedding-3 嵌入（注意：该模型属付费资源包）")
            self.embedding_fn = ZhipuEmbeddingFunction(api_key=api_key or "", api_base=api_base)
        else:
            if not api_key:
                raise ValueError("api_key is required for SiliconFlow embedding API")
            print("[VectorStore] 正在初始化硅基流动 BGE-m3 嵌入 API...")
            self.embedding_fn = SiliconFlowEmbeddingFunction(api_key=api_key, api_base=api_base)
            print("[VectorStore] 嵌入 API 就绪")

        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False)
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        self._bm25 = None  # BM25 统计缓存（首次检索时构建）

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
        """纯向量相似度检索"""
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
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
        # Step 1: 向量检索（扩大候选集，避免正确片段被截断）
        vec_results = self._vector_search(query, top_k=max(20, top_k * 3))
        vec_map = {d["id"]: d for d in vec_results}

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

        for doc in all_docs:
            vec_score = vec_map.get(doc["id"], {}).get("score", 0.0)
            kw_score = (bm25.get(doc["id"], 0.0) / max_bm25) if max_bm25 > 0 else 0.0
            doc["score"] = vec_score
            doc["keyword_score"] = kw_score
            hybrid = 0.55 * vec_score + 0.45 * kw_score
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


def build_knowledge_base(knowledge_dir: str, persist_dir: str, api_key: str = None, api_base: str = "https://api.siliconflow.cn/v1", embed_backend: str = "zhipu"):
    """一次性构建知识库"""
    from document_loader import DocumentLoader, chunks_to_texts_metadatas

    loader = DocumentLoader(knowledge_dir)
    chunks = loader.load_all()

    if not chunks:
        print("[build] 未找到任何知识文档！")
        return None

    texts, metadatas, ids = chunks_to_texts_metadatas(chunks)

    store = VectorStore(persist_dir=persist_dir, api_key=api_key, api_base=api_base, embed_backend=embed_backend)

    if store.count() > 0:
        print(f"[build] 检测到已有 {store.count()} 条数据，正在重建...")
        store.client.delete_collection(store.collection_name)
        store = VectorStore(persist_dir=persist_dir, api_key=api_key, api_base=api_base, embed_backend=embed_backend)

    store.add_documents(texts, metadatas, ids)
    print(f"[build] 知识库构建完成，共 {store.count()} 条向量")
    return store