# -*- coding: utf-8 -*-
"""追问的规则层补全（不调模型）

为什么要有这一层
----------------
context_rewrite 用模型补全追问，效果不错但有 10%~20% 的调用会超时
（3 秒硬超时，glm-4-flash 尾延迟摆在那里）。超时那一轮退回单轮，
用户的体感就是「它又记不住上一句了」。

可这一批追问里，绝大多数形态是规整的：

    「C1 能开什么车」→「那 B2 呢」
    「C1 换证怎么弄」→「要多久」

前者只是换主语，后者只是换谓词，**用不着模型**。字符串替换就能补，
毫秒级、不可能超时，而且补出来的实体全部来自用户原话，不可能幻觉。

所以改成三级：
    L0 规则补全（本模块，~0ms）→ L1 模型补全（3s 上限）→ L2 澄清/单轮
模型只在 L0 认不出形态时才出场，超时率按「需要模型的样本」算，不再
按「需要改写的样本」算。

三条规则
--------
R1 换主语，继承谓词
    「C1 能开什么车」→「那 B2 呢」   ⇒  「B2 能开什么车」
    前提：本句去掉「那/呢/吗/？」之后只剩下一个实体，没有自己的疑问点。
    实现就是 str.replace，把上句的主语字面换成本句的实体。

R2 换谓词，继承主语
    「C1 换证怎么弄」→「要多久」     ⇒  「C1 换证要多久」
    前提：上句里有**恰好一个**可计时动作（换证/年检/记分/考试…）且车型不超过一个。
    多于一个或一个都没有，就说明这句「要多久」指哪件事说不准——见 R3。

R3 判断不了就报歧义，不猜
    「C1 能开什么车」→「那 B2 呢」→「要多久」
    「要多久」到底问「考 B2 驾照要多久」「增驾 B2 要多久」还是「B2 实习期多久」，
    人也要再问一句。系统猜一个再答，猜错了用户还看不出来；
    所以这里输出 candidates，让前端列成按钮，用户点一下就行。
    这比「沉默地退回单轮」强：用户至少知道系统卡在哪，一步就能选对。

返回值约定
----------
    {"status": "resolved",  "question": "...", "rule": "R1", "reason": ""}
    {"status": "ambiguous", "candidates": [{"q": "...", "label": "..."}], "reason": "..."}
    {"status": "skip",      "reason": "..."}    # 形态复杂，交给模型
"""

import re
from typing import Dict, List

from context_rewrite import (
    _BIZ_WORDS,
    _CN_NUM_RE,
    _CODE_RE,
    _DURABLE_ACTIONS,
    _Q_WORD,
    _REF_HEAD,
    _norm_history,
    validate,
)

# 去掉标点与空白，只留字面
_PUNCT_RE = re.compile(r"[\s，。？！、,.?!~…「」\"'“”（）()]")

_TAIL_PARTICLES = ("呢", "吗", "啊", "呀", "吧", "嘛")

# 「可计时动作」表定义在 context_rewrite（校验层也要用同一份），
# 这里只做「长词优先、去重叠」的本地封法：句子命中「考试」时不再重复算「考」。

# 追问尾巴里表示「问时间 / 问钱」的说法
_TIME_WORDS = ("多久", "多长时间", "多少天", "几年", "几天", "几个月",
               "多少年", "多少时间", "多少日子")
_MONEY_WORDS = ("多少钱", "多少费用", "费用多少", "花多少钱", "多少花费")

# 尾巴前面常见的情态词，有它就不再补「要」
_MODAL_HEADS = ("要", "需要", "需", "得", "大概", "一般", "通常", "大约")

# 「实体」不能带这些成分。实体的作用是被搬来搬去，一旦带上动作或情态，
# 搬过去就把原来的谓语顶掉了：
#   「记满 12 分怎么办」+「那要考科目一吗」→ 若不拦，会补成
#   「要考科目一12分怎么办」，整句报废。
_ENTITY_STOP = ("要", "能", "会", "可以", "该", "想", "需要", "是否", "怎么",
                "为什么", "咋", "应该", "必须", "有")


def _bare(text: str) -> str:
    return _PUNCT_RE.sub("", text or "")


def _strip_head(t: str) -> str:
    for w in sorted(_REF_HEAD, key=len, reverse=True):
        if t.startswith(w) and len(t) > len(w):
            return t[len(w):]
    return t


def _strip_tail(t: str) -> str:
    while len(t) > 1 and t[-1] in _TAIL_PARTICLES:
        t = t[:-1]
    return t


def _codes(text: str) -> List[str]:
    return _CODE_RE.findall(text or "")


def _bizs(text: str) -> List[str]:
    return [w for w in _BIZ_WORDS if w in (text or "")]


# 泛动词：句子里同时出现具体动作时，它们不单独构成「在给哪件事计时」
_GENERIC_VERBS = ("办理", "处理", "操作", "弄")


def _durable(text: str) -> List[str]:
    """句子里的可计时动作，长词优先、去重叠（「考试」命中后不再算「考」）。

    泛动词顺便降级：句子同时有「换证」和「办理」时，只有「换证」算数——
    否则「换证怎么办」会被数成两个动作，白白判成歧义。
    """
    t = text or ""
    hits = sorted([w for w in _DURABLE_ACTIONS if w in t], key=len, reverse=True)
    picked: List[str] = []
    for w in hits:
        if any(w in p for p in picked):
            continue
        picked.append(w)
    if len(picked) > 1:
        specific = [w for w in picked if w not in _GENERIC_VERBS]
        if specific:
            picked = specific
    return picked


def _entity_of(q: str) -> str:
    """本句是否只是「一个实体」。

    是 → 返回该实体（「那 B2 呢」→「B2」），语义上等于「B2 在这件事上怎么样」。
    否 → 返回空串，说明这句有自己的疑问点，不能靠替换主语补。
    """
    t = _strip_tail(_strip_head(_bare(q)))
    if not t or len(t) > 8:
        return ""
    # 自带疑问成分就不能当实体（「记几分」「能开什么车」）
    if _Q_WORD.search(t):
        return ""
    # 带动作/情态也不能当实体：「要考科目一」搬过去会把上句谓语顶掉
    if any(w in t for w in _ENTITY_STOP):
        return ""
    if not (_codes(t) or _bizs(t)):
        return ""
    return t


def _subject_token(sentence: str) -> str:
    """上句里可以被替换掉的主语：车型码优先，其次最靠前的业务词。"""
    cs = _codes(sentence)
    if cs:
        return cs[0]
    bs = _bizs(sentence)
    if not bs:
        return ""
    bs.sort(key=lambda w: (sentence or "").find(w))
    return bs[0]


# 纯话题短语：「新车」「夫妻之间」「外地」。既不是疑问、也不含动作，
# 人说出来通常是给上句加个限定——「年检多久一次」+「那新车呢」
# 意思是「新车 年检多久一次」，不是把「年检」顶掉。
_TOPIC_RE = re.compile(r"^[\u4e00-\u9fa5]{2,6}$")

# 看着像话题、其实是车型的另一种说法。这类不能靠追加/替换补
# （会得到「摩托车 C1 能开什么车」），交回模型。
_VEHICLE_WORDS = ("摩托车", "轿车", "货车", "客车", "挂车", "三轮车",
                  "电动车", "小车", "大车", "汽车", "私家车", "营运车")


def _topic_of(q: str) -> str:
    """本句是否只是一个中性话题短语（无动作、无疑问、无数字、非车型别称）。"""
    t = _strip_tail(_strip_head(_bare(q)))
    if not _TOPIC_RE.match(t):
        return ""
    if _Q_WORD.search(t) or any(w in t for w in _ENTITY_STOP):
        return ""
    if _codes(t) or _bizs(t) or any(w in t for w in _VEHICLE_WORDS):
        return ""
    # 带数量的是条件不是话题：「那超过两年呢」追加到上句会得到
    # 「超过两年 那要多久」这种拼接垃圾。这类交给模型去理解。
    if _CN_NUM_RE.search(t):
        return ""
    return t


def _usable_ctx(text: str) -> str:
    """上句够不够格当上下文。

    只有一个完整问句才能拿来当「话题承接对象」。残句（「那要多久」）本身
    还等着被补全，用它去拼下一句只会生产拼接垃圾——实测把
    「那超过两年呢」补成了「超过两年 那要多久」，两级残句焊在一起。
    """
    p = (text or "").strip()
    if len(p) < 4:
        return ""
    if any(p.startswith(w) for w in _REF_HEAD):
        return ""
    if _bare(p).endswith("呢"):
        return ""
    if not _Q_WORD.search(p):
        return ""
    return p


def _try_r1(q: str, prev: str) -> str:
    """换主语：把上句的主语换成这句的实体。

    关键在**车型和业务词不能互相顶替**。二者是不同类的东西：
      「C1 能开什么车」→「那 B2 呢」  车型换车型，替换后仍通顺
      「年检要带什么」→「那 B2 呢」   业务词换成车型就崩了
                                       （「B2 要带什么材料」不通，
                                       人说的是「B2 的年检要带什么」）
    所以跨类时改成句首追加，同类时才做字面替换。
    """
    if not prev:
        return ""

    ent = _entity_of(q)
    if not ent:
        # 不是实体但可能是个限定话题，往句首追加
        topic = _topic_of(q)
        if topic and topic not in prev:
            return f"{topic} {prev}"
        return ""

    # 上句已经含这个实体，替换只会造成「B2 和 B2」
    if ent in prev:
        return ""
    subj = _subject_token(prev)
    if not subj or subj == ent:
        return ""

    ent_is_code = bool(_codes(ent))
    subj_is_code = bool(_codes(subj))

    if ent_is_code and not subj_is_code:
        # 车型 × 业务词：追加而不是顶替
        return f"{ent} {prev}"
    if not ent_is_code and subj_is_code:
        # 反向跨类（「C1 实习期……」「那醉驾呢」）替换后十有八九语义崩
        # （「醉驾实习期扣满 12 分怎么办」），这条交给模型判断更稳。
        return ""

    out = prev.replace(subj, ent, 1)
    if out == prev:
        return ""
    # 「饮酒驾驶怎么处罚」+「那醉驾呢」：字面替换得到「醉驾驾驶怎么处罚」，
    # 多蹭一个「驾」。实体本身以「驾」结尾、被替换词后面又紧跟「驾驶」时，
    # 把「X 驾驶」整体换掉更干净。
    if ent.endswith("驾") and f"{subj}驾驶" in prev:
        merged = prev.replace(f"{subj}驾驶", ent, 1)
        if merged != prev:
            return merged
    return out


def _try_r2b(q: str, ctx: str) -> str:
    """本句自带动作、只缺主语：「几年能考」「多久能学完」。

    和 R2 正好相反——R2 是「有主语没动作」，这里是「有动作没主语」。
    典型场景：「饮酒驾驶怎么处罚」→「那醉驾呢」→「几年能考」。
    最后一句里「考」已经说明了要问什么，缺的只是"考什么"，
    把上句的实体补到句首就是完整问句（「醉驾几年能考」），
    这种不该算歧义——动作在句子里，指向是唯一的。
    """
    t = _strip_tail(_strip_head(_bare(q)))
    if not t or len(t) > 10:
        return ""
    if not _tail_kind(t):
        return ""
    if _codes(t) or _bizs(t):
        return ""                     # 自带主体，不用补
    if len(_durable(t)) != 1:
        return ""                     # 没动作（归 R2）或多个动作（归歧义）
    subj = _subject_token(ctx)
    if not subj:
        return ""
    return f"{subj}{t}"


def _tail_kind(t: str) -> str:
    if any(w in t for w in _TIME_WORDS):
        return "time"
    if any(w in t for w in _MONEY_WORDS):
        return "money"
    return ""


def _try_r2(q: str, ctx: str) -> str:
    """换谓词：这句只有疑问尾巴，把上句的主语+动作搬过来。"""
    t = _strip_tail(_strip_head(_bare(q)))
    if not t or len(t) > 8:
        return ""
    kind = _tail_kind(t)
    if not kind:
        return ""
    # 尾巴自带主体就不是纯尾巴（「C1 要多久」自己能立住）
    if _codes(t) or _bizs(t):
        return ""
    acts = _durable(ctx)
    if len(acts) != 1:
        # 0 个：不知道该给哪个动作计时；≥2 个：也不知道。
        # 两种情况都交给 R3 出候选，这里不猜。
        #
        # 试过从上一轮**答案**里补动作（「过期了怎么办」的问句没有动作，
        # 答案里有「换证」），但答案里通常同时有好几个（换证/体检/办理），
        # 取「最靠前」就成了猜——猜错的代价是直接答错，而退回候选只是多一次点击。
        return ""
    cs = _codes(ctx)
    if len(set(cs)) > 1:
        return ""
    code = cs[0] if cs else ""
    head = f"{code} {acts[0]}".strip()
    if t.startswith(_MODAL_HEADS):
        return f"{head}{t}"
    return f"{head}要{t}"


# 候选排序用的上下文线索：用户这句「要多久」到底在给哪件事计时，
# 只能从上一轮的话题猜。猜错了用户点到别的按钮，等于白问一次。
_RENEW_HINT = ("换证", "过期", "到期", "期满", "失效", "有效期", "逾期")
_SCOPE_HINT = ("能开什么车", "准驾", "开什么车", "什么车型", "增驾")


def build_candidates(q: str, history: List[Dict]) -> List[Dict]:
    """上下文不足以唯一确定时，列出「最可能是哪几个问题」。

    候选只用上下文里真实出现过的车型，动作取法规语境里最常见的计时对象，
    每个候选都能独立拿去问（不依赖历史），点一下就有答案。
    顺序按上轮话题调整：问的是「换证」就把换证排前面，问的是「能开什么车」
    就把考驾照/增驾排前面。
    """
    t = _strip_tail(_strip_head(_bare(q)))
    kind = _tail_kind(t)
    if not kind:
        return []

    codes: List[str] = []
    acts: List[str] = []
    ctx_parts: List[str] = []
    for item in reversed(history):
        content = item.get("content", "")
        if item.get("role") == "user":
            ctx_parts.append(content)
        for c in _codes(content):
            if c not in codes:
                codes.append(c)
        for a in _durable(content):
            if a not in acts:
                acts.append(a)
    ctx_text = " ".join(ctx_parts)

    out: List[Dict] = []
    seen = set()

    def add(qq: str, label: str):
        qq = qq.strip()
        if qq and qq not in seen and qq != q.strip():
            seen.add(qq)
            out.append({"q": qq, "label": label})

    main = codes[0] if codes else ""

    if kind == "time":
        items = []
        if main:
            # 只放事实层真答得出来的问法。「考 {C} 要多久」看着最像，
            # 但法规对初次申领的办理时限没有规定，事实层是空的——
            # 点了等于白点。用户想说的「考 B2 要多久」，法规口径就是「增驾」。
            items = [
                (f"{main} 换证要多久", f"{main} 换证要多久"),
                (f"增驾 {main} 要多久", f"增驾 {main} 要多久"),
                (f"{main} 实习期多久", f"{main} 实习期多久"),
                (f"{main} 年检多久一次", f"{main} 年检多久一次"),
            ]
        elif acts:
            items = [(f"{acts[0]}要多久", f"{acts[0]}要多久")]

        # 按上轮话题把最贴的那个顶到第一：聊「能开什么车」多半想问增驾，
        # 聊「过期了」多半想问换证。
        scope_first = any(w in ctx_text for w in _SCOPE_HINT)
        renew_first = any(w in ctx_text for w in _RENEW_HINT)

        def _rank(item):
            q0 = item[0]
            if scope_first and "增驾" in q0:
                return 0
            if renew_first and "换证" in q0:
                return 0
            return 1

        items.sort(key=_rank)
        for qq, lb in items:
            add(qq, lb)
        if not codes:
            add("驾驶证期满换证要多久", "驾驶证期满换证要多久")
            add("记分周期多久清零", "记分周期多久清零")
    else:
        for c in codes[:2]:
            add(f"{c} 换证要多少钱", f"{c} 换证多少钱")
            add(f"{c} 补考要多少钱", f"{c} 补考多少钱")
        if not codes:
            add("驾驶证期满换证要多少钱", "期满换证多少钱")
            add("科目一补考要多少钱", "科目一补考多少钱")

    return out[:3]


def rule_resolve(question: str, history) -> Dict:
    """规则层入口。命中则完全不需要模型。"""
    res = {"status": "skip", "question": "", "rule": "", "reason": "",
           "candidates": []}

    h = _norm_history(history)
    if not h:
        res["reason"] = "无历史"
        return res

    users = [x["content"] for x in h if x["role"] == "user"]
    if not users:
        res["reason"] = "历史里没有用户问句"
        return res

    last = users[-1]

    # 当前话题怎么定：
    #   1) 往前找最近一条「能当上下文用」的完整问句（跳过「那要多久」这类残句）
    #   2) 上一条本身若是残句，用这条去展开它（「那醉驾呢」→「醉驾怎么处罚」）
    #   3) 都没有就用上一条自己
    prev = ""
    for j in range(len(users) - 2, -1, -1):
        prev = _usable_ctx(users[j])
        if prev:
            break
    ctx = _try_r1(last, prev) if prev else ""
    if not ctx:
        ctx = _usable_ctx(last) or prev

    # R2b 先判：本句自带动作、只缺主语（「几年能考」→「醉驾几年能考」）
    out2b = _try_r2b(question, ctx)
    if out2b:
        ok, why = validate(question, out2b, h)
        if ok:
            res.update(status="resolved", question=out2b, rule="R2b")
            return res
        res["reason"] = f"R2b 产物未通过校验：{why}"

    # R2 再判：只有尾巴、没有实体的句子，走替换谓词
    out2 = _try_r2(question, ctx)
    if out2:
        ok, why = validate(question, out2, h)
        if ok:
            res.update(status="resolved", question=out2, rule="R2")
            return res
        # 规则产物被校验拒收说明这条规则不适用这句，交给模型而不是直接判歧义
        res["reason"] = f"R2 产物未通过校验：{why}"

    # R1：只有实体、没有疑问点的句子，走替换主语
    out1 = _try_r1(question, ctx)
    if out1:
        ok, why = validate(question, out1, h)
        if ok:
            res.update(status="resolved", question=out1, rule="R1")
            return res
        res["reason"] = f"R1 产物未通过校验：{why}"

    # R3：这句在问时间/钱，但上下文凑不出唯一的对象 —— 报歧义，不猜。
    # 只对 time/money 这么做：这两类追问的候选问法可以枚举，用户点一下就行；
    # 是非问（「可以吗」）的候选无法枚举，交给模型或单轮。
    bare = _strip_tail(_strip_head(_bare(question)))
    if len(bare) <= 10 and _tail_kind(bare):
        cands = build_candidates(question, h)
        if len(cands) >= 2:
            res.update(status="ambiguous", candidates=cands,
                       reason=f"「{question}」在上下文里有多个可能的问法")
            return res

    res["reason"] = res["reason"] or "规则认不出形态"
    return res
