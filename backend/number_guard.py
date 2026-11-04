# -*- coding: utf-8 -*-
"""答案数字核对：答案里出现的每个法规数字，必须是知识库原文里真实存在的数字。

【为什么要重做这一层】
原实现（rag_engine._check_numbers）只做一件事：拿「值」去**本次检索片段**里找，
找到就算有依据，而且必须「3 个以上数字且六成无据」才提示。两个结构性缺陷：

  1. 依据源是截断后的片段。片段被切到 900 字、顶多 5 条，原文里的数字常在
     截断之外，正确答案也查不到 → 只能放宽（"宁可漏报也不误报"），于是
     单个数字写错（如把 500 元写成 200 元）永远拦不住。
  2. 只比值，不比单位。库里出现「200」可能来自「200 米」，答案写「200 元」
     照样算有依据。金额、期限、记分这三个最要命的量纲完全没区分。

本模块把依据源换成**知识库全量原文**（构建期抽成倒排索引，随镜像走），
核对粒度换成「归一化数值 + 归一化量纲」，并把处置从"提不提示"升级为
"逐项点名 / 整体降级"。

【归一化口径】
  金额：万元/亿元 → 元
  期限：年 → 月（所以「3 年」与「36 个月」互认）；日/天 同义
  其余：工作日、小时、分钟、分（记分）、周岁、岁、次、人、倍、吨、千克、
        米、厘米、公里、%、度

【刻意不核对的东西】
  · 年份（2012 年、2020 年 9 月）：法规发布/施行年份，属叙事信息，核它只会误报
  · 裸数字（不带量纲）：多是「第一百二十三条」「第 2 项」这类序号
  · 上下文里的日期段（20xx年x月x日）：会污染「1 日」「9 月」这类量纲池

【判不了的情况（本层的天花板，写在这里避免以后误以为已全覆盖）】
  答案的数字在库里"别处也有"，但归属错了法条——纯值核对无法识别。
  例如库里某条写了「200 元」，答案却在另一条的场景下写「200 元」而原文是
  500 元。要抓这种，必须做「数字—条号—要件」三元绑定，那是语义判定，
  正则做不到，调 LLM 又会击穿 15s 预算。故本层只做 L3 弱提示，不拦截。
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "kb_index", "number_index.json")

_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL = {"十": 10, "百": 100, "千": 1000}

# ── 量纲表：(字面, 规范量纲, 换算倍率) ──
# 顺序即正则优先级：长字面必须在短字面前（万元 先于 元，个月 先于 月）
_UNIT_SPEC: List[Tuple[str, str, Decimal]] = [
    ("亿元", "money", Decimal(100000000)),
    ("万元", "money", Decimal(10000)),
    ("元",   "money", Decimal(1)),
    ("个工作日", "workday", Decimal(1)),
    ("工作日", "workday", Decimal(1)),
    ("个月", "month", Decimal(1)),
    ("月",   "month", Decimal(1)),
    ("周岁", "age", Decimal(1)),
    ("岁",   "age", Decimal(1)),
    ("小时", "hour", Decimal(1)),
    ("分钟", "minute", Decimal(1)),
    ("公里", "km", Decimal(1)),
    ("千米", "km", Decimal(1)),
    ("厘米", "cm", Decimal(1)),
    ("米",   "m", Decimal(1)),
    ("天",   "day", Decimal(1)),
    ("日",   "day", Decimal(1)),
    ("年",   "year", Decimal(1)),
    ("分",   "point", Decimal(1)),
    ("次",   "times", Decimal(1)),
    ("人",   "person", Decimal(1)),
    ("倍",   "fold", Decimal(1)),
    ("吨",   "ton", Decimal(1)),
    ("千克", "kg", Decimal(1)),
    ("克",   "gram", Decimal(1)),
    ("度",   "degree", Decimal(1)),
    ("%",    "pct", Decimal(1)),
    ("％",   "pct", Decimal(1)),
]

_UNIT_BY_LITERAL = {u: (k, f) for u, k, f in _UNIT_SPEC}
_UNIT_ALT = "|".join(re.escape(u) for u, _, _ in _UNIT_SPEC)

_NUM = r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两]+)"
_NUM_UNIT_RE = re.compile(rf"(?<![\d.])(?P<num>{_NUM})\s*(?P<unit>{_UNIT_ALT})")

# 日期段：整段抹掉（等长空格，保住位置），否则「20xx年9月1日」会把 9月/1日 灌进量纲池
_DATE_RE = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?"
    r"|\d{4}\s*年"
    r"|\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}"
    r"|\d{4}\s*[-/.]\s*\d{1,2}(?!\d)"
)


def cn2num(s: str) -> Optional[Decimal]:
    """中文数字转数值，支持 万/亿 分段（二百、二千、十八万、二十万、五十）。"""
    if not s:
        return None
    if s.isdigit() or re.fullmatch(r"\d+(?:\.\d+)?", s):
        return Decimal(s)
    total = Decimal(0)   # 已结算到「亿」的部分
    section = Decimal(0)  # 未满「万」的部分
    num = Decimal(0)
    seen = False
    for ch in s:
        if ch in _DIGITS:
            num = Decimal(_DIGITS[ch])
            seen = True
        elif ch in _SMALL:
            unit = Decimal(_SMALL[ch])
            section += (num if num else Decimal(1)) * unit
            num = Decimal(0)
            seen = True
        elif ch == "万":
            section = (section + num) * Decimal(10000)
            total += section
            section = num = Decimal(0)
            seen = True
        elif ch == "亿":
            total = (total + section + num) * Decimal(100000000)
            section = num = Decimal(0)
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + section + num


def _fmt(d: Decimal) -> str:
    """数值规范化字符串：整数不带小数点，小数去掉尾零"""
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def _blank_dates(text: str) -> str:
    return _DATE_RE.sub(lambda m: " " * len(m.group(0)), text)


def extract_numbers(text: str, keep_year: bool = False) -> List[Dict]:
    """抽出文本中所有「数值 + 量纲」。

    返回 [{raw, value, unit, unit_raw, pos, key}]
      value 已按量纲归一：万元→元、年→月
      key   形如 money:200 / month:36
    keep_year=True 时保留年份条目（unit="year"），供索引期统计，不参与核对。
    """
    if not text:
        return []
    body = _blank_dates(text)
    out: List[Dict] = []
    for m in _NUM_UNIT_RE.finditer(body):
        raw_num, raw_unit = m.group("num"), m.group("unit")
        kind, factor = _UNIT_BY_LITERAL[raw_unit]
        v = cn2num(raw_num)
        if v is None:
            continue
        # 年份：四位数字 + 年 且落在 1900~2100，视为日期而非期限
        if kind == "year" and raw_num.isdigit() and len(raw_num) == 4 and 1900 <= v <= 2100:
            if keep_year:
                out.append({"raw": m.group(0).strip(), "value": _fmt(v), "unit": "year",
                            "unit_raw": raw_unit, "pos": m.start(), "key": f"year:{_fmt(v)}"})
            continue
        value = v * factor
        unit = kind
        # 年 → 月：让「3 年」与「36 个月」互认
        if kind == "year":
            value = v * 12
            unit = "month"
        out.append({
            "raw": m.group(0).strip(),
            "value": _fmt(value),
            "unit": unit,
            "unit_raw": raw_unit,
            "pos": m.start(),
            "key": f"{unit}:{_fmt(value)}",
        })
    return out


def _sentence_at(text: str, pos: int) -> str:
    """取 pos 所在的句子（以句号/分号/换行切）"""
    left = max(text.rfind(ch, 0, pos) for ch in "。！？；\n")
    right = min([i for i in (text.find(ch, pos) for ch in "。！？；\n") if i != -1] or [len(text)])
    return text[left + 1:right]


def build_index(kb_dir: str) -> Dict:
    """从知识库全量原文构建「量纲 → 数值集合」倒排索引，并按法规分表。"""
    try:
        import citation_guard
        cite = citation_guard.index()
        cite.load()
        by_source = {d.get("source"): d.get("name") for d in cite.docs}
    except Exception:
        by_source = {}

    glob: Dict[str, set] = {}
    per_doc: Dict[str, Dict[str, set]] = {}
    total = 0
    for fn in sorted(os.listdir(kb_dir)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(kb_dir, fn), encoding="utf-8") as f:
            text = f.read()
        doc_name = by_source.get(fn) or fn[:-3]
        bucket = per_doc.setdefault(doc_name, {})
        for it in extract_numbers(text):
            if it["unit"] == "year":
                continue
            glob.setdefault(it["unit"], set()).add(it["value"])
            bucket.setdefault(it["unit"], set()).add(it["value"])
            total += 1

    return {
        "kb_dir": os.path.basename(kb_dir.rstrip("/\\")),
        "total": total,
        "global": {k: sorted(v, key=lambda x: Decimal(x)) for k, v in glob.items()},
        "docs": {d: {k: sorted(v, key=lambda x: Decimal(x)) for k, v in u.items()}
                 for d, u in per_doc.items()},
    }


class NumberIndex:
    """索引持有者。进程内单例，首次使用加载。"""

    def __init__(self, path: str = INDEX_PATH):
        self.path = path
        self.glob: Dict[str, set] = {}
        self.docs: Dict[str, Dict[str, set]] = {}
        self.loaded = False
        self._alias_cache: Optional[Dict[str, str]] = None

    def load(self) -> bool:
        if self.loaded:
            return True
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[NumberGuard] 索引加载失败（跳过数字核对）: {e}")
            return False
        self.glob = {k: set(v) for k, v in (data.get("global") or {}).items()}
        self.docs = {d: {k: set(v) for k, v in u.items()}
                     for d, u in (data.get("docs") or {}).items()}
        self.loaded = True
        n = sum(len(v) for v in self.glob.values())
        print(f"[NumberGuard] 索引就绪：{len(self.glob)} 个量纲，{n} 个不同数值")
        return True

    def _doc_of_law(self, law: str) -> Optional[str]:
        """句子里的《法规名》→ 索引里的法规全名"""
        if self._alias_cache is None:
            try:
                import citation_guard
                ci = citation_guard.index()
                ci.load()
                self._alias_cache = {k: v["name"] for k, v in ci.by_alias.items()}
            except Exception:
                self._alias_cache = {}
        from citation_guard import _norm_law  # 复用同一套归一，避免两套口径
        key = _norm_law(law)
        name = self._alias_cache.get(key)
        if name in self.docs:
            return name
        for d in self.docs:
            if key and (key in d or d in key):
                return d
        return None

    @staticmethod
    def _derivable(value: str, unit: str, items: List[Dict]) -> bool:
        """答案里的这个数，是否由答案自己写出的其它数算出（合计/差额）。

        只认「答案内部」的组合，不去库里凑两个数——否则 5 和 2 都在库里，
        编造的「7 分」就能靠 5+2 蒙混过关，拦截率会被自己放水放没。
        典型场景：「180000 + 18000 + 2000 = 20 万元」、「63 岁 + 延长 3 年 = 66 岁」。
        """
        try:
            target = Decimal(value)
        except Exception:
            return False
        vals: List[Decimal] = []
        for it in items:
            try:
                v = Decimal(it["value"])
            except Exception:
                continue
            if it["unit"] == unit:
                vals.append(v)
            elif unit == "age" and it["unit"] == "month" and v and v % 12 == 0:
                vals.append(v / 12)   # 「延长 3 年」换算成 3 岁参与加法
        if not vals:
            return False
        for a in vals:
            if a - target in vals:      # 差额
                return True
        n = len(vals)
        for i in range(n):
            for j in range(i, n):
                if vals[i] + vals[j] == target:
                    return True
                for k in range(j, n):
                    if vals[i] + vals[j] + vals[k] == target:
                        return True
        return False

    def check(self, answer: str) -> Dict:
        """核对入口。返回 {ungrounded, total, items}。

        ungrounded 硬无据：该「值+量纲」在全库不存在，且不是答案内部的合计/差额 → 必拦
        判定顺序：全库精确命中 → 句内法条所属法规命中 → 答案内算术推导 → 判无据
        """
        res = {"ungrounded": [], "total": 0, "items": []}
        if not answer or not (self.loaded or self.load()):
            return res

        items = extract_numbers(answer)
        res["total"] = len(items)
        for it in items:
            unit, val = it["unit"], it["value"]
            pool = self.glob.get(unit) or set()
            if val in pool:
                it["status"] = "ok"
                res["items"].append(it)
                continue
            # 句子里有法条锚点 → 优先在该法规内核对，缩小"张冠李戴"的放行面
            sent = _sentence_at(answer, it["pos"])
            law_m = re.search(r"《\s*([^》]{2,80}?)\s*》", sent)
            doc = self._doc_of_law(law_m.group(1)) if law_m else None
            if doc and val in ((self.docs.get(doc) or {}).get(unit) or set()):
                it["status"] = "ok"
                it["doc"] = doc
                res["items"].append(it)
                continue
            # 答案内部的合计/差额（20万 = 18万+1.8万+2千）
            if self._derivable(val, unit, items):
                it["status"] = "derived"
                res["items"].append(it)
                continue
            it["status"] = "ungrounded"
            it["doc"] = doc
            res["ungrounded"].append(it)
            res["items"].append(it)
        return res


_INDEX = NumberIndex()


def index() -> NumberIndex:
    return _INDEX


def render_note(bad: List[Dict]) -> str:
    """点名式校验说明：告诉用户具体哪个数字没对上，而不是笼统一句"""
    if not bad:
        return ""
    shown = "、".join(f"「{b['raw']}」" for b in bad[:4])
    more = f" 等 {len(bad)} 处" if len(bad) > 4 else ""
    return ("\n\n【系统校验】回答中的{}未能在知识库法规原文中找到对应数值，"
            "该数值可能不准确，请以下方来源原文为准。").format(shown + more)
