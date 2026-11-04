# -*- coding: utf-8 -*-
"""复核拦下后转 RAG：答案会更好还是更差？

这是决定整套方案是否成立的最后一环。
如果"拦住事实层错答 → 转检索生成"得到的答案更差，
那复核就只能是"提醒"，不能是"重答"。

做法：屏蔽事实层（lookup 恒返回 None，build_fact_hint 恒返回空），
让这几个问题完整走检索 + 生成，看结果。
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

import fact_table  # noqa: E402

# 屏蔽事实层，模拟"复核判 WRONG，转检索"
fact_table.lookup = lambda q, *a, **k: None
fact_table.build_fact_hint = lambda q, *a, **k: ""

from rag_engine import create_rag_engine  # noqa: E402

eng = create_rag_engine(
    os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"), os.getenv("API_KEY"),
    os.getenv("MODEL"), os.getenv("EMBED_BACKEND", "cloud"),
    os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
)

QS = [
    ("我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗", "正解：满63岁不能再申请延长，应换C1/C2（A2保留C6）"),
    ("70岁C1还能开吗", "正解：C1 到 70 岁仍可开，只是每年提交体检证明"),
    ("驾驶证因超期未换证被注销怎么恢复", "正解：注销未满2年考科目一恢复；超2年重考"),
    ("科目一考试不合格多久可以重新预约", "正解：科目一不合格可当场补考一次，之后重新预约"),
]

for q, truth in QS:
    t0 = time.time()
    r = eng.query(q, budget=10.0)
    print(f"\n{'=' * 68}\nQ: {q}")
    print(f"（{truth}）")
    print(f"耗时 {time.time() - t0:.1f}s  fact_hit={r.get('fact_hit')}")
    print("-" * 68)
    print((r.get("answer") or "")[:800])
