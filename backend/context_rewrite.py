# -*- coding: utf-8 -*-
"""多轮追问改写（指代消解）

问题
----
`/api/query` 原本只收单句，小程序也只发当前这一句。用户连续追问时——

    用户：C1 能开什么车
    用户：那 B2 呢
    用户：要多久

第二句、第三句到后端时是孤立的。「那 B2 呢」不含任何业务词，「要多久」连主语
都没有。这两句在任何一层都过不去：

  · 事实层 `fact_table.lookup("要多久")` 匹配不到处理器（没有车型、没有业务词）
  · 检索层把「要多久」当向量去查，跟任何法条都不像，召回的全是噪音

结果是用户觉得「它记不住上一句」——实际是**这句话本身就不完整**，
跟记性无关。

为什么不能只把历史拼进提示词
--------------------------
生成层拿到历史确实能接着聊，但前两层照样失灵：检索已经偏了，模型只能在
偏掉的片段上发挥，容易把「答非所问」包装得很有把握。
所以正确顺序是**先补全问句，再走原来的四层**——补全后若能命中事实层，
确定性查表的优势一点不丢。

为什么不是每句都改写
------------------
改写要多一次模型调用（实测 0.4~1.5 秒）。事实层中位 0.8 秒，加上去 2 秒出头，
预算够；但走 RAG 的那条路本来就要 6~9 秒，再加就可能贴近 12 秒。
更重要的是：**改写本身有风险**，模型可能把一句完整的问句改坏。
所以只在「确实看不出来在问什么」时才动手，其余零成本放行（need_resolve）。

防幻觉的三道校验（validate）
--------------------------
改写是唯一一个「用模型去改写用户原话」的环节，必须防它自作主张：

  1. 数字不能新增——原句和历史里都没有的阿拉伯数字，一律拒收。
     否则「要多久」可能被补成「醉驾 5 年后能考」，5 这个数字是模型编的。
  2. 车型码不能新增——同上，避免把 C1 的上下文套成 A1。
  3. 长度、格式、拒答措辞——超过 60 字、多行、带「抱歉/无法」的一律拒收。

任何一项不过，退回原句。拒收的代价是这一轮仍是单轮效果，而误收的代价是
用一句被改错的话去查表——后者更糟。

失败策略
--------
同 fact_review：改写是加分项。超时、网络错、解析失败一律返回原句，
`resolved=False`，主流程照常走。
"""

import os
import re
import time
from typing import Dict, List, Optional

# 带上几轮历史：3 轮足够覆盖「那 B2 呢」「要多久」这类紧邻追问，
# 再多只是白占 token，也更可能把模型带偏到很久之前的话题。
MAX_TURNS = 3
# 历史里答案只留开头：追问靠的是「上一句在讲什么」，不是完整答案。
MAX_HIST_CHARS = 120
# 补全后的问句不该比原句长太多，超过这个长度基本是模型在自行作答
MAX_RESOLVED_CHARS = 60

_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(A1|A2|A3|B1|B2|C1|C2|C3|C4|C5|C6|D|E|F|M|N|P)(?![A-Za-z])")
_DIGIT_RE = re.compile(r"\d+")

# 业务词：自带主体，说明这句话自己能立住
_BIZ_WORDS = (
    "换证", "补证", "补办", "年检", "检验", "免检", "报废", "注销", "吊销", "暂扣",
    "记分", "扣分", "审验", "实习期", "增驾", "申领", "考试", "科目", "过户", "转让",
    "抵押", "解押", "迁入", "迁出", "转籍", "改装", "变更登记", "变更备案", "保险",
    "责任", "逃逸", "醉驾", "饮酒", "超速", "超员", "闯红灯", "记满", "换领", "延期",
    "恢复", "禁驾", "学法减分", "号牌", "行驶证", "登记证书", "准驾", "车型",
)

# 指代/承接词：出现在句首，说明这句挂在上一句上
_REF_HEAD = ("那", "这", "它", "那么", "还有", "另外", "顺便", "上面", "刚才", "前面", "同样")

# 只有疑问尾巴、没有主体的说法
_VAGUE_TAILS = (
    "多久", "多长时间", "多少钱", "几年", "几天", "几个月", "多少天",
    "可以吗", "行不行", "行吗", "能吗", "要不要", "怎么办", "怎么弄", "咋办",
)


def _norm_history(history: Optional[List[Dict]]) -> List[Dict]:
    """清洗历史：只留 role/content，剥空白，答案截断，最多 MAX_TURNS 轮。"""
    if not history:
        return []
    out: List[Dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        out.append({"role": role, "content": content[:MAX_HIST_CHARS]})
    # 只有 assistant 没有 user 的历史没有承接对象（比如历史被裁掉了问句），
    # 拿它去改写只会让模型对着一段自说自话乱猜。
    if not any(x["role"] == "user" for x in out):
        return []
    # 一轮 = 一问一答，保留最后 MAX_TURNS*2 条
    return out[-(MAX_TURNS * 2):]


def need_resolve(question: str, history: Optional[List[Dict]]) -> bool:
    """这句要不要结合历史补全。零成本、纯正则。

    只在「自己立不住」时才返回 True。判据刻意保守：宁可漏补一轮，
    也不要把完整的问句交给模型去改写。

    三种情况算立不住：
      · 句首有指代/承接词（那 B2 呢 / 还有呢 / 那要多久）
      · 以「呢」收尾（B2 呢）
      · 极短（≤6 字）且不含业务词（要多久 / 可以吗）

    反例（不改写）：
      「醉驾几年能考」7 字，含「醉驾」业务词 → 自己能立住
      「C1 驾驶证过期了怎么换证」含车型+换证 → 完整
    """
    h = _norm_history(history)
    if not h:
        return False

    q = (question or "").strip()
    if not q:
        return False

    has_biz = any(w in q for w in _BIZ_WORDS)

    # 句首指代本身就是承接上文的强信号。这时**不再要求句子里没有业务词**——
    # 实测「那醉驾呢」「那超速 50% 以上呢」都含业务词，但业务词只交代了话题，
    # 没说清是话题的哪一面（怎么罚？多久？几年？），必须靠上一句才能定。
    # 判据看句尾形态：以「呢/吗」收尾，或以「怎么办/多久」这类疑问尾巴收尾，
    # 或短到只剩几个字。
    core0 = re.sub(r"[\s，。？！、,.?!~…]", "", q)
    if any(q.startswith(w) for w in _REF_HEAD):
        if (core0.endswith("呢") or core0.endswith("吗")
                or any(core0.endswith(t) for t in _VAGUE_TAILS)
                or len(core0) <= 6):
            return True

    # 「呢」在口语里几乎只用于追问（「B2 呢」「醉驾呢」），完整问句很少这样收尾。
    # 例外是带完整业务主体的长句，如「驾驶员体检要带什么呢」——主体明确，不必补。
    if core0.endswith("呢") and not (has_biz and len(core0) > 10):
        return True

    # 极短句：去掉标点后不足 7 个字，且没有任何业务词，基本只能是追问
    core = re.sub(r"[\s，。？！、,.?!~…]", "", q)
    if len(core) <= 6 and not has_biz:
        return True

    # 纯疑问尾巴（「大概要多久」这种前面只有语气词的）
    if len(core) <= 10 and not has_biz:
        for tail in _VAGUE_TAILS:
            if tail in core:
                return True

    return False


_PROMPT = """下面是一段对话。用户最后一句是追问，单独看不知道在问什么。
请把它补成一句能独立看懂的问题，让不知道上下文的人也明白。

对话记录：
{history}

要求：
1. **必须去掉「那」「这」「呢」这类指代词**，把具体说的是什么东西补进去。
   只补对象和条件，不回答、不解释
2. 不得添加用户没说过的数字、年龄、年份、车型代码
3. 保持用户原说法，口语就保持口语，别改成法条腔
4. 最后一句换问别的事情时，按它自己的问法补，不要把上一句的问法套过来。
   例如上一句问「能上高速吗」，最后一句「那扣满 12 分呢」，应补成
   「实习期扣满 12 分会怎样」，而不是「扣满 12 分能上高速吗」
5. 判断不出用户在问什么，就原样输出最后一句
6. 只输出一句话，不要前缀、编号、引号

补全后："""


def _build_prompt(question: str, history: List[Dict]) -> str:
    lines = []
    for item in history:
        who = "用户" if item["role"] == "user" else "助手"
        lines.append(f"{who}：{item['content']}")
    lines.append(f"用户：{question}")
    return _PROMPT.format(history="\n".join(lines))


# 用户这句在问什么"面"。改写只允许把对象补清楚，不允许把问题换掉——
# 实测「那 B2 呢」之后再问「要多久」，模型会补成「C1 能开什么车？」，
# 把"问期限"整个换成了"问准驾范围"，答案自然驴唇不对马嘴。
_TIME_WORDS = ("多久", "多长时间", "多少天", "几个月", "几年", "几天", "多少年", "什么时候")
_MONEY_WORDS = ("多少钱", "多少费用", "费用", "收费", "花多少")


def _intent(text: str) -> set:
    t = text or ""
    tags = set()
    if any(w in t for w in _TIME_WORDS):
        tags.add("time")
    if any(w in t for w in _MONEY_WORDS):
        tags.add("money")
    return tags


def _digits(text: str) -> set:
    """阿拉伯数字集合。先剥掉车型码——B2 里的 2 是代号不是数量，
    否则「那 B2 呢」补全成「B2 能开什么车」会被误判为"新增数字 2"。"""
    return set(_DIGIT_RE.findall(_CODE_RE.sub(" ", text or "")))


# 中文数量词：「五年」「三个月」「两千元」。不校验的话，模型把「要多久」补成
# 「醉驾五年后能考」这种凭空加期限的改写给不出来——阿拉伯数字能拦，中文数字拦不住。
# 只匹配「数字+单位」，所以「一样」「一定」「一直」这类不会被误伤。
_CN_NUM_RE = re.compile(r"[一二三四五六七八九十两半百千]+\s*(?:年|个月|天|日|小时|分钟|岁|周岁|元|块|分)")


def _cn_quantities(text: str) -> set:
    return set(re.sub(r"\s+", "", m) for m in _CN_NUM_RE.findall(text or ""))


def _codes(text: str) -> set:
    return set(_CODE_RE.findall(text or ""))


def validate(original: str, resolved: str, history: List[Dict]) -> (bool, str):
    """校验改写结果。返回 (是否接受, 拒收原因)。

    拒收比误收安全：拒收只是这一轮退回单轮效果，误收等于用一句被改坏的话
    去查表，可能命中错误的处理器。
    """
    r = (resolved or "").strip()
    if not r:
        return False, "空输出"
    if r == original.strip():
        return False, "与原句相同"

    # 模型偶尔会输出多行，取第一行（不要因为换行就整条丢弃）
    r = r.splitlines()[0].strip()
    # 去掉模型自带的「补全后：」「问题：」之类前缀
    r = re.sub(r"^(补全后|问题|追问|完整问题)\s*[:：]\s*", "", r).strip("「」\"' “”")

    if not r:
        return False, "去前缀后为空"
    if len(r) > MAX_RESOLVED_CHARS:
        return False, f"过长（{len(r)} 字）"
    if re.search(r"抱歉|无法|作为\s*AI|不确定|无法判断|无法确定", r):
        return False, "含拒答措辞"

    # 「补全」的最低形式要求：不再依赖上下文。
    # 实测模型有时只把「呢」换成「怎么办」交差——「那超过两年呢」→「那超过两年怎么办」，
    # 主语「驾驶证」根本没补进去，检索照样跑到机动车年检去。
    # 这种半成品比不补更糟：看起来补过了，实际还是残句，还白花一次调用。
    if any(r.startswith(w) for w in _REF_HEAD):
        return False, "仍以指代词开头"
    if r.endswith("呢"):
        return False, "仍以「呢」结尾"

    # 不能把用户问的那件事换掉。补全只允许补对象，不允许换问题：
    # 原句问「要多久」，补出来必须还在问时间，不能变成「能开什么车」。
    lost = _intent(original) - _intent(r)
    if lost:
        return False, f"丢掉了原有的提问点 {sorted(lost)}"

    # 数字不得新增
    allowed_digits = _digits(original) | _digits(" ".join(h["content"] for h in history))
    extra_digits = _digits(r) - allowed_digits
    if extra_digits:
        return False, f"新增数字 {sorted(extra_digits)}"

    # 中文数量词不得新增（阿拉伯数字已在上一步拦下）
    allowed_cn = _cn_quantities(original) | _cn_quantities(" ".join(h["content"] for h in history))
    extra_cn = _cn_quantities(r) - allowed_cn
    if extra_cn:
        return False, f"新增期限/数量 {sorted(extra_cn)}"

    # 车型码不得新增
    allowed_codes = _codes(original) | _codes(" ".join(h["content"] for h in history))
    extra_codes = _codes(r) - allowed_codes
    if extra_codes:
        return False, f"新增车型 {sorted(extra_codes)}"

    return True, ""


def enabled() -> bool:
    """开关：CTX_REWRITE=off 关掉（离线评测、排障用）。"""
    v = (os.getenv("CTX_REWRITE", "on") or "on").strip().lower()
    return v not in ("off", "0", "false", "no")


def resolve(question: str, history: Optional[List[Dict]], client, model: str,
            timeout: float = 3.0) -> Dict:
    """把追问补成独立问句。

    返回：
      {"question": 实际该用的问题, "resolved": bool, "reason": str,
       "sec": float, "skipped": bool}

    resolved=False 时 question 就是原句，调用方无需分支处理。
    """
    res = {"question": question, "resolved": False, "reason": "", "sec": 0.0, "skipped": False}

    if not enabled():
        res.update(reason="改写已关闭（CTX_REWRITE=off）", skipped=True)
        return res
    if client is None:
        res.update(reason="生成侧模型未配置", skipped=True)
        return res

    h = _norm_history(history)
    if not h:
        res.update(reason="无历史", skipped=True)
        return res

    if not need_resolve(question, h):
        res.update(reason="问句自身完整", skipped=True)
        return res

    t0 = time.time()
    try:
        resp = client.with_options(timeout=max(0.8, timeout), max_retries=0).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你只负责补全追问，输出一句话，不做任何解释。"},
                {"role": "user", "content": _build_prompt(question, h)},
            ],
            temperature=0.0,
            max_tokens=80,
        )
        txt = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        res["sec"] = time.time() - t0
        res["reason"] = f"{type(e).__name__}: {e}"[:100]
        return res

    res["sec"] = time.time() - t0
    ok, why = validate(question, txt, h)
    if not ok:
        res["reason"] = f"拒收：{why}"
        return res

    cleaned = txt.splitlines()[0].strip()
    cleaned = re.sub(r"^(补全后|问题|追问|完整问题)\s*[:：]\s*", "", cleaned).strip("「」\"' “”")
    res.update(question=cleaned, resolved=True, reason="已补全")
    return res
