# -*- coding: utf-8 -*-
"""法条引用对账：答案里出现的《法规》第X条，必须能在知识库里对上号。

【为什么需要这道闸门】
2026-11-04 线上真实错答案例：用户问「造成交通事故致人死亡，负同等以上责任如何处理」，
模型答「记分办法第八条记12分、第十四条不得申请」——三处引用**全部编造**。
这类错误调 prompt 压不住：小模型编一个"像样的条号"比承认"知识库没有"更省力。
唯一可靠的办法是拿真实法条清单逐条对账。

【三层判定】
  unknown_law  法规名不在知识库 → 可能是凭空造的法规，或引用已废止的旧法
  no_article   法规在库且全文收录，但该条号不存在 → 必属编造
  unverified   条号存在，但本次检索片段里没有 → 无法核实，保留字样但统一提示

【刻意不判的情况（避免误伤）】
  · 条号超出本库收录范围（民法典只收了侵权编 1208~1217，引 1179 是真条文）
  · 非「第X条」体例的国标/行标（GB 38900、GA 1029 用章节号）
  · 本库为摘编、条号不连续的法规（无法区分"编造"与"未收录"）

索引由 tools/build_citation_index.py 从 data/knowledge_base/*.md 生成，
随 backend/ 进镜像。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

_CN = "零〇一二三四五六七八九十百千"
_CN_VAL = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "kb_index", "citation_index.json")

# 《法规名》第X条（之X）
# 法规名与条号之间常夹一个文号括注，如「（公安部令第163号）」「（2021年修订）」
# 「（公交管〔2022〕295号）」。不认这个括注，整条引用就抽不出来——
# 而事实层所有记分题答案正是这个写法，等于引用对账对它们完全失效。
_LAW_TAIL_PAD = r"(?:\s*[（(][^）)]{0,40}[）)])?"

_STRONG_RE = re.compile(
    r"《\s*(?P<law>[^》]{2,80}?)\s*》" + _LAW_TAIL_PAD + r"\s*第\s*"
    r"(?P<num>[0-9]{1,5}|[零〇一二三四五六七八九十百千]{1,10})\s*条"
    r"(?:\s*之\s*(?P<sub>[零〇一二三四五六七八九十]{1,3}))?"
)

_ART_TAIL = (
    _LAW_TAIL_PAD +
    r"\s*第\s*(?P<num>[0-9]{1,5}|[零〇一二三四五六七八九十百千]{1,10})\s*条"
    r"(?:\s*之\s*(?P<sub>[零〇一二三四五六七八九十]{1,3}))?"
)

_D = "零一二三四五六七八九"


def cn2int(s: str) -> int:
    """中文数字转整数（与 document_loader.cn2int 同口径，此处独立实现避免循环依赖）"""
    total = cur = 0
    for ch in s:
        if ch in _CN_VAL:
            cur = _CN_VAL[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "千":
            total += (cur or 1) * 1000
            cur = 0
    return total + cur


def int2cn(n: int) -> str:
    """整数转中文（用于提示文案，最大支持到千位，条号够用）"""
    if n <= 0:
        return "零"
    s, started, pending_zero = "", False, False
    for base, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        d = n // base % 10
        if d == 0:
            if started:
                pending_zero = True
            continue
        if pending_zero:
            s += "零"
            pending_zero = False
        s += _D[d] + name
        started = True
    return s[1:] if s.startswith("一十") else s


def _norm_law(name: str) -> str:
    """法规名归一：去空白、去尾部括号注释、去「中华人民共和国」前缀"""
    t = re.sub(r"[\s\u3000]+", "", name or "")
    t = re.sub(r"[（(][^（）()]*[）)]\s*$", "", t)
    t = re.sub(r"^中华人民共和国", "", t)
    return t.strip("《》 　")


class CitationIndex:
    """法规名/条号索引。进程内单例，首次使用时加载。"""

    def __init__(self, path: str = INDEX_PATH):
        self.path = path
        self.docs: List[Dict] = []
        self.by_alias: Dict[str, Dict] = {}
        self.loaded = False
        self._weak_re: Optional[re.Pattern] = None

    def load(self) -> bool:
        if self.loaded:
            return True
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[CitationGuard] 索引加载失败（跳过引用对账）: {e}")
            return False

        self.docs = data.get("docs", [])
        for d in self.docs:
            arts = d.get("articles") or []
            d["_arts"] = set(arts)
            d["_subs"] = set(d.get("sub_articles") or [])
            d["_min"] = min(arts) if arts else 0
            d["_max"] = max(arts) if arts else 0
            # 全文连续收录才能把"区间内缺失"判为编造；摘编型法规做不到这个判断
            d["_complete"] = bool(arts) and sorted(arts) == list(range(d["_min"], d["_max"] + 1))
            for a in d.get("aliases") or []:
                k = _norm_law(a)
                if k and k not in self.by_alias:
                    self.by_alias[k] = d
            k = _norm_law(d.get("name", ""))
            if k:
                self.by_alias.setdefault(k, d)

        # 弱匹配（不带书名号，如「依据道路交通安全法第九十九条」）：
        # 别名按长度倒序，保证「道路交通安全法实施条例」优先于「道路交通安全法」
        alts = sorted(self.by_alias.keys(), key=len, reverse=True)
        if alts:
            body = "|".join(re.escape(a) for a in alts if len(a) >= 2)
            self._weak_re = re.compile(rf"(?P<law>{body}){_ART_TAIL}")
        self.loaded = True
        print(f"[CitationGuard] 索引就绪：{len(self.docs)} 部法规，"
              f"{len(self.by_alias)} 个别名")
        return True

    # ── 抽取 ──────────────────────────────────────────────
    def extract(self, answer: str) -> List[Dict]:
        """抽取答案中所有「法规 + 条号」引用，返回带判定结果的列表"""
        if not answer:
            return []
        if not self.loaded and not self.load():
            return []

        found: List[Dict] = []
        taken: List[Tuple[int, int]] = []

        def _num(g):
            raw = g("num") or ""
            n = int(raw) if raw.isdigit() else cn2int(raw)
            sub = ""
            if g("sub"):
                s = g("sub")
                sub = str(int(s) if s.isdigit() else cn2int(s))
            return n, sub

        for m in _STRONG_RE.finditer(answer):
            n, sub = _num(m.group)
            if n <= 0:
                continue
            found.append(self._judge(m.group("law"), n, sub, m.group(0), strong=True))
            taken.append((m.start(), m.end()))

        if self._weak_re:
            for m in self._weak_re.finditer(answer):
                if any(s <= m.start() < e for s, e in taken):
                    continue
                n, sub = _num(m.group)
                if n <= 0:
                    continue
                # 弱匹配只在法规名与条号紧邻时采信（中间不夹别的书名号）
                span = answer[m.start():m.start("law")]
                if "《" in span:
                    continue
                found.append(self._judge(m.group("law"), n, sub, m.group(0), strong=False))

        # 同一 (法规, 条号) 只留一条
        uniq: Dict[Tuple[str, int, str], Dict] = {}
        for c in found:
            key = (c["doc_key"], c["article"], c["sub"])
            if key not in uniq:
                uniq[key] = c
        return list(uniq.values())

    def _judge(self, law_raw: str, n: int, sub: str, raw: str, strong: bool) -> Dict:
        key = _norm_law(law_raw)
        doc = self.by_alias.get(key)
        base = {
            "raw": raw.strip(),
            "law_raw": law_raw.strip(),
            "law_key": key,
            "article": n,
            "sub": sub,
            "doc_key": doc["name"] if doc else key,
            "strong": strong,
        }
        if doc is None:
            # 只有「像法规名」的才判未知，避免把普通书名号内容（如书名）误伤
            looks_like_law = bool(re.search(r"(法|条例|规定|办法|解释|规范|标准|规则|细则|通知|批复)$", key)) \
                or len(key) >= 12
            base["status"] = "unknown_law" if looks_like_law else "ignored"
            base["doc"] = None
            return base

        base["doc"] = doc
        label = f"《{doc['name']}》第{int2cn(n)}条" if not sub else \
                f"《{doc['name']}》第{int2cn(n)}条之{int2cn(int(sub))}"

        if not doc.get("article_style", True):
            base["status"] = "chapter_style"   # 国标/行标体例，条号无从校验
            return base

        in_set = (f"{n}-{sub}" in doc["_subs"]) if sub else (n in doc["_arts"])
        if not in_set:
            if doc["_complete"] and doc["_min"] <= n <= doc["_max"]:
                base["status"] = "no_article"
                base["label"] = label
                return base
            # 全文法规（从第一条起连续收录）越界也属编造：道交法只有 124 条，引 125 条必假。
            # 摘编型法规（民法典只收侵权编 1208~1217）越界不算——引 1179 条是真条文。
            if doc["_complete"] and doc["_min"] == 1 and (n > doc["_max"] or n < 1):
                base["status"] = "no_article"
                base["label"] = label
                return base
            if sub and n in doc["_arts"] and not doc["_subs"]:
                # 该法规有条号但没有分条体例，引「第X条之Y」多半是把两款当成分条
                base["status"] = "no_article"
                base["label"] = label
                return base
            base["status"] = "out_of_scope"   # 超出本库收录范围，不判
            return base

        base["status"] = "exists"
        base["label"] = label
        return base


_INDEX = CitationIndex()


def index() -> CitationIndex:
    return _INDEX


def _cn_art(n: int, sub: str = "") -> str:
    return f"第{int2cn(n)}条" + (f"之{int2cn(int(sub))}" if sub else "")


def _in_context(doc: Dict, n: int, sub: str, context: str) -> bool:
    """该条号是否出现在本次检索片段中（两种写法都认）"""
    pats = [_cn_art(n, sub)]
    if n >= 10:
        pats.append(f"第{n}条" + (f"之{sub}" if sub else ""))
    return any(p in context for p in pats)


def check(answer: str, context: str) -> Dict:
    """对账入口。返回分组后的结果，供 RAGEngine 决定如何处置。"""
    idx = index()
    cites = idx.extract(answer)
    out = {
        "citations": cites,
        "unknown_law": [c for c in cites if c["status"] == "unknown_law"],
        "no_article": [c for c in cites if c["status"] == "no_article"],
        "unverified": [c for c in cites if c["status"] == "exists"
                       and not _in_context(c["doc"], c["article"], c["sub"], context or "")],
        "bad": [],       # 需要从答案中摘除的（编造类）
        "total": 0,
    }
    out["bad"] = out["unknown_law"] + out["no_article"]
    out["total"] = len([c for c in cites if c["status"] not in ("ignored", "chapter_style")])
    return out


def render_removal_note(bad: List[Dict]) -> str:
    """把被摘除的编造引用写成一句用户看得懂的校验说明"""
    if not bad:
        return ""
    items = []
    for c in bad[:4]:
        if c["status"] == "no_article":
            items.append(f"「{c['label']}」在现行法规中不存在")
        else:
            items.append(f"「《{c['law_raw']}》」不在本知识库收录范围")
    more = f"等 {len(bad)} 处" if len(bad) > 4 else ""
    return ("\n\n【系统校验】本回答已摘除{}引用：{}。"
            "被摘除部分的条款号无法与现行法规原文对应，请以下方来源列表中的原文为准，"
            "或咨询当地车管所、交管部门确认。").format(more, "；".join(items))


def render_unverified_note(unverified: List[Dict]) -> str:
    """条号真实存在、但未出现在本次检索片段中的引用，统一提示"""
    if not unverified:
        return ""
    names = []
    for c in unverified[:3]:
        names.append(c.get("label") or c["raw"])
    return ("\n\n【系统校验】{}未出现在本次检索到的原文片段中，"
            "条款号本身在现行法规中有效，具体表述请以来源列表原文为准。").format("、".join(names))
