# -*- coding: utf-8 -*-
"""口语 → 法规术语对照与查询改写。

为什么需要这一层：
    知识库里用的是法规原文用语（住所迁出、转让登记、解除抵押登记、加处罚款），
    而用户问的是车圈口语（转籍、过户、解押、滞纳金）。两者字面几乎不重叠，
    检索会直接漏召回——问「外地车牌能迁到本地吗」时，知识库全篇没有「迁到本地」
    这种说法，向量和 BM25 都拿不到分。

    这一层只做「查询改写」：判断问题里出现了哪个口语词，把对应的正式术语一并
    加进检索式。不改写答案，也不替代法条。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

_GLOSSARY_PATH = os.getenv(
    "GLOSSARY_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "glossary_colloquial.json"),
)

_entries: List[Dict] | None = None
_alias_index: List[Tuple[str, int]] | None = None


def _load() -> None:
    global _entries, _alias_index
    if _entries is not None:
        return
    try:
        with open(_GLOSSARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _entries = data.get("entries", [])
    except Exception as e:  # 对照表缺失不应影响主链路
        print(f"[glossary] 载入失败，查询改写关闭: {e}")
        _entries = []
    # 按口语词长度倒序，保证「转籍」优先于「籍」这类包含关系
    pairs = []
    for i, e in enumerate(_entries):
        for w in e.get("colloquial", []):
            pairs.append((w, i))
    pairs.sort(key=lambda x: -len(x[0]))
    _alias_index = pairs


def match(question: str) -> List[Dict]:
    """返回问题命中的对照条目（按命中词长度降序，去重）。"""
    _load()
    assert _alias_index is not None
    hit_idx: List[int] = []
    for word, i in _alias_index:
        if word and word in question and i not in hit_idx:
            hit_idx.append(i)
    return [_entries[i] for i in hit_idx]


def expand(question: str, max_entries: int = 2, max_terms: int = 4,
           max_total: int = 8) -> Tuple[str, List[str]]:
    """把口语问题改写成「原问题 + 正式术语」。

    返回 (改写后的检索式, 命中的条目 id 列表)。
    命中过多时只取前 max_entries 条，避免把检索式撑得过长反而稀释原问题。
    """
    hits = match(question)
    if not hits:
        return question, []

    extra: List[str] = []
    for e in hits[:max_entries]:
        extra.append(e["official"])
        extra.extend(e.get("search_terms", [])[:max_terms])

    # 去重且保持顺序；已经出现在原问题里的词不再重复添加
    seen = set()
    tail: List[str] = []
    for w in extra:
        if not w or w in question or w in seen:
            continue
        seen.add(w)
        tail.append(w)

    tail = tail[:max_total]
    if not tail:
        return question, [e["id"] for e in hits]
    return f"{question} {' '.join(tail)}", [e["id"] for e in hits]


def explain(question: str) -> str:
    """给用户看的口径提示：你说的口语，法规上叫什么。无命中返回空串。"""
    hits = match(question)
    if not hits:
        return ""
    e = hits[0]
    note = f"，{e['note']}" if e.get("note") else ""
    return f"（你说的「{e['colloquial'][0]}」，在法规里对应「{e['official']}」{note}）"


def stats() -> Dict:
    _load()
    assert _entries is not None
    return {"entries": len(_entries), "aliases": len(_alias_index or [])}
