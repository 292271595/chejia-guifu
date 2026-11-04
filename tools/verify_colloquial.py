# -*- coding: utf-8 -*-
"""口语化问法验收：真实用户不会照着题库说话。

题库 116 条是"标准问法"（闯红灯怎么处罚、70岁C1还能开吗），
真实用户说的是「我A2本明年就63了还能开几年」「老头子70了还开小车行不行」。
这组专门测口语化、信息不全、带错别字的问法，看会不会掉到检索或者答偏。

判读标准不是断言，是人工看答案第一句对不对——所以脚本只负责跑和打印。
"""
import os
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

from rag_engine import create_rag_engine  # noqa: E402

# (问法, 期望的首句要点)
CASES = [
    ("我A2本，明年就63了，还能开几年", "63 岁前一年内申请延长，最长到 66"),
    ("老头子70了还开小车行不行", "C1 能开，需提交体检证明"),
    ("驾照过期两年了还能恢复不", "注销未超 2 年可考科目一恢复"),
    ("扣了6分能减分不，我还在实习期", "实习期不予受理"),
    ("科目一挂了啥时候能重考", "可当场补考一次"),
    ("车顶装个行李箱要备案吗", "属于变更登记/需申请"),
    ("醉驾还能开车不", "吊销，5 年内不得重新取得"),
    ("驾照吊销了多久能重考", "按吊销情形 2/5/10 年或终生"),
    ("年检能在外地做吗", "可以异地检验"),
    ("闯黄灯算不算违章", "黄灯亮时已越线可继续通行"),
    ("副驾驶没系安全带罚谁", "乘车人未系，罚乘车人/警告"),
    ("新车几年免检", "6 年内免上线，每 2 年申领标志"),
    ("驾驶证丢了能异地补办吗", "可以异地补证"),
    ("B2能开小客车吗", "可以，B2 含 C1 准驾"),
    ("实习期能上高速吗", "需持相应驾照 3 年以上的人陪同"),
    ("我C1扣满12分了咋整", "扣留驾照，参加满分教育，考科目一"),
    ("摩托车驾照能开电动车吗", "看是否属机动车（电动摩托车）"),
    ("大货车超载怎么罚", "按超载比例罚，记分"),
]

eng = create_rag_engine(
    os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"), os.getenv("API_KEY"),
    os.getenv("MODEL"), os.getenv("EMBED_BACKEND", "cloud"),
    os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
)

fact_n = rag_n = deg_n = 0
for q, expect in CASES:
    t0 = time.time()
    r = eng.query(q, budget=12.0)
    sec = time.time() - t0
    hit = r.get("fact_hit")
    deg = bool(r.get("degraded"))
    if hit:
        fact_n += 1
    else:
        rag_n += 1
    if deg:
        deg_n += 1
    first = ""
    for line in (r.get("answer") or "").split("\n"):
        if line.strip():
            first = line.strip().lstrip("*# ").strip()
            break
    print(f"\n问：{q}")
    print(f"  命中 {hit or 'RAG'}｜{sec:.1f}s｜降级 {deg}")
    print(f"  首句：{first[:110]}")
    print(f"  应为：{expect}")

print(f"\n{'=' * 70}")
print(f"{len(CASES)} 条口语问法：走事实层 {fact_n}、走检索 {rag_n}、降级 {deg_n}")
