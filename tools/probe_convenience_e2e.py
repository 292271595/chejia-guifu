# -*- coding: utf-8 -*-
"""便民措施端到端验收：直接问，看答案对不对。

只判关键词不够，所以每条都写「必含」；少数几条再写「禁含」，
专门防上一版答反的情况（如把"只考两科"答成"全部科目"）。

用法：python tools/probe_convenience_e2e.py [条数]
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

# (问句, 必含任一词, 禁含词)
CASES = [
    ("异地考驾照要居住证吗", ["不需要", "不用", "无需"], []),
    ("驾驶证考试能换几次考试地", ["三次", "3 次", "3次"], []),
    ("人在国外驾照快过期了怎么办", ["延期"], ["必须回国"]),
    ("70 岁还能考驾照吗", ["记忆力", "三力"], ["不能申请"]),
    ("自动挡科目二考几项", ["4项", "4 项", "四项"], []),
    ("注销满两年重新申领要考几科", ["科目三道路驾驶技能"], ["还能恢复"]),
    ("老年人考驾照能让子女代办吗", ["代办"], []),
    ("军人换地方驾照驾龄怎么算", ["连续计算", "连续"], []),
    ("新车能网上上牌吗", ["12123"], []),
    ("二手车过户还要回原地提档吗", ["不用", "无需", "不需要"], []),
    ("电子行驶证有用吗", ["同等", "法律效力"], []),
    # 禁含词不能写「全国都能」——答案里的「不是全国都能用」会把它当子串命中（踩过）
    ("轻微事故能视频快处吗", ["36", "试点"], ["全国已全面推广", "全国范围内均可"]),
    ("摩托车 6 年内免检吗", ["10 年", "十年"], ["6 年内免检，之后"]),
    ("免检标志还要贴纸吗", ["不用", "不需要", "无需"], ["免检（免上线检验）的车型范围"]),
    ("满分学习能在外地变更吗", ["12123", "App"], ["必须回原"]),
    # ── 第二轮筛查新增：这几条此前从未端到端问过，其中两条测出过真错 ──
    # 军人换 B2 只考科目一（原本免科目二，2023-06-01 起再免科目三）
    ("军人换B2考几科", ["只考科目一", "科目一"], ["科目二和科目三"]),
    # C1 是 30 日，只有 C2 是 20 日
    ("C1科目三多久能约考", ["30"], []),
    # 补证不用去窗口：检索很容易落到第六十九条答成"必须去车管所"
    ("补驾驶证必须去车管所吗", ["不用", "不需要", "无需", "邮寄"], ["必须本人到窗口办理"]),
    # 县城能办，不是只能去市里
    ("县城能考驾照吗", ["县级", "县"], []),
    # 驾驶记录证明走 12123，不用去车管所开
    ("驾驶记录证明怎么开", ["12123", "App", "交通安全记录"], []),
    # 电子驾驶证截图不能用
    ("电子驾驶证截图能用吗", ["截图"], []),
]


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(CASES)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    from rag_engine import create_rag_engine

    api_key = os.getenv("API_KEY", "")
    base = os.getenv("API_BASE") or "https://open.bigmodel.cn/api/paas/v4"
    engine = create_rag_engine(
        os.path.join(ROOT, "chroma_db"),
        base,
        api_key,
        os.getenv("MODEL", "glm-4-flash"),
        embed_backend=os.getenv("EMBED_BACKEND", "cloud"),
        embed_api_key=os.getenv("EMBED_API_KEY", ""),
        embed_api_base=os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1"),
    )

    ok = 0
    spent = []
    for q, must, forbid in CASES[:limit]:
        t0 = time.time()
        out = engine.query(q, budget=12.0)
        sec = time.time() - t0
        spent.append(sec)
        ans = " ".join((out.get("answer") or "").split())

        hit = any(k in ans for k in must)
        bad = [k for k in forbid if k in ans]
        good = hit and not bad
        ok += good

        print("=" * 76)
        print(("√ " if good else "× ") + q + f"   {sec:.2f}s  事实层={out.get('fact_hit') or '-'}")
        if not hit:
            print(f"   缺关键词 {must}")
        if bad:
            print(f"   出现禁含词 {bad}")
        print("   " + ans[:300])

    s = sorted(spent)
    print("\n" + "=" * 76)
    print(f"答对 {ok}/{len(CASES[:limit])}   中位 {s[len(s) // 2]:.2f}s   最大 {s[-1]:.2f}s")
    return 0 if ok == len(CASES[:limit]) else 1


if __name__ == "__main__":
    sys.exit(main())
