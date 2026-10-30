# -*- coding: utf-8 -*-
"""检索层独立诊断：对 20 道题只做检索，不调生成模型。
输出每题的 top-8 片段标题、分数、以及「关键锚点是否出现在检索结果中」。
用于区分：若锚点已在检索结果里而回答仍错 → 生成侧责任；若不在 → 检索侧责任。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

from vector_store import VectorStore  # noqa: E402

# (编号, 问题, 检索层锚点：出现在任一片段中即视为「检索成功」)
CASES = [
    (1, "A2驾驶证能开C6轻型牵引挂车吗？", ["A2", "C6"]),
    (2, "C1驾驶证可以开9座的面包车吗？", ["小型、微型载客汽车"]),
    (3, "增驾C6需要什么条件？", ["轻型牵引挂车（C6）准驾车型的，已取得"]),
    (4, "B1增驾A2需要持证几年？", ["中型客车（B1）或者大型货车（B2）准驾车型资格2年以上"]),
    (5, "A2增驾A1需要持证几年？", ["重型牵引挂车（A2）准驾车型资格1年以上"]),
    (6, "实习期上高速公路有什么要求？", ["3年以上的驾驶人陪同"]),
    (7, "增驾后有实习期吗？", ["增加准驾车型后的实习期"]),
    (8, "实习期内记满12分会怎么样？", ["注销其实习的准驾车型"]),
    (9, "驾驶与准驾车型不符的机动车记几分？", ["驾驶与准驾车型不符的机动车"]),
    (10, "在高速公路应急车道违法停车记几分？", ["违法停车"]),
    (11, "饮酒后驾驶机动车记几分？", ["饮酒后驾驶机动车的"]),
    (12, "代替他人接受处罚和记分牟利记几分？", ["代替实际机动车驾驶人接受"]),
    (13, "70周岁以上还能申请哪些准驾车型？", ["70周岁以上"]),
    (14, "一只眼睛看不见能考驾照吗？", ["单眼视力障碍"]),
    (15, "听力不好戴助听器能考什么驾照？", ["佩戴助听设备"]),
    (16, "B1驾驶证身高要求是多少？", ["150厘米"]),
    (17, "醉驾被吊销驾驶证后几年能重新申请？", ["未满5年"]),
    (18, "驾驶证超过有效期多久会被注销？", ["超过机动车驾驶证有效期1年以上未换证"]),
    (19, "驾驶证因超期未换证被注销后还能恢复吗？", ["未超过2年"]),
    (20, "学习驾驶证明有效期是多久？", ["学习驾驶证明的有效期为3年"]),
]


def main():
    store = VectorStore(persist_dir=r"C:\RAG\rag-vehicle-system\chroma_db",
                        embed_backend=os.getenv("EMBED_BACKEND", "local"))
    ok_count = 0
    for idx, q, anchors in CASES:
        results = store.search(q, top_k=8)
        blob = "\n".join(r["content"] for r in results)
        hit = [a for a in anchors if a in blob]
        miss = [a for a in anchors if a not in blob]
        if not miss:
            ok_count += 1
        print(f"\n{'=' * 74}")
        print(f"Q{idx:02d} {q}")
        print(f"  检索层判定: {'✅ 命中' if not miss else '❌ 未命中'}  锚点={hit} 缺失={miss}")
        for i, r in enumerate(results[:5], 1):
            t = r["metadata"].get("title", "")[:26]
            print(f"    [{i}] score={r['score']:.3f} kw={r.get('keyword_score', 0):.2f} {t}")
    print(f"\n检索层总命中: {ok_count}/20")


if __name__ == "__main__":
    main()
