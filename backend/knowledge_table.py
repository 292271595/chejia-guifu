# -*- coding: utf-8 -*-
"""知识点表加载与处理器工厂（2026-11-16）

《问答精准化路线》第一步的落地件。目标只有一个：

    新增知识点 = 加一行 JSON，不改 fact_table.py 的调度逻辑。

原来的做法是「一个问题写一个 Python 函数，答案用字符串常量硬拼」。8 个还能扛，
50 个就会互相抢答，而且每加一个都要全量回归一遍。这里把「答案、依据、守卫、
判分要素」全部挪进 data/knowledge_table/*.json，Python 只留一份通用的读取逻辑。

调度仍然走 fact_table 原有机制（主题正则筛候选 + 诉求门控 + 内部二次守卫），
所以这一个模块不引入新的调度路径——每行知识在 fact_table 里注册成独立的
handler 函数，行为与原来的手写函数一致。

字段说明（一行一个知识点）：
  id        事实键，如 EDGE:MOTO_SCRAP_YEARS，会进入 result["fact"]
  handler   迁移前的函数名，仅用于对照与日志，不参与匹配
  topic     调度层主题正则。由 fact_table 的注册表使用，加载器不解释它
  require   内部守卫：全部命中才算本行（对应原函数的 `if not re.search: return None`）
  exclude   内部守卫：任一命中就弃权（对应原函数的 `if re.search: return None`）
  slot      可选的变量抽取，形如 {"name","pattern","kind","min","max"}
  answer    答案模板，可用 {slot名} 插值；不做其它格式化，避免误伤花括号
  sources   依据列表，quote 是原文摘录，供一致性校验与报告引用
  verify    判分要素，must / must_not。第二步会把断言从这里生成
  status    active / retired；retired 的行不再参与匹配
  expires   失效日期（YYYY-MM-DD），到期自动不再命中——防止过期的答案
            以最高优先级压住正确的检索结果

设计上刻意不做的事：
  · 不做模糊匹配打分。当前阶段仍是「正则命中 + 守卫」，因为可读、可 diff、可人工审。
    文档里已写明这是会过时的实现，终局是语义匹配，但换之前先让表跑起来。
  · 不让答案里出现运行时计算。能进表的必须是确定文本，数值随输入变的用 slot。
"""
import json
import os
import re
from typing import Callable, Dict, List, Optional

from fact_common import result

# 表放在 backend 下，不放 data/：镜像的 .dockerignore 排掉了整个 data/，
# 放在那里就意味着「本地一切正常、线上 8 条表驱动知识点静默消失」。
# 症状极隐蔽——只有一行 stdout 提示，而云托管 stdout 只留 7 天。
# 同一条理由在 Dockerfile 里对 kb_index/*.json 已经写过一次，这里是第二例。
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_data")

_DIR = DIR

_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
       "七": 7, "八": 8, "九": 9, "十": 10}

_CACHE: Dict[str, List[Dict]] = {}


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _is_live(row: Dict) -> bool:
    """status 与有效期判定。过期行必须退出匹配，否则会以最高优先级压住检索结果。"""
    if (row.get("status") or "active") != "active":
        return False
    expires = (row.get("expires") or "").strip()
    if expires and expires <= _today():
        print(f"[KnowledgeTable] {row.get('id')} 已过有效期 {expires}，退出匹配")
        return False
    effective = (row.get("effective") or "").strip()
    if effective and effective > _today():
        return False
    return True


def load(domain: str = "edge") -> List[Dict]:
    """读表并缓存。文件缺失/损坏时返回空表：宁可少答，不能让问答服务崩。"""
    if domain in _CACHE:
        return _CACHE[domain]

    path = os.path.join(_DIR, f"{domain}.json")
    if not os.path.isfile(path):
        print(f"[KnowledgeTable] 表文件不存在：{path}，{domain} 域的确定性事实层停用")
        _CACHE[domain] = []
        return []

    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = [r for r in payload.get("rows") or [] if _is_live(r)]
    except Exception as e:
        print(f"[KnowledgeTable] 读表失败：{e}，{domain} 域停用")
        rows = []

    _CACHE[domain] = rows
    return rows


def _cn_to_int(raw: str):
    """中文数字转整数，覆盖一~十二。「十二」不能走单字表，否则会解析失败。"""
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if len(raw) == 2 and raw.startswith("十"):
        return 10 + (_CN.get(raw[1]) or 0)
    if len(raw) == 2 and raw.endswith("十"):
        return (_CN.get(raw[0]) or 0) * 10
    return _CN.get(raw)


def _extract_slot(row: Dict, q: str):
    """抽取槽位。返回 (ok, value)。

    刻意不把值写回 row：row 来自缓存、被所有请求共享，写回会在并发下串答案
    （A 问「扣 6 分」、B 问「扣 3 分」同时进来，谁后写谁覆盖，两个答案互相污染）。
    """
    slot = row.get("slot")
    if not slot:
        return True, None
    m = re.search(slot["pattern"], q)
    if not m:
        return False, None
    raw = m.group(1)
    val = _cn_to_int(raw) if slot.get("kind") == "cn2num" else raw
    if val is None:
        return False, None
    lo, hi = slot.get("min"), slot.get("max")
    if lo is not None and val < lo:
        return False, None
    if hi is not None and val > hi:
        return False, None
    return True, val


def _render(row: Dict, value) -> Optional[str]:
    """渲染答案。只替换已声明的槽位，其余花括号原样保留。"""
    answer = row.get("answer") or ""
    slot = row.get("slot")
    if not slot:
        return answer
    try:
        return answer.replace("{" + slot["name"] + "}", str(value))
    except Exception as e:
        print(f"[KnowledgeTable] {row.get('id')} 渲染失败：{e}")
        return None


def _build_sources(row: Dict) -> List[Dict]:
    out = []
    for s in row.get("sources") or []:
        out.append({"title": s.get("title", ""),
                    "source": s.get("file", ""),
                    "score": 1.0,
                    "snippet": (s.get("quote") or "")[:300]})
    return out


def make_handler(row: Dict) -> Callable[[str], Optional[Dict]]:
    """把一行知识包装成 fact_table 认识的 handler。

    返回的函数签名与手写处理器一致：吃问句，命中返回 {'answer','sources','fact'}，
    不命中返回 None（调度继续往下走）。audit 会读 __name__，所以取一个可读名字。
    """
    rid = row.get("id") or "UNKNOWN"
    requires = [re.compile(p) for p in row.get("require") or []]
    excludes = [re.compile(p) for p in row.get("exclude") or []]

    def handler(q: str) -> Optional[Dict]:
        for p in requires:
            if not p.search(q):
                return None
        for p in excludes:
            if p.search(q):
                return None
        ok, value = _extract_slot(row, q)
        if not ok:
            return None
        text = _render(row, value)
        if text is None:
            return None
        return result(text, _build_sources(row), rid)

    handler.__name__ = "table_" + rid.replace(":", "_").lower()
    handler.__doc__ = f"知识点表行 {rid}（迁移自 {row.get('handler') or '手写处理器'}）"
    return handler


def registry(domain: str = "edge"):
    """返回 [(handler, topic, row)]，供 fact_table 拼装 _HANDLERS。"""
    out = []
    for row in load(domain):
        topic = (row.get("topic") or "").strip()
        if not topic:
            print(f"[KnowledgeTable] {row.get('id')} 缺 topic，跳过")
            continue
        out.append((make_handler(row), topic, row))
    return out


def find(fact_id: str, domain: str = "edge") -> Optional[Dict]:
    for row in load(domain):
        if row.get("id") == fact_id:
            return row
    return None


def reload(domain: Optional[str] = None):
    """改表后热加载（测试与运维用）。"""
    if domain is None:
        _CACHE.clear()
    else:
        _CACHE.pop(domain, None)
