"""
FastAPI 后端服务 —— 提供问答接口和简单前端
"""
import os
import sys
from typing import Optional

# 确保 backend 目录在 Python 路径中（支持从项目根目录运行）
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from fastapi import FastAPI, Request, staticfiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import concurrent.futures
import re

from rag_engine import RAGEngine, create_rag_engine

# ============ 配置 ============
# 生成模型后端（GEN_BACKEND）：zhipu（默认·永久免费，无需充值）| siliconflow
#   - 智谱 GLM-4-Flash：open.bigmodel.cn 注册免费 Key（送 2000 万 token），中文强、响应快
#   - 硅基流动：原方案，需充值（余额 0 时连免费模型也被 402 拦截）
# 嵌入模型：本地 BAAI/bge-m3（sentence-transformers，零 API，复用既有 chroma_db）
GEN_BACKEND = os.getenv("GEN_BACKEND", "zhipu")
if GEN_BACKEND == "siliconflow":
    API_BASE = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
    MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-V3")
else:  # zhipu 默认
    API_BASE = os.getenv("API_BASE", "https://open.bigmodel.cn/api/paas/v4")
    MODEL = os.getenv("MODEL", "glm-4-flash")
# API_KEY：智谱 / 硅基流动 Key，从环境变量或 .env 提供（不建议硬编码）
API_KEY = os.getenv("API_KEY", "")
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "local")  # local（默认·本地中文模型·零API）| zhipu（付费）| siliconflow（需余额）

# 向量库路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSIST_DIR = os.path.join(PROJECT_ROOT, "chroma_db")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# ============ 小程序接入相关配置 ============
# 微信云托管 CallContainer 的硬上限是 15 秒（官方限制，改不了）。
# 所以后端必须"抢在 15 秒前主动降级"，而不是让前端拿到一个 timeout 空白。
QUERY_TIMEOUT = float(os.getenv("QUERY_TIMEOUT", "12"))
MAX_QUESTION_LEN = int(os.getenv("MAX_QUESTION_LEN", "300"))
# 可选白名单：填了 openid（逗号分隔）就只允许这些用户提问；留空=不限制
OPENID_ALLOWLIST = {x.strip() for x in os.getenv("OPENID_ALLOWLIST", "").split(",") if x.strip()}

# ============ 启动应用 ============
app = FastAPI(title="车驾管知识库问答系统", description="RAG + 大模型")

# CORS 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局存储 RAG 引擎
rag_engine: Optional[RAGEngine] = None


def init_engine():
    """初始化 RAG 引擎（在服务启动时调用）"""
    global rag_engine
    if not API_KEY or API_KEY == "your-api-key-here":
        print("=" * 60)
        print("警告: 未设置 API_KEY，请在环境变量中设置或修改 main.py")
        print("启动后你需要配置 API 才能使用问答功能")
        print("=" * 60)
        return False

    try:
        rag_engine = create_rag_engine(PERSIST_DIR, API_BASE, API_KEY, MODEL, embed_backend=EMBED_BACKEND)
        print(f"[init] RAG 引擎初始化成功")
        return True
    except Exception as e:
        print(f"[init] RAG 引擎初始化失败: {e}")
        return False


# 初始化（启动时执行）
init_success = init_engine()


# ============ 回答结构化（给小程序用） ============
# 为什么不引第三方 markdown 库（towxml 等）：
#   ① 小程序没有原生 markdown 渲染；② 第三方解析库体积几百 KB 到 1MB+，挤占分包额度；
#   ③ 我们的回答形态是固定的几种（标题/段落/列表/表格/引用），后端切成 blocks 后，
#      前端用几个 wx:if 分支就能渲染完，最稳、最小、最好调试。
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# 列表项：-, *, +, 1. 1、1)
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s+(.*)$")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _plain(text: str) -> str:
    """去掉行内 markdown 标记（粗体/行内码/链接），前端按纯文本渲染。"""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_CODE.sub(r"\1", text)
    return text.strip()


def to_blocks(markdown_text: str):
    """
    把回答切成结构化块：
      {type:'h'|'p'|'quote', text, level?}
      {type:'list', items:[...]}
      {type:'table', header:[...], rows:[[...]]}
    """
    blocks = []
    lines = (markdown_text or "").split("\n")
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        # 表格：连续以 | 开头的行
        if s.startswith("|") and s.endswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                # 跳过 |---|:---:| 这类分隔行
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append([_plain(c) for c in cells])
                i += 1
            if rows:
                blocks.append({"type": "table", "header": rows[0], "rows": rows[1:]})
            continue

        # 标题
        m = _HEAD_RE.match(s)
        if m:
            blocks.append({"type": "h", "level": len(m.group(1)), "text": _plain(m.group(2))})
            i += 1
            continue

        # 列表（连续多项合并成一个 list 块）
        if _LIST_RE.match(s):
            items = []
            while i < n:
                mi = _LIST_RE.match(lines[i])
                if not mi:
                    break
                items.append(_plain(mi.group(1)))
                i += 1
            blocks.append({"type": "list", "items": items})
            continue

        # 引用
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({"type": "quote", "text": _plain("".join(x for x in buf if x))})
            continue

        blocks.append({"type": "p", "text": _plain(s)})
        i += 1

    return blocks


# 单实例线程池：把"检索+生成"放进线程里跑，主线程只负责计时，
# 这样才能在 12 秒时准时返回降级答案（15 秒是云托管调用链的硬上限）。
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag")


# ============ API 接口 ============
class QueryRequest(BaseModel):
    question: str
    # both=answer+blocks（网页版用）；blocks=只回结构化块（小程序用，省一半流量）
    format: str = "both"


@app.post("/api/query")
def query(request: QueryRequest, http_request: Request):
    if not rag_engine:
        return JSONResponse({
            "success": False,
            "message": "RAG 引擎未初始化，请检查 API_KEY 配置和知识库构建。",
            "answer": "",
            "blocks": [],
            "sources": []
        }, status_code=400)

    # 云托管私有链路会把调用者身份放在 header 里，等于"免费"拿到了用户标识
    openid = http_request.headers.get("X-WX-OPENID", "")
    if OPENID_ALLOWLIST and openid not in OPENID_ALLOWLIST:
        return JSONResponse({
            "success": False,
            "message": "当前为内测状态，暂未对你开放。",
            "answer": "",
            "blocks": [],
            "sources": []
        }, status_code=403)

    q = (request.question or "").strip()
    if not q:
        return JSONResponse({"success": False, "message": "问题不能为空",
                             "answer": "", "blocks": [], "sources": []}, status_code=400)
    if len(q) > MAX_QUESTION_LEN:
        q = q[:MAX_QUESTION_LEN]

    def _norm_source(s):
        """兼容两种来源结构：检索结果（含 metadata）与事实表（扁平结构）。"""
        if isinstance(s, dict) and "metadata" in s:
            return {
                "title": s["metadata"].get("title", ""),
                "source": s["metadata"].get("source", ""),
                "score": s.get("score", 0.0),
            }
        return {
            "title": s.get("title", ""),
            "source": s.get("source", ""),
            "score": s.get("score", 1.0),
        }

    try:
        fut = _EXECUTOR.submit(rag_engine.query, q)
        result = fut.result(timeout=QUERY_TIMEOUT)
    except concurrent.futures.TimeoutError:
        # 主动降级：云托管链路 15s 硬超时，走到那里前端只会看到"请求失败"
        msg = ("这个问题检索和整理用的时间超出预期了。建议把问题拆短一点再问，"
               "比如只问「检验周期」或只问「记几分」，答案会更准也更快。")
        return JSONResponse({
            "success": True,
            "answer": msg,
            "blocks": [{"type": "p", "text": msg}],
            "fact_hit": None,
            "sources": [],
            "degraded": "timeout"
        })
    except Exception as e:
        print(f"[query] 处理失败: {e}")
        msg = "服务暂时出了点问题，稍后再试一次。"
        return JSONResponse({
            "success": False,
            "message": msg,
            "answer": msg,
            "blocks": [{"type": "p", "text": msg}],
            "fact_hit": None,
            "sources": []
        }, status_code=500)

    answer = result.get("answer", "")
    blocks = to_blocks(answer)
    payload = {
        "success": True,
        "blocks": blocks,
        "fact_hit": result.get("fact_hit"),
        "sources": [_norm_source(s) for s in result.get("sources", [])]
    }
    if (request.format or "both").lower() != "blocks":
        payload["answer"] = answer          # 保留原字段：网页版仍在用
    return JSONResponse(payload)


@app.get("/health")
def health():
    """给云托管健康检查用：只回进程活跃，不查知识库（避免探针把冷启动拖长）。"""
    return {"status": "ok", "engine": bool(rag_engine)}


@app.get("/api/status")
def status():
    """检查服务状态"""
    if not rag_engine:
        return {
            "status": "not_ready",
            "message": "RAG 引擎未就绪，请检查配置",
            "document_count": 0
        }
    return {
        "status": "ready",
        "message": "服务就绪",
        "document_count": rag_engine.vector_store.count()
    }


# ============ 前端静态文件 ============
# 挂载静态文件
app.mount("/static", staticfiles.StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回主页 HTML"""
    html_path = os.path.join(FRONTEND_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
       port=8000,
        reload=True,
        log_level="info"
    )