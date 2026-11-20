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
import re
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
#
# 注意这里的上限是**截断线**，不是**拒收线**——超长一律压缩后照收。
# 实测事实层答案最长 1048 字（200 题里 P99 是 930），检索层的模型回答可能更长，
# 所以给到 3000 字：真实答案基本都装得下，装不下的也不会被丢掉，只是压一压。
MAX_QUESTION = 300
MAX_ANSWER = 3000
MAX_NOTE = 500

# 云托管日志采集对单行有长度上限，一行破万字节时整行可能被丢。
# 日志是反馈的主要留存手段（容器重启文件就没了），所以宁可退化也不能被丢。
MAX_LOG_BYTES = 8000

# 群消息里愿意带多长的回答。三家积压能力差一个数量级，必须分开算：
# 飞书、钉钉单条可到 20KB；企业微信 text.content 只有 2048 **字节**，
# 中文 UTF-8 一个字 3 字节，留给答案的大约 400 字。
# 超限的后果不是报错而是「整条被拒」，排查时很难联想到长度。
ANSWER_IN_PUSH = {"feishu": 600, "dingtalk": 600, "wecom": 400}


def _fit_bytes(text: str, limit: int) -> str:
    """按**字节**截断，不是按字数。

    企业微信 text.content 的上限写的是 2048 字节而不是 2048 字，
    中文一个字 3 字节，按字数算会整整超出三倍。超了它回一个 4xx，
    而我们的推送在后台线程里，用户和页面都感知不到——反馈就这么平静地丢了。
    """
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix = "…（超长已截断）"
    keep = limit - len(suffix.encode("utf-8"))
    # 别把汉字切一半，那会变成替换字符
    return raw[:keep].decode("utf-8", "ignore") + suffix

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
    """短字段截断（问题、用户补充）。超长就砍尾巴，这些字段的信息都在开头。"""
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _trim_middle(s, n: int, tail_ratio: float = 0.35):
    """把超长文本压到 n 字以内，返回 (文本, 截断信息)。**不拒收**，只压缩。

    为什么不是简单砍尾巴：事实层答案的结构是「结论在前、依据和例外在后」。
    只留开头会把「哪些车型不适用」「什么情况下例外」整段丢掉——而这些恰恰是
    最容易答错、也最需要被看到的部分。砍中间只丢过渡和重复论证，信息损失最小。

    返回的截断信息要落进记录，否则事后看日志永远不知道「当时用户看到的到底
    有多长、我拿到的是不是全的」。
    """
    s = str(s or "").strip()
    total = len(s)
    if total <= n:
        return s, None
    tail = max(1, int(n * tail_ratio))
    head = max(1, n - tail)
    cut = total - head - tail
    text = s[:head].rstrip() + f"\n……（此处省略 {cut} 字）……\n" + s[-tail:].lstrip()
    return text, {"kept": head + tail, "total": total, "omitted": cut}


def _log_record(rec: Dict) -> None:
    """把整条反馈打成一行日志。**行太长就压缩，不能整行被丢。**

    云托管日志采集对单行有长度上限，破万字节时整行可能静默消失。而日志是反馈
    最主要的留存手段——`data/feedback/` 在容器里是临时的，重启就没了。所以这一
    步的优先级是「留下一行」而不是「一行里什么都有」。
    """
    line = "[FB] " + json.dumps(rec, ensure_ascii=False)
    if len(line.encode("utf-8")) <= MAX_LOG_BYTES:
        _log(line)
        return

    # 退化为精简行：先砍「可以再查的」长字段，short 字段（原因、补充）保留——
    # 它们只有几十字，占不了多少预算，却是判断「用户到底不满意什么」的直接依据。
    slim = {
        "id": rec.get("id"),
        "ts": rec.get("ts"),
        "date": rec.get("date"),
        "openid": rec.get("openid"),
        "question": rec.get("question"),
        "fact_hit": rec.get("fact_hit"),
        "reason": rec.get("reason"),
        "reason_label": rec.get("reason_label"),
        "note": _cut(rec.get("note"), 200),
        "answer_total": (rec.get("answer_truncated") or {}).get("total"),
        "slim": True,
    }
    budget = max(500, MAX_LOG_BYTES - len(json.dumps(slim, ensure_ascii=False).encode("utf-8")) - 200)
    slim["answer"] = _fit_bytes(rec.get("answer") or "", budget)
    line = "[FB] " + json.dumps(slim, ensure_ascii=False)
    _log(_fit_bytes(line, MAX_LOG_BYTES))


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


def _rate_key(openid: str, client_ip: str) -> str:
    """限流分桶的键。

    为什么不能直接 `openid or "anonymous"`：平台没注入 X-WX-OPENID 的场景
    （未登录、测试号、网关丢头、直接调公网接口）会让**所有用户挤进同一个
    60 条/小时的桶**。表现是最难查的一种：某个用户第一次点「回答不准」就被
    判「提交太频繁」，而他自己一条都没报过——桶是被别人用满的。

    退到来源 IP 不是精确方案（NAT 后可能重合、XFF 可伪造），但比全站共用
    一个桶好得多；这个限流本来就只防手滑和脚本，不承担安全职责。
    """
    if openid:
        return "u:" + openid
    if client_ip:
        return "ip:" + client_ip
    return "anonymous"


def _allowed(key: str) -> bool:
    """简单的滑动窗口限流。进程内内存即可——多实例部署时各算各的，
    这种量级下不追求全局精确，挡住连点就够了。"""
    key = key or "anonymous"
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
        # 飞书 text 消息体上限 20KB，这里留足余量按 10KB 收，一般不触发
        text = _fit_bytes(text, 10000)
        body = {"msg_type": "text", "content": {"text": text}}
        if _FEISHU_SECRET:
            ts, sign = _feishu_sign(_FEISHU_SECRET)
            body["timestamp"] = ts
            body["sign"] = sign
        return json.dumps(body, ensure_ascii=False).encode("utf-8")
    # 企业微信 / 钉钉：企业微信的 text.content 硬上限是 2048 字节，
    # 留 200 字节给 JSON 外壳，正文按 1800 收。
    text = _fit_bytes(text, 1800)
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
            body = _webhook_body(kind, _format_for_human(rec, kind))
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
                _log(f"[FB] webhook 被拒 kind={kind} code={code} resp={raw[:200]}")
        except Exception as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            # 用 _log 不用 print：这个线程里 stdout 是 ascii 时，
            # 含中文的 print 会抛 UnicodeEncodeError，线程静默死掉，
            # 连"发送失败"这行都留不下来——比发送失败本身更难查。
            _log(f"[FB] webhook 发送失败 kind={kind}: {e} {detail}")

    threading.Thread(target=run, daemon=True).start()


def _format_for_human(rec: Dict, kind: str = "wecom") -> str:
    """给群里看的版本。

    回答原文必须带上——用户说「这条答错了」，我要看的就是他屏幕上那段话。
    只发问题不发答案，等于把最关键的证据留在了我这边，群里只能看到一句
    「某人不满意」，还得回头去日志里捞，那配 webhook 的意义就丢了一半。

    但字数得按平台卡，不能一股脑塞：
    飞书/钉钉单条上限约 20KB，企业微信的 text.content 只有 **2048 字节**
    （中文 UTF-8 占 3 字节，约 680 字）。超了对方直接拒，而拒的理由写在
    正文里、HTTP 状态码照样 200，很容易被当成「发成功了」。
    """
    ans = rec.get("answer") or ""
    limit = ANSWER_IN_PUSH.get(kind, 400)
    total = (rec.get("answer_truncated") or {}).get("total") or len(ans)
    if len(ans) > limit:
        # 头尾都留一点：群消息里只剩开头那几句，经常正好看不到「哪些车型不适用」。
        ans, _ = _trim_middle(ans, limit, tail_ratio=0.3)
        ans = ans.rstrip() + f"\n……（原答案 {total} 字，群里只放这么多，全文见日志或导出）"

    lines = [
        f"【回答不准确】{rec.get('reason_label') or rec.get('reason') or '未选原因'}",
        f"问题：{rec.get('question')}",
    ]
    if rec.get("resolved_question") and rec["resolved_question"] != rec.get("question"):
        lines.append(f"系统理解成：{rec['resolved_question']}")
    if ans:
        lines.append("")           # 空行隔开，不然一大段贴着脸看不清
        lines.append(f"当时的回答：\n{ans}")
    else:
        # 空着不说，群消息看起来就跟「只收到问题」一样，等于没修。
        # 宁可显式写一句，说明是上报方没带，而不是消息拼装漏了。
        lines.append("当时的回答：（前端未带上来，需去日志按 id 取）")
    if rec.get("fact_hit"):
        lines.append(f"命中处理器：{rec['fact_hit']}")
    if rec.get("degraded"):
        lines.append(f"降级：{rec['degraded']}")
    if rec.get("note"):
        lines.append(f"用户补充：{rec['note']}")
    lines.append(f"时间：{rec.get('ts')}  用户：{rec.get('openid') or '-'}")
    return "\n".join(lines)


def save(payload: Dict, openid: str = "", client_ip: str = "") -> Dict:
    """收下一条反馈。返回 {"ok": bool, "id": str, "reason": str}。

    不抛异常——上报失败不该让用户看到一个红色错误框，
    他自己已经遇到一次「答错了」，再失败一次体验更差。
    """
    if not _allowed(_rate_key(openid, client_ip)):
        return {"ok": False, "id": "", "reason": "too_many"}

    now = _now()
    # 回答单独处理：超长要保留头尾，不能只砍尾巴（结论在前、例外在后，两头都得留）。
    answer, cut = _trim_middle(payload.get("answer"), MAX_ANSWER)
    rec = {
        "id": f"{now.strftime('%Y%m%d%H%M%S')}-{os.urandom(3).hex()}",
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "openid": openid or "",
        "question": _cut(payload.get("question"), MAX_QUESTION),
        "answer": answer,
        "resolved_question": _cut(payload.get("resolved_question"), MAX_QUESTION),
        "fact_hit": _cut(payload.get("fact_hit"), 60),
        "degraded": _cut(payload.get("degraded"), 40),
        "reason": _cut(payload.get("reason"), 20),
        "reason_label": _cut(payload.get("reason_label"), 20),
        "note": _cut(payload.get("note"), MAX_NOTE),
    }
    if cut:
        # 记下来：事后要是怀疑「答案是不是本来就短」，看这个就知道当时有多长。
        rec["answer_truncated"] = cut

    # 这一行是主要留存手段：日志系统会保存，容器重启也不丢。
    # 要检索就在云托管控制台搜 "[FB] "，整行是合法 JSON，复制出来可直接解析。
    _log_record(rec)

    _write_file(rec)
    _post_webhook(rec)
    return {"ok": True, "id": rec["id"], "reason": "",
            "truncated": bool(cut),
            "kept": cut["kept"] if cut else len(answer),
            "total": cut["total"] if cut else len(answer)}


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


def _writable() -> bool:
    """真的落一次盘再下结论。

    原来的写法是 `os.access(os.path.dirname(_DIR), os.W_OK)`——检查父目录
    的权限。但容器里 `data/` 这个目录**是首次写入时才创建的**（`.dockerignore`
    把它排除了，镜像里根本没有），所以服务刚起来时父目录不存在，
    `os.access` 一律返回 False。

    结果就是线上 `/health` 恒显示 `writable: false`，看着像「写不进去」，
    实际保存时 makedirs 一建就好了。排查反馈丢失时这个假警报会直接把方向
    带到权限问题上，白白绕一圈。

    真判断只有一个办法：建目录、写一个探针文件、删掉。
    """
    try:
        os.makedirs(_DIR, exist_ok=True)
        probe = os.path.join(_DIR, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def status() -> Dict:
    return {
        "dir": _DIR,
        "writable": _writable(),
        "webhook": bool(_WEBHOOK),
        # 配了 webhook 却一直收不到时，先看这两个：
        # kind 认错了平台（地址不像三家任何一个，默认按企业微信发）就会一直被拒
        "webhook_kind": _webhook_kind(_WEBHOOK) if _WEBHOOK else "",
        "feishu_signed": bool(_FEISHU_SECRET) if _WEBHOOK else False,
    }
