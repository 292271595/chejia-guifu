# -*- coding: utf-8 -*-
"""20 道刁钻法规题压力测试：每题 2 次，记录回答 + 检索片段 + 护栏触发情况。
目的：把错误归因到「检索 / 生成 / 知识库」三层。
"""
import io
import json
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

API = "http://127.0.0.1:8000/api/query"


def _squash(s: str) -> str:
    """去掉所有空白与 markdown 强调符，用于锚点宽松匹配（避免"9分"与"9 分"误判）。"""
    return re.sub(r"[\s*`_]+", "", s or "")

# (编号, 问题, 判分锚点：回答中必须出现/不得出现的要点, 期望口径说明)
CASES = [
    (1, "A2驾驶证能开C6轻型牵引挂车吗？", ["C6"], "能。附件1：A2准予驾驶其他车型含C6"),
    (2, "C1驾驶证可以开9座的面包车吗？", ["小型"], "可以。C1准驾小型、微型载客汽车（≤9座）"),
    (3, "增驾C6需要什么条件？", ["1年"], "C1/C2满1年 + 两周期无满分"),
    (4, "B1增驾A2需要持证几年？", ["2年"], "B1满2年 + 连续2个记分周期无满分"),
    (5, "A2增驾A1需要持证几年？", ["1年"], "A2满1年 + 连续3个记分周期无满分"),
    (6, "实习期上高速公路有什么要求？", ["3年"], "需持相应或更高车型驾驶证3年以上的人陪同"),
    (7, "增驾后有实习期吗？", ["实习期"], "有；但驾驶原准驾车型不受限"),
    (8, "实习期内记满12分会怎么样？", ["注销"], "注销实习的准驾车型驾驶资格"),
    (9, "驾驶与准驾车型不符的机动车记几分？", ["9分"], "9分（第九条第五项）"),
    (10, "在高速公路应急车道违法停车记几分？", ["9分"], "9分（第九条第三项）"),
    (11, "饮酒后驾驶机动车记几分？", ["12分"], "12分（第八条第一项）"),
    (12, "代替他人接受处罚和记分牟利记几分？", ["12分"], "12分（第八条第七项）"),
    (13, "70周岁以上还能申请哪些准驾车型？", ["C1", "C2", "C5", "F"], "C1/C2/C5/F，需三力测试"),
    (14, "一只眼睛看不见能考驾照吗？", ["150", "5.0"], "优眼5.0且水平视野150度，可申请C1/C2/C3/C4/C5"),
    (15, "听力不好戴助听器能考什么驾照？", ["C1", "C2"], "仅C1、C2"),
    (16, "B1驾驶证身高要求是多少？", ["150"], "150厘米以上"),
    (17, "醉驾被吊销驾驶证后几年能重新申请？", ["5年"], "非营运5年；营运10年（第十五条第五/六项）"),
    (18, "驾驶证超过有效期多久会被注销？", ["1年"], "超过有效期1年以上未换证（第七十九条第八项）"),
    (19, "驾驶证因超期未换证被注销后还能恢复吗？", ["2年", "科目一"], "未超过2年，考科目一合格恢复"),
    (20, "学习驾驶证明有效期是多久？", ["3年"], "3年，且不超过申请年龄条件上限"),
]


def ask(question: str, timeout: int = 180) -> dict:
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {
            "ok": bool(data.get("success")),
            "answer": data.get("answer", "") or data.get("message", ""),
            "sources": [
                {
                    "title": s.get("title", ""),
                    "source": s.get("source", ""),
                    "score": round(float(s.get("score", 0)), 4),
                }
                for s in (data.get("sources") or [])
            ],
            "elapsed": round(time.time() - t0, 1),
            "fact_hit": data.get("fact_hit"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "answer": f"<REQUEST_FAILED> {e}", "sources": [], "elapsed": round(time.time() - t0, 1)}


def main():
    results = []
    for idx, question, anchors, expect in CASES:
        runs = []
        for attempt in (1, 2):
            r = ask(question)
            a = r["answer"]
            runs.append(
                {
                    "attempt": attempt,
                    "ok": r["ok"],
                    "elapsed": r["elapsed"],
                    "answer": a,
                    "sources": r["sources"],
                    "guardrail": "【系统校验】" in a,
                    "fact_hit": r.get("fact_hit"),
                    "anchor_hit": [x for x in anchors if _squash(x) in _squash(a)],
                    "anchor_miss": [x for x in anchors if _squash(x) not in _squash(a)],
                }
            )
            print(f"[Q{idx:02d}-{attempt}] {r['elapsed']:>6.1f}s  anchor={runs[-1]['anchor_hit']} miss={runs[-1]['anchor_miss']}", flush=True)
            time.sleep(1)
        results.append(
            {"id": idx, "question": question, "expect": expect, "anchors": anchors, "runs": runs}
        )

    out = r"C:\RAG\rag-vehicle-system\tools\stress_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print("汇总（锚点命中数 / 2 次）")
    for item in results:
        hits = sum(1 for r in item["runs"] if not r["anchor_miss"])
        flag = "OK  " if hits == 2 else ("PART" if hits == 1 else "FAIL")
        print(f"{flag} Q{item['id']:02d} {item['question'][:30]:<32} {hits}/2")
    print(f"\n结果已存: {out}")


if __name__ == "__main__":
    main()
