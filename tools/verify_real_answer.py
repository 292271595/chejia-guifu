# -*- coding: utf-8 -*-
"""用真实问题验收：修复后到底答什么。

不跑题库断言，直接看答案原文——断言只能证明"包含关键词"，看不出"答的是不是这件事"。
这里把用户截图里那条问题、以及同族的几个高频问法，走线上同一条链路（含复核），
打印完整答案和耗时。
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

eng = create_rag_engine(
    os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"), os.getenv("API_KEY"),
    os.getenv("MODEL"), os.getenv("EMBED_BACKEND", "cloud"),
    os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
)

QS = [
    # 截图那条原话
    "我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗",
    # 同一族的其他问法（换个说法还会不会串）
    "63岁A2还能开吗",
    "62岁A2可以申请延长吗",
    "70岁C1还能开吗",
    "驾驶证因超期未换证被注销怎么恢复",
    "驾驶证延期换证怎么办",
    "科目一考试不合格多久可以重新预约",
    "实习期内可以学法减分吗",
]

for q in QS:
    t0 = time.time()
    r = eng.query(q, budget=12.0)
    sec = time.time() - t0
    rv = (r.get("fact_review") or {}).get("verdict") or "-"
    print(f"\n{'=' * 70}")
    print(f"问：{q}")
    print(f"耗时 {sec:.1f}s｜命中 {r.get('fact_hit')}｜复核 {rv}"
          f"｜降级 {bool(r.get('degraded'))}")
    print("-" * 70)
    ans = (r.get("answer") or "").strip()
    print(ans[:560] + ("…" if len(ans) > 560 else ""))
