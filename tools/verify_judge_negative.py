# -*- coding: utf-8 -*-
"""负向验证：确认判分器不是"把标准改松了"。

背景：100 题压测中我改了 6 轮断言，每次都是"答案对、断言错"。改完之后
100% 通过，这时必须回答一个问题——是我把尺子放松了，还是系统真的对了？

办法：喂**明知是错**的答案，判分器必须判 FAIL。抓不住就说明断言形同虚设。
"""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"C:\RAG\rag-vehicle-system"

PARTS = ["data/eval_100_part1.json", "data/eval_100_part2.json"]

# (题号, 故意写错的答案, 说明)
WRONG = [
    ("S01", "闯红灯一次记 3 分，处 200 元罚款。", "闯红灯正确是 6 分"),
    ("S02", "故意遮挡机动车号牌，一次记 12 分。", "遮挡号牌正确是 9 分"),
    ("S03", "占用应急车道行驶，一次记 12 分。", "应急车道正确是 6 分"),
    ("S04", "在高速公路上违法停车，一次记 6 分。", "高速违停正确是 9 分"),
    ("S05", "驾驶人未按规定系安全带，一次记 3 分。", "安全带正确是 1 分"),
    ("S08", "驾驶与准驾车型不符的机动车，一次记 12 分。", "准驾不符正确是 9 分"),
    ("S17", "学法减分一个记分周期最多可以扣减 12 分。", "学法减分上限是 6 分"),
    ("S21", "醉酒驾驶营运机动车，5 年内不得重新取得驾驶证。", "营运醉驾是 10 年"),
    ("S22", "醉酒驾驶机动车，10 年内不得重新取得驾驶证。", "普通醉驾是 5 年"),
    ("S13", "记分周期为 1 年，满分为 12 分。", "周期应表述为 12 个月"),
    ("L03", "70 岁可以申请普通二轮摩托车驾驶证。", "70 周岁不能再申请 D/E"),
    ("L13", "初次申领的驾驶证有效期为 10 年。", "初次是 6 年"),
    ("L14", "增驾没有实习期。", "增驾后有 12 个月实习期"),
    ("C04", "期满换证可以提前 30 日内办理。", "应为期满前 90 日内"),
    ("C15", "延期换证最长可以延期 1 年。", "延期最长 3 年"),
    ("C16", "延期换证期间可以驾驶机动车。", "延期期间不得驾驶"),
    ("R02", "车辆过户应当在交付之日起 90 日内办理。", "应为 30 日内"),
    ("R08", "号牌互换一年可以办多次，不限次数。", "一年只能互换一次"),
    ("R11", "加装车顶行李架需要办理变更登记。", "应为变更备案，不是变更登记"),
    ("R19", "车架号锈蚀看不清的，需要办理变更登记。", "应为变更备案"),
    ("N05", "第 6 年的车不需要上线检测。", "第 6 年必须上线"),
    ("N06", "超过 10 年的非营运小客车每半年检验一次。", "超过 10 年是每年一次"),
    ("N10", "逾期未年检上路的，一次记 12 分。", "应为 1 分（营运类 3 分）"),
    ("A03", "交通事故致人死亡负同等责任，一律构成交通肇事罪。", "同等责任需死亡三人以上才构罪"),
    ("A05", "交通肇事逃逸致人死亡的，处三年以下有期徒刑。", "应为七年以上"),
    ("I01", "交强险无责赔付限额合计 11000 元。", "现行为 19900 元"),
    ("S24", "造成事故后逃逸尚不构成犯罪的，吊销机动车驾驶证并终生禁驾。", "不构罪不吊销、不禁驾"),
    ("I02", "交警判我无责，交强险不用赔。", "无责也要在无责限额内赔"),
]


def load():
    m = {}
    for p in PARTS:
        with io.open(os.path.join(ROOT, p), encoding="utf-8") as f:
            for q in json.load(f)["questions"]:
                m[q["id"]] = q
    return m


def main():
    bank = load()
    ok = 0
    miss = []
    for qid, wrong, why in WRONG:
        q = bank.get(qid)
        if not q:
            print(f"[SKIP] {qid} 不在题库")
            continue
        hit_must = [p for p in q["must"] if not re.search(p, wrong)]
        hit_bad = [p for p in q["must_not"] if re.search(p, wrong)]
        caught = bool(hit_must or hit_bad)
        flag = "OK  " if caught else "MISS"
        if caught:
            ok += 1
        else:
            miss.append((qid, wrong, why))
        print(f"[{flag}] {qid}  {'被抓（缺要点）' if hit_must else ''}"
              f"{'被抓（踩错法）' if hit_bad else ''}")
        if not caught:
            print(f"        错误答案：{wrong}")
            print(f"        应判错理由：{why}")

    print("\n" + "=" * 70)
    print(f"负向验证：{ok}/{len(WRONG)} 条错误答案被抓住")
    if miss:
        print("\n判分器漏掉的（说明这些题断言太松，需要收紧）：")
        for qid, w, why in miss:
            print(f"  {qid}: {why}")
    else:
        print("全部抓住——判分器有效，100% 不是靠放松标准换来的。")


if __name__ == "__main__":
    main()
