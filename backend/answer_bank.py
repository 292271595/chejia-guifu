# -*- coding: utf-8 -*-
"""答案库：语义召回 + 守卫裁决的第二道确定性防线。

定位（为什么存在）：
    fact_table 靠正则匹配问法，口语变体接不住（"考个驾照要带什么证件"）。
    这些变体掉进生成层后由模型自由发挥，是线上错答的来源之一。答案库把
    knowledge_table 表行的 verify.questions 当种子，用向量相似度召回近邻
    问法，再用表行自带的 require/exclude 守卫复核——通过了才吐表行的
    审定答案，通不过就返回 None 交回生成层。

为什么守卫不能省（2026-11-19 实验，bge-small-zh 实测）：
    同义变体相似度 0.82~0.92，而答案相反的危险对（"异地考驾照要居住证吗"
    vs "异地买车上牌要居住证吗"）高达 0.85——两个区间完全重叠，不存在能
    同时接住同义、挡住混淆的阈值。向量只能做召回，裁决必须靠确定性守卫。
    权衡是宁可漏掉（漏掉=回退生成层，行为同现状），绝不错吐。

增长机制（零维护成本）：
    种子不单存文件，运行时直接从 edge.json 表行读——每加一行表行、每次
    把新变体补进 verify.questions，答案库自动扩充，不需要同步动作。

嵌入与缓存：
    复用调用方传入的 embedding_fn（与检索层同一向量空间）。种子向量缓存
    到磁盘，以「问句指纹 + 嵌入后端指纹」双校验——两个指纹任一变化都
    重嵌，防止换嵌入模型后拿旧空间的向量算相似度（那是静默的垃圾结果）。
    嵌入不可用（none 后端/异常）时答案库整体静默关闭，检索行为与现状
    完全一致，绝不向上层抛异常。
"""
import hashlib
import json
import os
import re
from typing import Dict, Optional

import knowledge_table

# 表的目录只在 knowledge_table.DIR 里写一次，别在这儿再抄一遍路径：
# 抄两遍的后果是「挪了表、忘了改这里」，线上答案库静默关掉。
_TABLE = os.path.join(knowledge_table.DIR, "edge.json")
_CACHE = os.path.join(knowledge_table.DIR, "answer_bank_cache.npz")

_TOP_K = 3       # 召回候选数：实验显示危险对常排 top1，3 条给守卫轮询足够
_MIN_SIM = 0.75  # 召回下限：低于它连守卫都不必跑（实验中危险对最低 0.71）

_state: Optional[Dict] = None


def _load_seeds() -> list:
    with open(_TABLE, encoding="utf-8") as f:
        rows = json.load(f).get("rows") or []
    seeds = []
    for r in rows:
        for q in (r.get("verify") or {}).get("questions") or []:
            seeds.append({
                "q": q,
                "fact": r.get("id") or "",
                "answer": r.get("answer") or "",
                "require": r.get("require") or [],
                "exclude": r.get("exclude") or [],
                "sources": [
                    {"title": s.get("title", ""), "source": s.get("file", ""),
                     "score": 1.0, "snippet": (s.get("quote") or "")[:300]}
                    for s in (r.get("sources") or [])
                ],
            })
    return seeds


def _fn_identity(embedding_fn) -> str:
    """嵌入后端指纹：类型+模型名+维度。换后端必须重建缓存。"""
    model = getattr(embedding_fn, "model_name", "") or ""
    dim = getattr(embedding_fn, "dimension", "") or ""
    return f"{type(embedding_fn).__name__}|{model}|{dim}"


def _guard(seed: Dict, question: str) -> bool:
    """守卫：把用户问句（不是种子问句）过表行 require/exclude。缺一不吐。"""
    for p in seed["require"]:
        if not re.search(p, question):
            return False
    for p in seed["exclude"]:
        if re.search(p, question):
            return False
    return True


def _ensure(embedding_fn=None) -> Dict:
    global _state
    if _state is not None:
        return _state

    seeds = _load_seeds()
    if not seeds:
        _state = {"empty": True, "reason": "表行为空"}
        return _state

    # 嵌入不可用（none 后端/未配置）：答案库静默关闭
    if embedding_fn is None or getattr(embedding_fn, "dimension", 0) == 0:
        _state = {"empty": True, "reason": "嵌入不可用"}
        return _state

    try:
        import numpy as np
    except ImportError:
        _state = {"empty": True, "reason": "numpy 不可用"}
        return _state

    fp = hashlib.sha1(
        ("\n".join(s["q"] for s in seeds) + "||" + _fn_identity(embedding_fn)).encode("utf-8")
    ).hexdigest()

    q_vecs = None
    if os.path.exists(_CACHE):
        try:
            cached = np.load(_CACHE, allow_pickle=False)
            if str(cached["fingerprint"]) == fp and cached["vecs"].shape[0] == len(seeds):
                q_vecs = cached["vecs"]
        except Exception:
            q_vecs = None

    if q_vecs is None:
        try:
            vecs = np.asarray(embedding_fn([s["q"] for s in seeds]), dtype="float32")
        except Exception as e:
            print(f"[AnswerBank] 种子嵌入失败，答案库关闭: {e}")
            _state = {"empty": True, "reason": f"种子嵌入失败: {e}"}
            return _state
        if vecs.ndim != 2 or vecs.shape[0] != len(seeds):
            _state = {"empty": True, "reason": "嵌入结果形状异常"}
            return _state
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        q_vecs = (vecs / norms).astype("float32")
        try:
            np.savez(_CACHE, vecs=q_vecs, fingerprint=np.array(fp))
        except Exception:
            pass  # 缓存写失败只影响下次启动速度，不影响正确性

    _state = {"seeds": seeds, "q_vecs": q_vecs, "fn": embedding_fn, "fingerprint": fp}
    print(f"[AnswerBank] 就绪：{len(seeds)} 条种子（{len({s['fact'] for s in seeds})} 行表行）")
    return _state


def lookup(question: str, embedding_fn=None) -> Optional[Dict]:
    """查答案库。命中返回与 fact_table 同构的 dict（answer/sources/fact），
    未命中或任何异常返回 None——调用方继续走原有链路，行为与现状一致。"""
    st = _ensure(embedding_fn)
    if not st or st.get("empty"):
        return None

    try:
        import numpy as np
        v = np.asarray(st["fn"]([question])[0], dtype="float32")
        n = float(np.linalg.norm(v))
        if n == 0:
            return None
        v = v / n
        sims = st["q_vecs"] @ v
        order = np.argsort(-sims)[:_TOP_K]
    except Exception as e:
        print(f"[AnswerBank] 查询嵌入失败（关闭本次查询）: {e}")
        return None

    for i in order:
        if float(sims[i]) < _MIN_SIM:
            break  # 相似度已排序递减，后面只会更低
        s = st["seeds"][int(i)]
        if _guard(s, question):
            return {
                "answer": s["answer"],
                "sources": s["sources"],
                "fact": f"AB:{s['fact']}",
                "ab_sim": round(float(sims[i]), 3),
            }
    return None


def reset(embedding_fn=None) -> Dict:
    """清空状态并重建（表行更新后 / 测试用）。"""
    global _state
    _state = None
    return _ensure(embedding_fn)
