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
import concurrent.futures
from typing import Dict, List, Optional

# 带上几轮历史：3 轮足够覆盖「那 B2 呢」「要多久」这类紧邻追问，
# 再多只是白占 token，也更可能把模型带偏到很久之前的话题。
MAX_TURNS = 3
# 历史里问句和答案的截断长度不同：
#   问句是「上一句在问什么」的唯一线索，而且规则层要从里面抽车型和动作，
#   截狠了 R1/R2 就抽不出东西，所以给到 200 字（小程序发的也是 200）。
#   答案只用来判断话题走向，60 字够；整段答案会让 prompt 翻倍，
#   token 越多首 token 延迟越大，而延迟正是超时的来源。
_MAX_USER_CHARS = 200
_MAX_ASST_CHARS = 60
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

# 主体词：不是"业务动作"，而是"这件事挂在什么东西上"。
# 为什么单独列：`_BIZ_WORDS` 收的是动作（换证/记分/年检），而「B2 考驾照要多久」
# 的动作是「考」，主体是「驾照」，两个表都不含「驾照」——
# 结果这句自带主体的完整问句被当成残句，白跑一次改写（还可能被改坏）。
# 8~10 字、自带主体的短问句在口语里很常见，必须能自己立住。
_SUBJECT_WORDS = (
    "驾照", "驾驶证", "行驶证", "登记证书", "机动车", "车辆", "车牌",
    "号牌", "科目一", "科目二", "科目三", "科目四", "车主",
)


def _has_subject(q: str) -> bool:
    """这句有没有自带主体（车型 / 业务动作 / 挂在什么证件车辆上）。"""
    return (bool(_CODE_RE.search(q or ""))
            or any(w in q for w in _BIZ_WORDS)
            or any(w in q for w in _SUBJECT_WORDS))


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
        cap = _MAX_USER_CHARS if role == "user" else _MAX_ASST_CHARS
        out.append({"role": role, "content": content[:cap]})
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
      · 极短（≤6 字）且不含主体（要多久 / 可以吗）

    反例（不改写）：
      「醉驾几年能考」7 字，含「醉驾」业务词 → 自己能立住
      「B2 考驾照要多久」8 字，含车型+「驾照」主体 → 自己能立住
      「C1 驾驶证过期了怎么换证」含车型+换证 → 完整
    """
    h = _norm_history(history)
    if not h:
        return False

    q = (question or "").strip()
    if not q:
        return False

    # 有主体（车型/业务动作/证件车辆）才算「自己立得住」
    has_subj = _has_subject(q)

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
    if core0.endswith("呢") and not (has_subj and len(core0) > 10):
        return True

    # 极短句：去掉标点后不足 7 个字，且没有任何业务词，基本只能是追问
    core = re.sub(r"[\s，。？！、,.?!~…]", "", q)
    if len(core) <= 6 and not has_subj:
        return True

    # 纯疑问尾巴（「大概要多久」这种前面只有语气词的）
    if len(core) <= 10 and not has_subj:
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
5. **补完必须还是一个问句，不能写成答案或名词短语。**
   例如上一句助手说「要考科目一」，最后一句「那要考科目几」，
   应补成「满分 12 分要考哪几个科目」，**不是**「科目一」；
   最后一句「那学多久」，应补成「满分学习要学多久」，**不是**「7 天」。
6. 判断不出用户在问什么，就原样输出最后一句
7. 只输出一句话，不要前缀、编号、引号

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

# 「可计时动作」：法规里真有期限、能被「多久」修饰的业务动作。
# 故意不收「开」「用」「有」这类泛动词——「C1 能开多久」不是成立的问题，
# 放进来就等于放行语义漂移。ctx_rules 复用同一份表，避免两处判据打架。
_DURABLE_ACTIONS = (
    "换证", "换领", "换发", "核发", "补证", "补办", "年检", "检验", "审验",
    "申领", "增驾", "满分", "学习", "记分", "记分周期", "实习期", "有效期",
    "过户", "转移", "注销", "报废", "恢复", "延期", "抵押", "解押",
    "变更登记", "变更备案", "事故", "考试", "考", "办理", "预约", "体检",
    "缴纳", "处理", "转籍", "迁入", "迁出",
)

# 法规语料里不成立的问法：驾驶证讲有效期、准驾范围讲车型，
# 没有「能开多久」这个概念。原句和历史都没这么说时，出现即是模型自造。
_NO_TIME_PHRASE = re.compile(r"(?:能|可以|可|会)开\s*多久")

# 判断「改写结果还是个问句」用。刻意放宽：只要出现任一疑问成分就算，
# 宁可放过一个不太好的改写，也不要误杀正常补全。
_Q_WORD = re.compile(
    r"(吗|呢|怎么|怎样|如何|什么|为啥|为什么|哪里|哪个|哪些|哪几|"
    r"几|多少|多久|多长时间|什么时候|"
    r"能否|能不能|可否|可以吗|是否|要不要|会不会|有没有|行不行|算不算|\?)")


def _intent(text: str) -> set:
    t = text or ""
    tags = set()
    if any(w in t for w in _TIME_WORDS):
        tags.add("time")
    if any(w in t for w in _MONEY_WORDS):
        tags.add("money")
    return tags


def _has_durable(text: str) -> bool:
    """句子里有没有真能计时的动作/对象。"""
    return any(w in (text or "") for w in _DURABLE_ACTIONS)


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

    # 改写结果必须还是个问句。实测「那要考科目几」被补成三个字「科目一」——
    # 没主语、没疑问词，前面四道形式校验全过，检索层拿「科目一」当关键词
    # 查出一段「科目一考什么内容」，答的完全是另一件事。
    # 判断很粗：只要含疑问词或问号就算问句，宁可放过不可错收。
    if not _Q_WORD.search(r):
        return False, "改写结果不是问句"

    # 不能把用户问的那件事换掉。补全只允许补对象，不允许换问题：
    # 原句问「要多久」，补出来必须还在问时间，不能变成「能开什么车」。
    lost = _intent(original) - _intent(r)
    if lost:
        return False, f"丢掉了原有的提问点 {sorted(lost)}"

    hist_text = " ".join(h["content"] for h in history)

    # 第五道半：时间问句必须锚定在可计时的动作上。
    # 实测「要多久」（上文是「C1 能开什么车」「那 B2 呢」）被补成
    # 「C1 小型汽车需要多久才能开」——还问时间、没有新增实体、仍是问句，
    # 前面五道全过，意思却从「考/增驾要多久」漂成了「能开多久」。
    # 后者在法规里不成立，照它检索只会捞回无关条文，而用户看不出错在哪。
    # 判据：既然还在问时间，结果里就必须有一个真能计时的对象。
    if "time" in _intent(original) and not _has_durable(original) \
            and not _has_durable(r):
        return False, "时间问句没锚定到可计时动作（疑似语义漂移）"

    # 「能开多久」句式零容忍：原句和历史都没这么说过，就是模型自造的。
    if _NO_TIME_PHRASE.search(r) and not _NO_TIME_PHRASE.search(original) \
            and not _NO_TIME_PHRASE.search(hist_text):
        return False, "用了「能开多久」这类法规里不成立的问法"

    # 数字不得新增
    allowed_digits = _digits(original) | _digits(hist_text)
    extra_digits = _digits(r) - allowed_digits
    if extra_digits:
        return False, f"新增数字 {sorted(extra_digits)}"

    # 中文数量词不得新增（阿拉伯数字已在上一步拦下）
    allowed_cn = _cn_quantities(original) | _cn_quantities(hist_text)
    extra_cn = _cn_quantities(r) - allowed_cn
    if extra_cn:
        return False, f"新增期限/数量 {sorted(extra_cn)}"

    # 车型码不得新增
    allowed_codes = _codes(original) | _codes(hist_text)
    extra_codes = _codes(r) - allowed_codes
    if extra_codes:
        return False, f"新增车型 {sorted(extra_codes)}"

    return True, ""


def enabled() -> bool:
    """开关：CTX_REWRITE=off 关掉（离线评测、排障用）。"""
    v = (os.getenv("CTX_REWRITE", "on") or "on").strip().lower()
    return v not in ("off", "0", "false", "no")


# 改写用的硬超时执行器。和 rag_engine._LLM_EXECUTOR 同理，单独一个池，
# 免得改写卡住的线程把生成侧的槽位占掉。
_RW_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8,
                                                 thread_name_prefix="ctxrw")


def _chat_hard_timeout(client, model: str, prompt: str, timeout: float) -> str:
    """带墙钟硬超时的改写调用。

    ⚠️ openai SDK 的 `timeout` 参数是 httpx 的 read timeout，语义是
    「两次数据块之间的最大间隔」，不是整次请求的时长。服务端缓慢但持续
    吐字时，设 3 秒可以跑成 6 秒——改写这一环原本就栽在这里：看起来设了
    3 秒上限，实际偶发 5~6 秒，正好吃掉本该留给生成的时间。
    只有 `future.result(timeout=)` 才是真的时间上限。
    """
    fut = _RW_POOL.submit(
        client.with_options(timeout=max(0.8, timeout), max_retries=0
                            ).chat.completions.create,
        model=model,
        messages=[
            {"role": "system", "content": "你只负责补全追问，输出一句话，不做任何解释。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=60,
    )
    try:
        resp = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"改写超过 {timeout:.1f}s")
    return (resp.choices[0].message.content or "").strip()


def resolve(question: str, history: Optional[List[Dict]], client, model: str,
            timeout: float = 4.0) -> Dict:
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
        txt = _chat_hard_timeout(client, model, _build_prompt(question, h), timeout)
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
