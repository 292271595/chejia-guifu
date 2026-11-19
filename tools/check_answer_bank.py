# -*- coding: utf-8 -*-
"""答案库断言：语义召回 + 守卫裁决的行为边界。

不是「加个库顺手跑一遍」，而是把 2026-11-19 的可行性实验固化成回归：

  1. 危险对必须拒绝——字面像、答案相反的问句（醉驾 vs 报废车、买车上牌 vs
     考驾照），相似度再高也不能吐（阈值分不开是实测结论，守卫是唯一裁决）；
  2. 变体必须命中——正则接不住的口语变体（"想报个驾照得准备什么材料"），
     这是答案库存在的意义；
  3. 种子与 edge.json 同步——种子就是表行 verify.questions，数量对不上
     说明 build/读表出了问题；
  4. 嵌入缓存指纹校验——换了嵌入后端必须重嵌，不许拿旧空间的向量算相似度。

跑不动本地嵌入（模型没缓存）时明确 FAIL 而不是静默跳过——静默跳过会让
「答案库坏了」伪装成「答案库没问题」。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

DANGER = [  # (问句, 原因) —— 事实层未接住时，答案库也绝不能吐
    ("醉驾喝多了吊销了几年后才能再考驾照", "醉驾5年档，种子库里没有对应表行，吐了就是报废车答案"),
    ("异地给车子上牌登记要居住证明吗", "上牌要居住证明，答案与驾照报名相反"),
    ("70岁还能开大货车拉货吗", "驾驶年龄，库里无对应表行"),
    ("考驾照一共花多少钱", "问费用，exclude 必须拦"),
]

HIT = [  # (问句, 期望 fact) —— 事实层正则接不住的口语变体，答案库必须接住
    ("想报个驾照，得准备点什么材料", "AB:APPLY:MATERIAL"),
    ("七十多岁了还能考小汽车驾照不", "AB:AGE:APPLY_ALL"),
]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    import fact_table as ft
    import answer_bank as ab

    # ── 嵌入可用性 ──
    try:
        import vector_store as vs
        fn = vs.LocalSTEmbeddingFunction()
    except Exception as e:
        print(f"FAIL: 本地嵌入不可用，答案库断言无法执行（{type(e).__name__}: {e}）")
        print("     先跑一次 tools 下任意用本地嵌入的脚本预热模型缓存。")
        return 1

    ab.reset(embedding_fn=fn)

    # ── 种子同步 ──
    with open(os.path.join(ROOT, "data", "knowledge_table", "edge.json"), encoding="utf-8") as f:
        rows = json.load(f).get("rows") or []
    expect_n = sum(len((r.get("verify") or {}).get("questions") or []) for r in rows)
    seeds = ab._load_seeds()
    if len(seeds) != expect_n:
        print(f"FAIL: 种子数 {len(seeds)} != 表行问句总数 {expect_n}，读表不同步")
        return 1

    # ── 危险对：宁可拒绝，绝不错吐 ──
    errors = []
    for q, why in DANGER:
        f = ft.lookup(q)
        if f:
            continue  # 事实层接住（如醉驾走 REVOKE），与答案库无关
        r = ab.lookup(q, embedding_fn=fn)
        if r:
            errors.append(f"危险对被错吐: {q!r} 吐了 {r.get('fact')}（{why}）")
        else:
            print(f"  拒绝 OK  {q}")

    # ── 变体：必须接住 ──
    for q, expect in HIT:
        f = ft.lookup(q)
        if f:
            print(f"  变体改由事实层接住（{f.get('fact')}），不算失败: {q}")
            continue
        r = ab.lookup(q, embedding_fn=fn)
        got = (r or {}).get("fact")
        if got == expect:
            print(f"  命中 OK  {q} -> {got} sim={r.get('ab_sim')}")
        else:
            errors.append(f"变体漏接: {q!r} 期望 {expect}，实际 {got or '不吐'}")

    # ── 缓存指纹：换嵌入后端后旧缓存必须失效 ──
    st = ab._state or {}
    if st.get("empty"):
        errors.append(f"答案库未就绪: {st.get('reason')}")
    elif os.path.exists(ab._CACHE):
        import numpy as np
        cached = np.load(ab._CACHE, allow_pickle=False)
        if str(cached["fingerprint"]) != st.get("fingerprint"):
            errors.append("缓存指纹与当前种子/嵌入不一致（重嵌后仍不一致=缓存逻辑 bug）")

    if errors:
        print("答案库断言失败：")
        for e in errors:
            print("  - " + e)
        return 1
    print(f"答案库断言通过：种子 {len(seeds)} 条，危险对 {len(DANGER)} 拒绝、变体 {len(HIT)} 命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
