# -*- coding: utf-8 -*-
"""负向验证：确认「结论区判分」不是把尺子放松了。

2026-11-15 把 eval_web100.judge 的 must_not 从「全文 + 局部豁免」改成
「只在主结论区判」，自动判分从 89/97 变成 97/97。这时必须回答一个问题：
是系统真的对了，还是错误答案现在全被放过去了？

办法：喂**明知是错**的答案，判分器必须判 FAIL；再喂**正确答案却带辨析段落**
的样本，判分器必须判 PASS。两边都过，才说明口径改动是安全的。
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from eval_web100 import judge, _conclusion_zone
from web100_bank import as_dicts

RESULT = os.path.join(ROOT, "tools", "_web100_result.json")

# 直接用题库里的真 must/must_not，不手抄，避免验证脚本自己抄错标准
BANK = {d["id"]: d for d in as_dicts()}
CASES = ["W003", "W008", "W009", "W010", "W027", "W028", "W031", "W033", "W041"]

# 故意写错的「主结论」。后面接一段正确的辨析，模拟最容易蒙混过关的形态。
WRONG_HEAD = {
    # W003 是 2026-11-15 改口径时新踩的坑：首段结论对、第二段是引用辨析，
    # 结论区不能把辨析段并进来，否则这条正确答案会被判 FAIL。
    "W003": "**科目一预约考试次数分别不得超过 5 次，第 5 次不过成绩作废。**",
    "W008": "**科目四挂了要等 10 日后才能重新预约。**",
    "W009": "**考完科目一要满 30 日才能约科目二。**",
    "W010": "**考完科目一满 10 日就能约科目三。**",
    "W027": "**窄路掉头没有时间限制，慢慢做都行。**",
    "W028": "**通过限宽门没有时间限制。**",
    "W031": "**闯红灯一次记 12 分。**",
    "W033": "**醉酒驾驶机动车，注销未超过 2 年就能恢复驾驶资格。**",
    "W041": "**故意遮挡号牌一次记 12 分。**",
}

TAIL = ("\n\n依据：第四十七条等现行条文原文。\n\n"
        "⚠️ 别和相邻的规则混淆，网上流传的说法只适用于别的情形，不适用于本题。")


def main():
    if not os.path.isfile(RESULT):
        print("缺少 _web100_result.json，先跑一次 eval_web100.py")
        return 1
    rows = {r["id"]: r for r in json.load(open(RESULT, encoding="utf-8"))}

    passed = 0
    total = 0
    problems = []

    print("=" * 72)
    print("A 组 · 正确答案带辨析段落，应判 PASS（检验不会误伤）")
    print("=" * 72)
    for qid in CASES:
        r = rows.get(qid)
        q = BANK[qid]
        if not r:
            print(f"  [SKIP] {qid} 不在结果里")
            continue
        ok, miss, bad, ex = judge(r["answer"], q["must"], q["must_not"])
        total += 1
        if ok:
            passed += 1
        else:
            problems.append((qid, "正确样本被判 FAIL", miss, bad))
        print(f"  [{'OK  ' if ok else 'FAIL'}] {qid} 缺要点={miss} 结论区踩词={bad} "
              f"结论区外豁免={len(ex)}")

    print()
    print("=" * 72)
    print("B 组 · 把主结论换成错法，后面仍带辨析段，应判 FAIL（检验没放松）")
    print("=" * 72)
    for qid in CASES:
        q = BANK[qid]
        fake = WRONG_HEAD[qid] + TAIL
        ok, miss, bad, ex = judge(fake, q["must"], q["must_not"])
        total += 1
        if not ok:
            passed += 1
        else:
            problems.append((qid, "错误样本被放过", miss, bad))
        print(f"  [{'OK  ' if not ok else 'MISS'}] {qid} "
              f"抓到={bool(miss or bad)} 缺要点={miss} 结论区踩词={bad}")

    print()
    print("=" * 72)
    print("C 组 · 结论区极短也要生效（不能因为首段短就整个放弃判分）")
    print("=" * 72)
    short_cases = [
        ("**一次记 12 分。**", ["6\\s*分"], ["12\\s*分"]),
        ("**注销未超过 2 年就能恢复驾驶资格。**\n\n依据第十五条第五项。",
         ["5\\s*年"], ["2\\s*年"]),
        ("**没有时间限制。**\n\n标准里其他项目确实没写时间。", ["5\\s*分"], ["没有时间限制"]),
    ]
    for ans, must, mn in short_cases:
        ok, miss, bad, ex = judge(ans, must, mn)
        total += 1
        if not ok:
            passed += 1
        else:
            problems.append(("短结论", "短结论错误样本被放过", miss, bad))
        print(f"  [{'OK  ' if not ok else 'MISS'}] 结论区={len(_conclusion_zone(ans))}字 "
              f"抓到={bool(miss or bad)}")

    print()
    print("=" * 72)
    print(f"负向验证：{passed}/{total} 条符合预期")
    if problems:
        print("\n不符合预期的：")
        for p in problems:
            print("  ", p)
        return 1
    print("正确样本全过、错误样本全被抓——改判分口径没有把尺子放松。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
