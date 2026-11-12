"""反馈推送内容测试。

为什么单独立一个：这个模块出过两次错，都是「字段漏了」而不是「逻辑错了」——
第一次是飞书请求体字段名反着写（msgtype vs msg_type），第二次是群消息里
压根没拼 answer，用户只看到问题、看不到答错的原文。

这类错误不会抛异常、不会返回非 2xx，只是内容少了。所以只能靠断言
「该有的东西在不在」「长度有没有超平台上限」来防。

跑法：python tools/test_feedback_push.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.feedback as fb

PASS, FAIL = "OK  ", "FAIL"
_ok = 0
_bad = 0


def check(name, cond, extra=""):
    global _ok, _bad
    if cond:
        _ok += 1
        print(f"{PASS} {name}")
    else:
        _bad += 1
        print(f"{FAIL} {name} {extra}")


def make_rec(**kw):
    rec = {
        "id": "20261112160000-abcdef",
        "ts": "2026-11-12 16:00:00",
        "date": "2026-11-12",
        "openid": "oTestUser123",
        "question": "免检标志还要贴吗",
        "answer": "哪些车六年免检……第 6 年、第 10 年要上线检验。",
        "resolved_question": "",
        "fact_hit": "VEHICLE:EXEMPTION",
        "degraded": "",
        "reason": "irrelevant",
        "reason_label": "答非所问",
        "note": "",
    }
    rec.update(kw)
    return rec


# ── 1. 群消息必须包含当时的回答（这是 2026-11-12 那次漏掉的字段）
rec = make_rec()
for kind in ("feishu", "wecom", "dingtalk"):
    txt = fb._format_for_human(rec, kind)
    check(f"{kind}: 消息里有回答原文", "第 6 年、第 10 年要上线检验" in txt)
    check(f"{kind}: 有「当时的回答」标签", "当时的回答" in txt)
    check(f"{kind}: 消息里有问题原文", "免检标志还要贴吗" in txt)

# ── 2. 答案为空时不能说谎，要写明是前端没带
txt = fb._format_for_human(make_rec(answer=""), "feishu")
check("答案为空: 有显式说明", "当时的回答" in txt and "未带上来" in txt)

# ── 3. 长答案要截断并注明，不能把整段塞进去
txt = fb._format_for_human(make_rec(answer="答" * 2000), "feishu")
check("长答案: 已截断并注明", "截断" in txt and len(txt) < 2000 + 300)

# ── 4. 各平台字节上限。企业微信 2048 是硬上限，超了整条被拒且只回 4xx
worst = make_rec(
    question="问" * 300, answer="答" * 1500,
    resolved_question="理" * 300, note="补" * 500,
    fact_hit="X" * 60, degraded="D" * 40, openid="o" + "x" * 27,
)
for kind, limit in (("wecom", 2048), ("dingtalk", 20000), ("feishu", 20000)):
    body = fb._webhook_body(kind, fb._format_for_human(worst, kind))
    check(f"{kind}: 最坏情况 {len(body)} 字节 < {limit}", len(body) < limit)

# ── 5. 请求体字段名必须对得上各家的要求，反了对方就回 4xx
body = json.loads(fb._webhook_body("feishu", "x").decode())
check("飞书: msg_type + content.text", "msg_type" in body and "text" in body.get("content", {}))
body = json.loads(fb._webhook_body("wecom", "x").decode())
check("企业微信/钉钉: msgtype + text.content", "msgtype" in body and "content" in body.get("text", {}))

# ── 6. _fit_bytes 不能把汉字切成半个（那会变成乱码替换符）
cut = fb._fit_bytes("中" * 1000, 100)
check("按字节截断后无替换字符", "\ufffd" not in cut and len(cut.encode("utf-8")) <= 100)

# ── 7. 平台识别：填错地址是最常见的配置错误
check("识别飞书", fb._webhook_kind("https://open.feishu.cn/open-apis/bot/v2/hook/xxx") == "feishu")
check("识别钉钉", fb._webhook_kind("https://oapi.dingtalk.com/robot/send?access_token=x") == "dingtalk")
check("识别企业微信", fb._webhook_kind("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x") == "wecom")
check("未知域名按企业微信", fb._webhook_kind("https://example.com/hook") == "wecom")

# ── 8. 飞书签名：密钥是「时间戳\\n密钥」、消息体为空，和常见写法反过来
ts, sign = fb._feishu_sign("TESTSECRET")
import base64
check("飞书签名 32 字节 SHA256", len(base64.b64decode(sign)) == 32)
check("飞书时间戳是 10 位秒级", len(ts) == 10)

# ── 9. 保存时的截断，别让一段粘贴把日志和文件撑爆
fb._DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "feedback_test_tmp")
fb._WEBHOOK = ""
res = fb.save({"question": "问" * 5000, "answer": "答" * 5000, "note": "补" * 5000}, openid="oX")
check("save 成功", res.get("ok") is True)
lines = fb.load("2026-11-12")
got = json.loads(lines[-1]) if lines else {}
check("问题截到 300 字", len(got.get("question", "")) <= fb.MAX_QUESTION + 1)
check("答案截到 1500 字", len(got.get("answer", "")) <= fb.MAX_ANSWER + 1)
check("补充截到 500 字", len(got.get("note", "")) <= fb.MAX_NOTE + 1)

# 清理测试产生的文件
try:
    import shutil
    shutil.rmtree(fb._DIR, ignore_errors=True)
except Exception:
    pass

print(f"\n通过 {_ok}/{_ok + _bad}")
sys.exit(1 if _bad else 0)
