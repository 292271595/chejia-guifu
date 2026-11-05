# -*- coding: utf-8 -*-
"""事实层答案复核（LLM as reviewer）

背景
----
事实层是确定性查表，命中即直接返回、不经过模型。它的可靠性取决于「问句被路由到
哪个处理器」。关键词命中一旦把问句送错处理器，就会用确定性的口吻答出另一个制度，
而答案本身语法、法条、格式全都正常——这是 2026-11 那次线上事故（问 63 岁 A2 能否
延期继续驾驶，答成第七十五条延期换证）的形态。

本模块在事实层命中之后、返回之前插一道复核：让模型判断「这个答案讲的是不是用户问
的那件事」。

为什么不直接重答
----------------
实测（tools/exp3_recheck_via_rag.py）：复核判错后转检索生成，4 个样本耗时 5.0~10.0s，
其中 2 个超时降级为检索原文，答案质量不升反降。所以复核判错时**不重答**，改为退回
法规原文——宁可难读，不可错答。

三项实测数据（tools/exp2_review_levels.py，171 条，glm-4-flash）
--------------------------------------------------------------
  正确样本 93 条  误报 1 条（1.1%），且该条经人工核实是真错，不是误报
  真实事故  3 条  抓出 3 条（100%）
  同族错配 25 条  抓出 19 条（76%）
  同模块错配 25 条 抓出 22 条（88%）
  跨模块错配 25 条 抓出 25 条（100%）
  耗时：均值 1.70s，中位 1.37s，P90 3.54s

提示词的写法是成败关键。同一批样本，用「判断是否回答了同一件事」的宽松写法，
三个真实事故只抓到 1 个（33%）；改成「找出答案针对的情形要件，逐项对照用户问句」
的要件核查写法，三个全抓（100%）。差别不在模型能力，在于让它做「是不是」而不是
「像不像」。

失败策略
--------
复核是加分项，不是必需项。任何异常（超时、网络、解析失败）都放行原答案——
放行的代价是可能漏一个错，拦截的代价是把正确答案换成难读的原文。前者用户未必遇到，
后者每个用户都会遇到。
"""

import os
import re
import time
from typing import Dict, List, Optional

# ── 实体覆盖信号 ────────────────────────────────────────
# 零成本、零延迟的先验信号：问句里明确点出的车型/年龄/制度词，答案里有没有落到。
# 线上事故「63 岁 A2 问延期」的答案里既没有 A2 也没有 63，信号全中。
# 实测 93 条正确答案误报 2 条（2.2%），且均为同义表述（「逾期」写成「超过有效期」）。

_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(A1|A2|A3|B1|B2|C1|C2|C3|C4|C5|C6|D|E|F|M|N|P)(?![A-Za-z])")
_AGE_RE = re.compile(r"(\d{2})\s*(?:周岁|岁)")

# 制度词：同一族里结论相反的词对，最容易被答串
_SYSTEM_WORDS = ("注销", "吊销", "过期", "逾期", "延期", "延长", "暂扣", "满分", "审验", "换证", "补证", "变更登记", "变更备案")

# 同义表述：问句用了 A 词，答案用 B 词，不算丢失（否则「逾期」写成「超过有效期」会误报）
_SYNONYM = {
    "逾期": ("超过有效期", "超期", "有效期届满", "期满"),
    "过期": ("超过有效期", "超期", "有效期届满", "期满"),
    "延长": ("延期", "延长期限"),
    "延期": ("延长", "延长期限"),
    "换证": ("期满换证", "换领"),
    "审验": ("审验教育",),
    "满分": ("满 12 分", "记满12分", "满12分"),
}


def entity_signals(question: str, answer: str) -> List[str]:
    """问句里的明确实体，答案里没落到，返回提示列表。纯正则，零成本。"""
    if not question or not answer:
        return []
    hits = []

    for code in sorted(set(_CODE_RE.findall(question))):
        if code not in answer:
            hits.append(f"问句提到 {code} 车型，答案未涉及")

    for age in sorted(set(_AGE_RE.findall(question))):
        if age not in answer:
            hits.append(f"问句提到 {age} 周岁，答案未涉及")

    for kw in _SYSTEM_WORDS:
        if kw in question and kw not in answer:
            if any(s in answer for s in _SYNONYM.get(kw, ())):
                continue
            hits.append(f"问句提到「{kw}」，答案未涉及")
    return hits


# ── 复核提示词 ──────────────────────────────────────────
# 这段是实测出来的，不是随手写的：宽松版只抓 33%，要件核查版抓 100%。
# few-shot 示例刻意不用线上事故的形态，避免"见过答案"冒充"会判断"。

_REVIEW_PROMPT = """你在核查一个交通法规问答系统是否"答错了对象"。

用户的问题：{q}

系统给出的回答：
{ans}

不要评价回答对不对、全不全、啰不啰嗦。只判断一件事：这个回答讲的是不是用户问的那件事。

方法：
第一步，找出回答针对的具体情形——它在讲哪种业务、哪类车型、哪个年龄段、哪个触发前提。
第二步，对照用户的问题，用户问的是不是同一个情形。
第三步，不是同一个情形，判 WRONG。

"答错对象"的典型形态：
- 用户问「B2 增驾要什么条件」，回答讲的是「A1 增驾条件」——车型不对
- 用户问「号牌丢了怎么补」，回答讲的是「行驶证丢了怎么补」——证件不对
- 用户问「60 岁能不能开大货」，回答讲的是「70 岁以上不得驾驶大货」——年龄段不对
- 用户问「记 6 分要不要审验」，回答讲的是「记满 12 分怎么处理」——触发条件不对

注意：
- 用户问句往往很简短，他没提到的细节不算"情形不符"，只核对他明确问的那件事
- 回答顺带提到别的制度、做了对比说明，只要主体答的是用户问的那件事，判 OK
- 回答比问题详细很多，不算错
- **用户只问一个期限（"几年""多久""多长时间"）时，只要答案给出的是这件事的期限，就判 OK**，
  不要求答案把用户没说出口的前提再复述一遍。例如「醉驾几年能考」答「5 年内不得申请驾驶证」，判 OK
- **用户用的是口语说法**（"本子""扣分""老司机"）时，只要答案答的是对应的那件事，判 OK
{extra}
只输出一行：
OK
或
WRONG: <一句话说明回答讲的什么情形、用户问的什么情形>
"""

_SIGNAL_HINT = """
另外请留意以下线索（可能有助于判断，但不构成直接结论）：
{hints}
"""

_ANSWER_OK = "OK"


def review(question: str, answer: str, client, model: str,
           timeout: float = 3.5, signals: Optional[List[str]] = None) -> Dict:
    """让模型判断事实层答案是否答错了对象。

    返回 {"verdict": OK/WRONG/SKIP, "reason": str, "sec": float}
    SKIP 表示没做成复核（超时/异常/未配置），调用方应当放行原答案。
    """
    res = {"verdict": "SKIP", "reason": "", "sec": 0.0}
    if not question or not answer or client is None:
        res["reason"] = "无可用模型客户端"
        return res

    extra = ""
    if signals:
        extra = _SIGNAL_HINT.format(hints="\n".join(f"- {h}" for h in signals[:4]))

    t0 = time.time()
    try:
        resp = client.with_options(timeout=max(1.0, timeout), max_retries=0).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是质检员，只输出 OK 或 WRONG: 原因。"},
                {"role": "user", "content": _REVIEW_PROMPT.format(q=question, ans=answer[:900], extra=extra)},
            ],
            temperature=0.0,
            max_tokens=90,
        )
        txt = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        res["sec"] = time.time() - t0
        res["reason"] = f"{type(e).__name__}: {e}"[:120]
        return res

    res["sec"] = time.time() - t0
    if txt.upper().startswith("WRONG"):
        m = re.search(r"WRONG\s*[:：]?\s*(.*)", txt, re.S)
        res["verdict"] = "WRONG"
        res["reason"] = (m.group(1).strip() if m else txt)[:160]
    else:
        res["verdict"] = "OK"
        res["reason"] = txt[:80]
    return res


def mode() -> str:
    """复核模式（环境变量 FACT_REVIEW）

    on      全量复核（默认）。每道事实层题都判一次，约多 1.4 秒，覆盖最全。
    signal  只在实体信号报警时复核。零成本信号把流量压到 2% 左右，
            代价是「实体都在、制度答串」那类错会被漏掉。
    off     关闭。离线评测省时间用。
    """
    v = (os.getenv("FACT_REVIEW", "on") or "on").strip().lower()
    if v in ("off", "0", "false", "no"):
        return "off"
    if v in ("signal", "signals", "risk"):
        return "signal"
    return "on"


def enabled() -> bool:
    """复核开关。默认开；设 FACT_REVIEW=off 关掉（离线评测可关，省时间）。"""
    return mode() != "off"
