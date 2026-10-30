# -*- coding: utf-8 -*-
"""修复后复测：只测第一轮出问题的 7 题，每题 2 次。"""
import io
import json
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

API = "http://127.0.0.1:8000/api/query"
CASES = [
    (1, "A2驾驶证能开C6轻型牵引挂车吗？", ["可以"], "能，A2准驾含C6，无需增驾"),
    (7, "增驾后有实习期吗？", ["同类", "跨", "12"], "同大类无新实习期；跨大类有12个月"),
    (10, "在高速公路应急车道违法停车记几分？", ["9分"], "9分（第九条三）"),
    (12, "代替他人接受处罚和记分牟利记几分？", ["12分"], "12分（第八条七）"),
    (13, "70周岁以上还能申请哪些准驾车型？", ["C1", "C2", "C5", "F", "能力测试"], "C1/C2/C5/F + 三力测试"),
    (14, "一只眼睛看不见能考驾照吗？", ["C3", "C4", "C5"], "C1/C2/C3/C4/C5 五种"),
    (19, "驾驶证因超期未换证被注销后还能恢复吗？", ["第七十九条", "2年"], "第七十九条，未超2年考科目一恢复"),
]


def ask(q, timeout=180):
    body = json.dumps({"question": q}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d.get("answer", ""), round(time.time() - t0, 1)


for idx, q, anchors, expect in CASES:
    print("=" * 78)
    print(f"Q{idx:02d} {q}")
    print(f"   期望: {expect}")
    for a in (1, 2):
        ans, t = ask(q)
        miss = [x for x in anchors if x not in ans]
        print(f"   --- run{a} ({t}s)  {'✅' if not miss else '❌ 缺' + str(miss)}")
        print("     " + ans.replace("\n", "\n     ")[:520])
