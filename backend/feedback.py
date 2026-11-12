"""回答错误上报。

存哪儿，以及为什么
──────────────────
云托管容器的文件系统是**临时**的：部署新版本、重启、扩缩容，盘都会被重置。
只写文件的话，攒了三个月的反馈可能在某次发布后就没了，而且不会有任何报错。

所以每条反馈同时走三条路，重要性递减：

  1. 结构化日志（stdout）—— 云托管日志默认留 7 天，可在控制台按关键词检索。
     这条最可靠，因为容器怎么重启日志都还在。前缀固定为 `[FB]`，好检索。
  2. 落盘 JSONL（FEEDBACK_DIR）—— 方便集中导走，但可能随容器重置消失。
     写失败只降级，不影响接口返回。
  3. Webhook（FEEDBACK_WEBHOOK）—— 配了就往外出（企业微信机器人、
     飞书群机器人、钉钉机器人、自建接口都行）。长期台账靠这条，不配也不影响前两条。
     三家请求体格式不一样，填错地址对方只回一个 4xx，日志里只看到「发送失败」，
     所以这里按域名自动选格式，不用人去记：企业微信/钉钉是
     `{"msgtype":"text","text":{"content":...}}`，飞书是
     `{"msg_type":"text","content":{"text":...}}`。

本地开发时把 FEEDBACK_DIR 指到项目 data/feedback 就行，跟线上一致。
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# 容器默认 UTC，但反馈按人看，统一用东八区切日期。
# 不这么做的话，晚上 8 点以后提交的反馈会被记到"明天"，对不上账。
_TZ = timezone(timedelta(hours=8))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.getenv("FEEDBACK_DIR", "").strip() or os.path.join(_ROOT, "data", "feedback")
_WEBHOOK = os.getenv("FEEDBACK_WEBHOOK", "").strip()
# 飞书机器人开了「签名校验」才需要。不填就是没开，照样能发。
# 建议用签名而不是「自定义关键词」：关键词要求每条消息都含那几个字，
# 而反馈内容是用户写的，命中不了就被拒（飞书 code 19024）。
_FEISHU_SECRET = os.getenv("FEISHU_SECRET", "").strip()

# 字段长度上限。用户可能整段粘贴，不截断会把日志和文件撑爆；
# 但主要信息（问题 + 回答开头 + 选中原因）都在前几百字里，截掉不影响判断。
MAX_QUESTION = 300
MAX_ANSWER = 1500
MAX_NOTE = 500

# 限流：同一 openid 每小时最多这么多条。
# 防的不是恶意攻击，是手滑连点——同一个人一小时内报 60 条基本可以确定
# 是误触或脚本，真的"每条都答错"应该看日志而不是靠这个数。
RATE_LIMIT = 60
_WINDOW = 3600

_lock = threading.Lock()
_hits: Dict[str, List[float]] = {}


def _now() -> datetime:
    return datetime.now(_TZ)


def _cut(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _log(line: str) -> None:
    """打一行日志，编码不配合也不许抛。

    为什么不能直接 print：容器的基础镜像不保证 locale 是 UTF-8。一旦 stdout
    是 ascii，含中文的行会抛 UnicodeEncodeError——而这条 print 在 save() 里，
    抛出去就变成 500，前端只会看到「没传上去」。日志是排查手段，不能自己
    成为故障源。
    """
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        try:
            print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
        except Exception:
            pass
    except Exception:
        pass


def _allowed(openid: str) -> bool:
    """简单的滑动窗口限流。进程内内存即可——多实例部署时各算各的，
    这种量级下不追求全局精确，挡住连点就够了。"""
    key = openid or "anonymous"
    now = time.time()
    with _lock:
        arr = [t for t in _hits.get(key, []) if now - t < _WINDOW]
        if len(arr) >= RATE_LIMIT:
            _hits[key] = arr
            return False
        arr.append(now)
        _hits[key] = arr
        # 顺手清理过期的 key，避免长期运行内存只增不减
        if len(_hits) > 2000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > _WINDOW]:
                _hits.pop(k, None)
        return True


def _write_file(rec: Dict) -> bool:
    try:
        os.makedirs(_DIR, exist_ok=True)
        path = os.path.join(_DIR, f"{rec['date']}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        # 写不进去不是错——日志那一路已经保住了信息。
        # 这里必须打出来，否则「明明没人上报」会被当成真相。
        _log(f"[FB] 落盘失败（已降级，日志仍在）: {e}")
        return False


def _webhook_kind(url: str) -> str:
    """按地址判断是哪家的机器人。

    三家字段名不通用：企业微信和钉钉都是 `msgtype` + `text.content`（恰好一致），
    飞书是 `msg_type` + `content.text`。填错地址对方只会回一个 4xx，
    而日志里只看到「发送失败」，很容易误判成网络问题。
    """
    u = (url or "").lower()
    if "feishu.cn" in u or "larksuite.com" in u:
        return "feishu"
    if "dingtalk.com" in u:
        return "dingtalk"
    # 默认按企业微信处理，兼容以前只配企业微信的老配置
    return "wecom"


def _feishu_sign(secret: str):
    """飞书签名，算法和常见写法是反的：密钥是「时间戳\\n密钥」，消息体为空。

    时间戳离当前超过 1 小时会被判过期（返回 code 19021），
    所以这个值必须在发送那一刻算，不能缓存。
    """
    ts = str(int(time.time()))
    key = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(key, digestmod=hashlib.sha256).digest()
    return ts, base64.b64encode(digest).decode("utf-8")


def _webhook_body(kind: str, text: str) -> bytes:
    if kind == "feishu":
        body = {"msg_type": "text", "content": {"text": text}}
        if _FEISHU_SECRET:
            ts, sign = _feishu_sign(_FEISHU_SECRET)
            body["timestamp"] = ts
            body["sign"] = sign
        return json.dumps(body, ensure_ascii=False).encode("utf-8")
    return json.dumps({"msgtype": "text", "text": {"content": text}},
                      ensure_ascii=False).encode("utf-8")


def _post_webhook(rec: Dict) -> None:
    if not _WEBHOOK:
        return
    # 后台线程发，失败只记一行。上报接口的响应时间不能被外部服务拖住——
    # 用户点"提交"后卡三秒，下次就不会再点了。
    def run():
        kind = _webhook_kind(_WEBHOOK)
        try:
            import urllib.request
            body = _webhook_body(kind, _format_for_human(rec))
            req = urllib.request.Request(
                _WEBHOOK, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8", "replace")
            # 三家成功都回 HTTP 200，真正的结果在正文里（code / errcode）。
            # 只看状态码会把「被拒」当成「发成功」，反馈就这么静默丢了。
            try:
                j = json.loads(raw or "{}")
            except Exception:
                j = {}
            code = j.get("code", j.get("errcode", 0))
            if code not in (0, None):
                print(f"[FB] webhook 被拒 kind={kind} code={code} resp={raw[:200]}")
        except Exception as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            print(f"[FB] webhook 发送失败 kind={kind}: {e} {detail}")

    threading.Thread(target=run, daemon=True).start()


def _format_for_human(rec: Dict) -> str:
    """给群里看的一行版。"""
    lines = [
        f"【回答不准确】{rec.get('reason_label') or rec.get('reason') or '未选原因'}",
        f"问题：{rec.get('question')}",
    ]
    if rec.get("resolved_question") and rec["resolved_question"] != rec.get("question"):
        lines.append(f"系统理解成：{rec['resolved_question']}")
    if rec.get("fact_hit"):
        lines.append(f"命中处理器：{rec['fact_hit']}")
    if rec.get("note"):
        lines.append(f"用户补充：{rec['note']}")
    lines.append(f"时间：{rec.get('ts')}  用户：{rec.get('openid') or '-'}")
    return "\n".join(lines)


def save(payload: Dict, openid: str = "") -> Dict:
    """收下一条反馈。返回 {"ok": bool, "id": str, "reason": str}。

    不抛异常——上报失败不该让用户看到一个红色错误框，
    他自己已经遇到一次「答错了」，再失败一次体验更差。
    """
    if not _allowed(openid):
        return {"ok": False, "id": "", "reason": "too_many"}

    now = _now()
    rec = {
        "id": f"{now.strftime('%Y%m%d%H%M%S')}-{os.urandom(3).hex()}",
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "openid": openid or "",
        "question": _cut(payload.get("question"), MAX_QUESTION),
        "answer": _cut(payload.get("answer"), MAX_ANSWER),
        "resolved_question": _cut(payload.get("resolved_question"), MAX_QUESTION),
        "fact_hit": _cut(payload.get("fact_hit"), 60),
        "degraded": _cut(payload.get("degraded"), 40),
        "reason": _cut(payload.get("reason"), 20),
        "reason_label": _cut(payload.get("reason_label"), 20),
        "note": _cut(payload.get("note"), MAX_NOTE),
    }

    # 这一行是主要留存手段：日志系统会保存，容器重启也不丢。
    # 要检索就在云托管控制台搜 "[FB] "，整行是合法 JSON，复制出来可直接解析。
    _log("[FB] " + json.dumps(rec, ensure_ascii=False))

    _write_file(rec)
    _post_webhook(rec)
    return {"ok": True, "id": rec["id"], "reason": ""}


def load(day: str) -> List[str]:
    """按天取原始行。导出用，不做任何加工。"""
    path = os.path.join(_DIR, f"{day}.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


def list_days() -> List[Dict]:
    """有哪些天有反馈、各多少条。"""
    out = []
    try:
        for name in sorted(os.listdir(_DIR), reverse=True):
            if not name.endswith(".jsonl"):
                continue
            p = os.path.join(_DIR, name)
            with open(p, encoding="utf-8") as f:
                n = sum(1 for ln in f if ln.strip())
            out.append({"day": name[:-6], "count": n,
                        "size": os.path.getsize(p)})
    except Exception as e:
        print(f"[FB] 列举失败: {e}")
    return out


def status() -> Dict:
    return {
        "dir": _DIR,
        "writable": os.access(os.path.dirname(_DIR) or ".", os.W_OK),
        "webhook": bool(_WEBHOOK),
        # 配了 webhook 却一直收不到时，先看这两个：
        # kind 认错了平台（地址不像三家任何一个，默认按企业微信发）就会一直被拒
        "webhook_kind": _webhook_kind(_WEBHOOK) if _WEBHOOK else "",
        "feishu_signed": bool(_FEISHU_SECRET) if _WEBHOOK else False,
    }
