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


# ============ .env 加载（零依赖，密钥不进代码不进仓库） ============
def _load_dotenv(path: str) -> None:
    """把项目根目录的 .env 读进环境变量（已存在的真实环境变量优先）。

    为什么不引 python-dotenv：云托管里密钥来自控制台环境变量，本地才需要 .env，
    为这一个场景多一个依赖不值得。格式只支持 KEY=VALUE / # 注释，够用。
    """
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    os.environ.setdefault(k, v)
        print(f"[config] 已加载 {path}")
    except Exception as e:
        print(f"[config] .env 读取失败（忽略）: {e}")


_load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ============ 配置 ============
# 生成模型后端（GEN_BACKEND）：zhipu（默认·永久免费，无需充值）| siliconflow
#   - 智谱 GLM-4-Flash：open.bigmodel.cn 注册免费 Key（送 2000 万 token），中文强、响应快
#   - 硅基流动：原方案，需充值（余额 0 时连免费模型也被 402 拦截）
GEN_BACKEND = os.getenv("GEN_BACKEND", "zhipu")
if GEN_BACKEND == "siliconflow":
    API_BASE = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
    MODEL = os.getenv("MODEL", "deepseek-ai/DeepSeek-V3")
else:  # zhipu 默认
    API_BASE = os.getenv("API_BASE", "https://open.bigmodel.cn/api/paas/v4")
    MODEL = os.getenv("MODEL", "glm-4-flash")
# API_KEY：生成侧 Key（智谱），从环境变量或 .env 提供（不硬编码）
API_KEY = os.getenv("API_KEY", "")

# ── 嵌入（检索侧）：默认走云端，让 0.5 核 1G 规格也能跑 ──
# cloud/siliconflow = 硅基流动 BAAI/bge-m3（1024 维，免费，国内直连）
# zhipu = 智谱 embedding-3（1024 维，需资源包）；local = 本地 sentence-transformers（需 torch，约 1.5G 镜像）
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "cloud")
EMBED_API_BASE = os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
# 嵌入 Key 单独配置：生成用智谱、嵌入用硅基流动是常态，混用一个变量迟早出事
EMBED_API_KEY = os.getenv("EMBED_API_KEY", "") or API_KEY
# 查询期嵌入超时：必须远小于 15s 前端硬上限，且超时后靠 BM25 兜住
EMBED_TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "8"))

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
# 初始化失败原因：云托管上"服务活着但答不了"最常见的原因就是嵌入 Key 缺失/维度不匹配，
# 把原因留在 /health 里，排查不用翻日志
init_error: str = ""


def init_engine():
    """初始化 RAG 引擎（在服务启动时调用）。

    关键设计：**缺生成 key 不再阻止引擎初始化**。
    检索靠嵌入 key（EMBED_API_KEY），生成靠模型 key（API_KEY），是两套独立凭据。
    历史实现只要 API_KEY 为空就 return False，后果是：
      · /health 只说「缺少生成侧 API_KEY」，
      · 向量库有没有加载、嵌入通不通，全部看不到，
      · 排查时只能靠猜（实际已经为此多跑了三轮部署）。
    现在改为：嵌入通则引擎照常起来，生成侧缺失降级为「返回检索原文」，
    并在 /health 的 gen_ready 字段如实标注。
    """
    global rag_engine, init_error
    if not API_KEY or API_KEY == "your-api-key-here":
        print("=" * 60)
        print("警告: 未设置生成侧 API_KEY —— 引擎仍会初始化（可检索），")
        print("      但回答会降级为「检索原文、未经 AI 整理」。")
        print("=" * 60)

    try:
        rag_engine = create_rag_engine(
            PERSIST_DIR, API_BASE, API_KEY, MODEL,
            embed_backend=EMBED_BACKEND,
            embed_api_key=EMBED_API_KEY,
            embed_api_base=EMBED_API_BASE,
        )
        print(f"[init] RAG 引擎初始化成功（嵌入={EMBED_BACKEND}，"
              f"维度={getattr(getattr(rag_engine, 'vector_store', None), 'stored_dim', None)}，"
              f"生成={'就绪' if getattr(rag_engine, 'gen_ready', False) else '缺失'}）")
        init_error = "" if getattr(rag_engine, "gen_ready", False) else "生成侧 API_KEY 未配置（降级为检索原文）"
        return True
    except Exception as e:
        init_error = f"{type(e).__name__}: {e}"
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


# 查询线程池：把"检索+生成"放进线程里跑，主线程只负责计时，
# 这样才能在 QUERY_TIMEOUT 时准时返回降级答案（15 秒是云托管调用链的硬上限）。
#
# ⚠️ worker 数必须 ≥ 预期并发：原先只有 2 个，8 个并发请求里 6 个在队列里排队，
# 而 fut.result(timeout=) 从「提交时刻」起算、排队时间被计入，导致这些请求还没
# 开始执行就被判定超时，直接落到兜底文案。整个链路是「等 LLM 网络 I/O」的
# 阻塞型任务，不占 CPU，8~16 个 worker 不会压垮 0.5 核容器。
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=12, thread_name_prefix="rag")


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
            "message": f"RAG 引擎未初始化：{init_error or '未知原因'}。"
                       "请访问 /api/env 查看哪个环境变量没进容器。",
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
        # 生成预算与 QUERY_TIMEOUT 对齐（留 0.8s 给 blocks 序列化与网络回程）。
        # 之前 engine 内部硬编码 10s、外层 12s，中间 2s 是空的：
        # 生成 10.0s 就降级，而外层还在等到 12s，等于白扔掉 2s 预算。
        # 现在改一处 QUERY_TIMEOUT，内外同步。
        gen_budget = max(6.0, QUERY_TIMEOUT - 0.8)
        fut = _EXECUTOR.submit(lambda: rag_engine.query(q, budget=gen_budget))
        result = fut.result(timeout=QUERY_TIMEOUT)
    except concurrent.futures.TimeoutError:
        # 最后一道保险：正常情况下 RAGEngine 会按 gen_budget（=QUERY_TIMEOUT-0.8）降级返回检索原文，
        # 走到这里说明排队或检索本身已超预算（并发高峰）。
        # 不再直接吐一句「服务繁忙」，而是补一次纯检索（毫秒级），把法条原文交给用户。
        fallback = {}
        try:
            fallback = rag_engine.quick_lookup(q, top_k=3) if rag_engine else {}
        except Exception as e:
            print(f"[query] 兜底检索失败: {e}")

        if fallback.get("answer"):
            return JSONResponse({
                "success": True,
                "answer": fallback["answer"],
                "blocks": to_blocks(fallback["answer"]),
                "fact_hit": None,
                "sources": [_norm_source(s) for s in fallback.get("sources", [])],
                "degraded": "timeout_search"
            })

        msg = ("抱歉，这次检索整理没能在 10 秒内完成（服务当前响应较慢）。"
               "请稍等几秒再试一次；把问题问得更具体（例如只问「检验周期」或「记几分」）"
               "通常返回得更快。")
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
    """给云托管健康检查用：只回进程活跃，不查知识库（避免探针把冷启动拖长）。

    embed_vector 反映向量通道是否处于降级状态（云端嵌入不可用时为 false），
    排查时不用翻日志就能判断"答得差"是不是嵌入挂了导致的。
    """
    vec_ok = True
    model_name = EMBED_MODEL
    dim = None
    if rag_engine is not None:
        vs = getattr(rag_engine, "vector_store", None)
        try:
            vec_ok = not getattr(vs, "vector_degraded", False)
            fn = getattr(vs, "embedding_fn", None)
            # 实际生效的模型名以嵌入函数为准：local 后端用的是本地模型名，
            # 直接回显 EMBED_MODEL 会显示成云端模型名（bge-m3），排查时被误导
            model_name = (getattr(fn, "model_name", None) or getattr(fn, "model", None)
                          or getattr(fn, "name", None) or EMBED_MODEL)
            dim = getattr(vs, "stored_dim", None)
        except Exception:
            vec_ok, model_name, dim = True, EMBED_MODEL, None
    return {
        "status": "ok",
        "engine": bool(rag_engine),
        "gen_ready": bool(getattr(rag_engine, "gen_ready", False)) if rag_engine else False,
        "engine_error": init_error,
        "embed_backend": EMBED_BACKEND,
        "embed_model": model_name,
        "embed_dim": dim,
        "embed_vector": vec_ok,
    }


# 必需的环境变量清单：变量名 → 是否含密钥（影响展示方式）
_REQUIRED_ENV = {
    "API_KEY": True,
    "GEN_BACKEND": False,
    "MODEL": False,
    "EMBED_BACKEND": False,   # cloud / local / none，必须明文可见，填错就崩
    "EMBED_API_BASE": False,
    "EMBED_MODEL": False,
    "EMBED_API_KEY": True,
    "QUERY_TIMEOUT": False,
}


def _mask(v: str, secret: bool) -> str:
    """密钥只露前 3 位 + 长度：足够判断「是不是这条、有没有被截断」，
    又不至于因为公网可访问而把 Key 泄露出去（控制台随时可核对全文）。"""
    if not v:
        return ""
    if not secret:
        return v
    return f"{v[:3]}***(len={len(v)})"


@app.get("/api/env")
def env_report():
    """容器内环境变量体检：解决「控制台填了，但容器里到底有没有」的反复扯皮。

    只回「在不在 + 脱敏后的值」，不回完整密钥。
    设计动机：云托管环境变量是版本级配置，填了没发布、发布没成功、名字拼错、
    值带空格，四种情况在 /health 里表现完全一样（都是 engine=false），
    靠猜只能一轮轮重试。这个接口把四种情况一次性区分开。
    """
    items = {}
    missing = []
    blank = []
    for k, secret in _REQUIRED_ENV.items():
        raw = os.getenv(k)
        if raw is None:
            items[k] = {"present": False, "value": ""}
            missing.append(k)
        elif raw.strip() == "":
            items[k] = {"present": True, "value": "(空字符串)"}
            blank.append(k)
        else:
            # 值首尾带空格是控制台粘贴的高频错误，单独标出来
            dirty = raw != raw.strip()
            items[k] = {
                "present": True,
                "value": _mask(raw.strip(), secret),
                "trailing_space": dirty,
            }
            if dirty:
                blank.append(k + "(首尾有空格)")
    return {
        "missing": missing,
        "abnormal": blank,
        "ok": not missing and not blank,
        "vars": items,
        "hint": (
            "missing 里的变量在容器里根本不存在 → 控制台没保存成功，或保存后没发布新版本"
            if missing else
            ("abnormal 里的变量值异常（空/带空格）→ 重新粘贴" if blank else "环境变量齐全")
        ),
    }


@app.get("/api/status")
def status():
    """检查服务状态"""
    if not rag_engine:
        return {
            "status": "not_ready",
            "message": f"RAG 引擎未就绪：{init_error or '未知原因'}",
            "document_count": 0,
            "gen_ready": False,
        }
    return {
        "status": "ready",
        "message": "服务就绪" if getattr(rag_engine, "gen_ready", False)
                   else "检索就绪；生成侧 API_KEY 缺失，回答将降级为检索原文",
        "document_count": rag_engine.vector_store.count(),
        "gen_ready": bool(getattr(rag_engine, "gen_ready", False)),
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