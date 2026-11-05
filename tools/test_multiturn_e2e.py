# -*- coding: utf-8 -*-
"""多轮连问端到端验收

不走 HTTP，直接建引擎跑完整链路（事实层 → 复核 → 检索 → 生成 → 引用对账），
模拟小程序连续问三轮，每轮把前几轮带上。

为什么必须端到端：
  tools/test_context_rewrite.py 只看「改写后命中哪个处理器」，但命中处理器
  不等于答案对——「那扣满 12 分呢」早期被改成「扣满 12 分能上高速吗」，
  也确实命中了 INTERNSHIP:HIGHWAY，答案却答的是「上高速要陪同」。
  所以这里直接把答案打出来看。

用法：
  python tools/test_multiturn_e2e.py
  python tools/test_multiturn_e2e.py --rounds 3
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

CHAINS = [
    {
        "name": "驾驶证过期三连问",
        "turns": ["C1 驾驶证过期了怎么办", "那要多久", "那超过两年呢"],
    },
    {
        "name": "酒驾转醉驾再问期限",
        "turns": ["饮酒驾驶怎么处罚", "那醉驾呢", "几年能考"],
    },
    {
        "name": "实习期高速转扣分",
        "turns": ["实习期能上高速吗", "那扣满 12 分呢", "那会注销吗"],
    },
]


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))

    from rag_engine import create_rag_engine

    api_key = os.getenv("API_KEY", "")
    backend = os.getenv("GEN_BACKEND", "zhipu")
    base = os.getenv("API_BASE") or (
        "https://api.siliconflow.cn/v1" if backend == "sili"
        else "https://open.bigmodel.cn/api/paas/v4")
    model = os.getenv("MODEL", "glm-4-flash")

    engine = create_rag_engine(
        os.path.join(ROOT, "chroma_db"), base, api_key, model,
        embed_backend=os.getenv("EMBED_BACKEND", "cloud"),
        embed_api_key=os.getenv("EMBED_API_KEY", ""),
        embed_api_base=os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1"),
    )

    all_sec = []
    for chain in CHAINS:
        print("\n" + "=" * 78)
        print(f"链：{chain['name']}")
        history = []
        for i, q in enumerate(chain["turns"], 1):
            t0 = time.time()
            out = engine.query(q, budget=12.0, history=history or None)
            sec = time.time() - t0
            all_sec.append(sec)

            ans = " ".join((out.get("answer") or "").split())
            resolved = out.get("resolved_question") or ""
            fact = out.get("fact_hit") or "-"
            degrade = " ⚠降级" if out.get("degraded") else ""

            print(f"\n  第 {i} 问：{q}")
            if resolved:
                print(f"  补全为：{resolved}")
            elif i > 1:
                print(f"  未补全：{out.get('ctx_reason') or '（无）'}")
            print(f"  耗时 {sec:.2f}s | 事实层 {fact}{degrade}")
            print(f"  答案：{ans[:260]}")

            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": ans[:150]})

    print(f"\n{'=' * 78}")
    if all_sec:
        s = sorted(all_sec)
        print(f"共 {len(s)} 轮，中位 {s[len(s)//2]:.2f}s，最大 {s[-1]:.2f}s，"
              f"超 12s 的 {sum(1 for x in s if x > 12)} 轮")


if __name__ == "__main__":
    main()
