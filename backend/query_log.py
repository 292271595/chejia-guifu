# -*- coding: utf-8 -*-
"""全量问句观测日志。

为什么不能只靠“回答不准确”反馈
────────────────────────────
反馈接口只有用户主动点踩时才有记录。没有全量问句，就没有分母：
既不知道用户最常问什么，也算不出某类错误在全部提问里占多少。
所以这里记录**每一次 /api/query**，只记诊断字段，不记用户身份和答案正文。

记录什么
────────
- 原话、改写后问句
- 命中层级（fact / rag / fallback / rewrite / refused / error）
- 事实 key
- 是否降级、耗时
- 是否有来源、历史轮数（用于区分单轮与多轮）

不记录什么
──────────
- openid、IP、header
- 答案正文（只记长度）
- 密钥、请求头

留存方式
────────
1. stdout 一行 `[QL] {json}`：云托管日志默认保留，容器重启不丢。
2. 可选 JSONL（QUERY_LOG_DIR）：本地开发、导出分析用；容器内可能随发布清空。
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

MAX_QUESTION = 300
MAX_RESOLVED = 300
MAX_CTX_REASON = 200
MAX_LINE_BYTES = 8000

# 回答正文的留存长度上限（按字符）。1500 字足够覆盖绝大多数车驾管答案
# （实测多在 300~800 字），再长的尾部通常是法条罗列，对判断"答得对不对"没有增量。
MAX_ANSWER_CHARS = 1500
# stdout 里的预览长度。云端日志单行过长会被截断，截断后整行 JSON 解析不出来，
# 等于把这条记录彻底弄丢——所以控制台那一路只带开头。
MAX_ANSWER_STDOUT = 200


def _answer_mode() -> str:
    """回答正文留存策略：full=存到上限、head=只存开头、off=完全不存。

    默认 full：只记问句不记答案，能看出"问得多"，看不出"答得差"。
    答错对象、答非所问、同一句重复三遍这类问题，恰恰只在正文里。
    """
    v = (os.getenv("QUERY_LOG_ANSWER", "full") or "full").strip().lower()
    return v if v in ("full", "head", "off") else "full"


def _answer_limit() -> int:
    try:
        return max(0, int(os.getenv("QUERY_LOG_ANSWER_MAX", MAX_ANSWER_CHARS)))
    except Exception:
        return MAX_ANSWER_CHARS


def _flatten_blocks(blocks) -> str:
    """把 blocks 还原成纯文本。

    format=blocks 时返回体里没有 answer 字段，只有结构化块。不还原就等于
    小程序侧的问答全部没有答案正文——而那恰恰是最需要看的一半。
    """
    out = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t in ("h", "p", "quote"):
            s = str(b.get("text") or "").strip()
            if s:
                out.append(s)
        elif t == "list":
            for it in b.get("items") or []:
                s = str(it or "").strip()
                if s:
                    out.append("- " + s)
        elif t == "table":
            head = [str(x or "") for x in (b.get("header") or [])]
            if head:
                out.append(" | ".join(head))
            for row in b.get("rows") or []:
                out.append(" | ".join(str(x or "") for x in row))
    return "\n".join(out)


def _answer_fields(payload: dict) -> dict:
    """回答正文 + 真实字数。

    字数按完整文本算，不按截断后的算：那个数字是"答案有多长"的度量，
    被留存上限影响的话，超过 1500 字的答案会齐刷刷显示成 1500。
    """
    text = str(payload.get("answer") or "").strip()
    if not text:
        text = _flatten_blocks(payload.get("blocks"))
    full_len = len(text)
    mode = _answer_mode()
    if mode == "off" or not text:
        return {"answer": "", "answer_chars": full_len}
    limit = _answer_limit()
    if mode == "head":
        limit = min(limit, MAX_ANSWER_STDOUT) if limit else MAX_ANSWER_STDOUT
    stored = text if not limit else (text[:limit] + "…" if len(text) > limit else text)
    return {"answer": stored, "answer_chars": full_len}


def _enabled() -> bool:
    return os.getenv("QUERY_LOG", "1").strip().lower() not in (
        "0", "false", "off", "no", "disabled",
    )


def _log_dir() -> str:
    """每次动态读环境变量，方便测试和本地切换目录。"""
    return os.getenv("QUERY_LOG_DIR", "").strip()


def _cut(value, n: int) -> str:
    s = str(value or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _fit_bytes(text: str, limit: int = MAX_LINE_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix = "…（超长已截断）"
    keep = limit - len(suffix.encode("utf-8"))
    return raw[:keep].decode("utf-8", "ignore") + suffix


def classify(payload: dict, status_code: int = 200) -> str:
    """把返回结构归成一个可统计的命中层级。

    这里刻意不把“降级”与“事实层”混在一起：
    事实层复核后回退检索原文时，fact_hit 仍然存在，但用户拿到的已经不是
    确定性命中答案，必须单独记成 fact_review_fallback。
    """
    payload = payload or {}
    if status_code >= 500 or not payload.get("success"):
        return "error"

    degraded = payload.get("degraded")
    deg = str(degraded or "")
    if payload.get("refused") or deg == "off_topic":
        return "refused"
    if deg == "blocked_input":
        return "blocked"
    if deg in ("ctx_unresolved", "ctx_ambiguous"):
        return "rewrite"
    if payload.get("fact_hit"):
        return "fact_review_fallback" if deg else "fact"
    if deg:
        return "rag_fallback"
    if payload.get("sources"):
        return "rag"
    if payload.get("answer"):
        return "rag_no_source"
    return "empty"


def build_record(question: str, payload: dict, status_code: int = 200,
                 elapsed_ms: int = 0, history_turns: int = 0) -> dict:
    payload = payload or {}
    rec = {
        "ts": datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "q": _cut(question, MAX_QUESTION),
        "resolved_q": _cut(payload.get("resolved_question"), MAX_RESOLVED),
        "layer": classify(payload, status_code),
        "fact": _cut(payload.get("fact_hit"), 80),
        "degraded": payload.get("degraded", ""),
        "ctx_reason": _cut(payload.get("ctx_reason"), MAX_CTX_REASON),
        "ms": max(0, int(elapsed_ms)),
        "history_turns": max(0, int(history_turns)),
        "source_count": len(payload.get("sources") or []),
        "success": bool(payload.get("success")),
        "status": int(status_code),
    }
    rec.update(_answer_fields(payload))
    return rec


def _stdout_view(rec: dict) -> dict:
    """控制台那一路只带回答预览，避免单行超长被云日志截断。"""
    if not rec.get("answer"):
        return rec
    view = dict(rec)
    view["answer"] = _cut(rec["answer"], MAX_ANSWER_STDOUT)
    return view


def list_days() -> list:
    """已落盘的日期列表（升序）。没配 QUERY_LOG_DIR 时为空。"""
    directory = _log_dir()
    if not directory or not os.path.isdir(directory):
        return []
    out = []
    for name in os.listdir(directory):
        if name.endswith(".jsonl") and len(name) == 16:
            out.append(name[:-6])
    return sorted(out)


def load(day: str) -> list:
    """按天读取原始行。只允许 YYYY-MM-DD，防止路径穿越。"""
    day = (day or "").strip()
    if not day or len(day) != 10:
        return []
    directory = _log_dir()
    if not directory:
        return []
    path = os.path.join(directory, f"{day}.jsonl")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f if line.strip()]
    except Exception:
        return []


def _write_file(rec: dict, directory: str) -> bool:
    try:
        os.makedirs(directory, exist_ok=True)
        day = rec["ts"][:10]
        path = os.path.join(directory, f"{day}.jsonl")
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        # 埋点失败不能影响问答；但必须留一行，避免“没人提问”的假象。
        try:
            print(f"[QL] 落盘失败（已降级，stdout 仍在）: {e}", flush=True)
        except Exception:
            pass
        return False


def log_query(question: str, payload: dict, status_code: int = 200,
              elapsed_ms: int = 0, history_turns: int = 0,
              emit: bool = True, force_dir: str = "") -> dict:
    """记录一条查询。永远不抛异常。"""
    rec = build_record(question, payload, status_code, elapsed_ms, history_turns)
    if not _enabled() and not force_dir:
        return rec

    if emit:
        line = "[QL] " + json.dumps(_stdout_view(rec), ensure_ascii=False)
        try:
            print(_fit_bytes(line), flush=True)
        except Exception:
            try:
                print(_fit_bytes(line).encode("ascii", "backslashreplace").decode("ascii"),
                      flush=True)
            except Exception:
                pass

    directory = force_dir or _log_dir()
    if directory:
        _write_file(rec, directory)
    return rec


def log_response(question: str, response, elapsed_ms: int = 0,
                 history_turns: int = 0) -> dict:
    """从 FastAPI 的 JSONResponse 里取字段并记录。"""
    payload = {}
    try:
        body = getattr(response, "body", b"") or b""
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        payload = {}
    return log_query(
        question=question,
        payload=payload,
        status_code=int(getattr(response, "status_code", 200) or 200),
        elapsed_ms=elapsed_ms,
        history_turns=history_turns,
    )
