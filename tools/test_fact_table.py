# -*- coding: utf-8 -*-
"""事实表离线自测：不联网、不走 LLM，纯查表结果核对。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

import fact_table as FT

CASES = [
    ("C1能开什么车", "SCOPE:C1"),
    ("A2准驾车型有哪些", "SCOPE:A2"),
    ("大客车能开什么车", "SCOPE:A1"),
    ("A2驾驶证能开C6轻型牵引挂车吗", "CONTAIN:A2->C6"),
    ("C1能开B2吗", "CONTAIN:C1->B2"),
    ("A1能开A2吗", "CONTAIN:A1->A2"),
    ("70周岁以上还能申请哪些准驾车型", "AGE70_APPLY"),
    ("学习驾驶证明有效期是多久", "STUDY_CERT"),
    ("增驾A1需要什么条件", "ADD:A1"),
    ("C1能直接增驾A2吗", "ADD:A2"),
    ("B2能增驾摩托车吗", "ADD:MOTO"),
    ("增驾C6要什么条件", "ADD:C6"),
    ("考A1多大年龄", "AGE_APPLY:A1"),
    ("C1年龄要求多少岁", "AGE_APPLY:C1"),
    ("63岁能开大客车吗", "AGE_DRIVE:63:A1"),
    ("62岁能开大客车吗", "AGE_DRIVE:62:A1"),
    ("66岁能开大客车吗", "AGE_DRIVE:66:A1"),
    ("70岁还能开摩托车吗", "AGE_DRIVE:70:MOTO"),
    ("68岁能开C1吗", "AGE_DRIVE:68:C1"),
    ("C6能初次申领吗", "FIRST_APPLY:C6"),
    ("B2能直接考吗", "FIRST_APPLY:B2"),
    ("占用应急车道行驶记几分", "SCORE:6"),
    ("高速应急车道违法停车记几分", "SCORE:9"),
    ("代替他人记分牟利记几分", "SCORE:12"),
    ("闯红灯扣几分", "SCORE:6"),
    ("不系安全带扣几分", "SCORE:1"),
    ("开车打电话扣几分", "SCORE:3"),
    ("遮挡号牌记几分", "SCORE:9"),
    ("高速倒车记几分", "SCORE:12"),
    ("醉驾吊销驾驶证多久能重考", "REVOKE:5"),
    ("醉驾营运车吊销几年", "REVOKE:10"),
    ("肇事逃逸构成犯罪还能考驾照吗", "REVOKE:FOREVER"),
    ("驾驶证被吊销多久能重考", "REVOKE:TABLE"),
    ("增驾有实习期吗", "INTERNSHIP"),
    ("实习期上高速公路有什么要求", "INTERNSHIP:HIGHWAY"),
    ("实习期是多久", "INTERNSHIP"),
    ("驾驶证因超期未换证被注销怎么恢复", "RECOVER"),
    ("红绿色盲能考驾驶证吗", "PHYSICAL:color"),
    ("一只眼睛看不见能考驾照吗", "PHYSICAL:monocular"),
    ("听力不好能考驾照吗", "PHYSICAL:hearing"),
    ("申请A1身高要求多少", "PHYSICAL:height"),
]

print("=" * 88)
print(f"{'问句':<30} {'期望':<24} {'实际':<24} 结果")
print("=" * 88)
ok = 0
fail = []
for q, expect in CASES:
    r = FT.lookup(q)
    actual = r["fact"] if r else "（未命中→走RAG）"
    if r and actual == expect:
        status = "OK"
        ok += 1
    else:
        status = "FAIL"
        fail.append((q, expect, actual))
    print(f"{q:<30} {expect:<24} {actual:<24} {status}")

print("=" * 88)
print(f"通过 {ok}/{len(CASES)}")
if fail:
    print("\n未通过明细：")
    for q, e, a in fail:
        print(f"  {q}  期望={e}  实际={a}")
