# -*- coding: utf-8 -*-
"""构建「量纲 → 数值集合」索引，供生成侧数字核对（number_guard）使用。

产物：backend/kb_index/number_index.json
  必须落在 backend 下——Dockerfile 只 COPY backend/ chroma_db/ frontend/，
  data/ 不进镜像，索引留在 data/ 等于线上没有。

为什么索引要建在「全量原文」上（2026-11-04）：
  原数字核对拿答案去**检索片段**里找依据，片段被截断到 900 字，原文数字常在
  截断之外 → 正确答案也查不到 → 只能放宽阈值，单个数字写错拦不住。
  换成全库原文后，「有没有这个数」变成确定性判断，不再受检索切分影响。

索引粒度取「量纲 × 数值」而不是「法规 × 数值」为主键：
  用户问「罚款多少」，答案写「200 元」，只要库里任何一处出现过 200 元就算有据——
  这是刻意的宽松。归属错配由句内法条锚点做二次核对（number_guard._doc_of_law），
  那种错只提示不拦，因为纯值核对无法确证"这个 200 元不是那个 200 元"。

用法：
    python tools/build_number_index.py            # 重建
    python tools/build_number_index.py --check    # 只校验，不写文件
"""
import io
import json
import os
import sys
from datetime import date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(ROOT, "data", "knowledge_base")
OUT = os.path.join(ROOT, "backend", "kb_index", "number_index.json")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from number_guard import build_index  # noqa: E402


def main():
    idx = build_index(KB_DIR)
    idx["generated_at"] = date.today().isoformat()
    n_units = len(idx["global"])
    n_vals = sum(len(v) for v in idx["global"].values())
    print(f"知识库 {len(idx['docs'])} 部法规，抽到 {idx['total']} 个数字，"
          f"归并为 {n_units} 个量纲 / {n_vals} 个不同取值")
    for u in sorted(idx["global"], key=lambda x: -len(idx["global"][x])):
        vals = idx["global"][u]
        print(f"  {u:<8} {len(vals):>4} 个   例：{'、'.join(vals[:8])}")

    # 自检：交强险限额与记分分值必须在索引里，否则说明抽取或归一有静默失败
    must = {
        "money": ["180000", "18000", "2000"],
        "point": ["12", "9", "6", "3", "1"],
        "month": ["36", "12", "6", "3"],
    }
    fails = []
    for unit, vals in must.items():
        pool = set(idx["global"].get(unit) or [])
        if not pool:
            fails.append(f"量纲 {unit} 为空")
            continue
        for v in vals:
            if v not in pool:
                fails.append(f"{unit}:{v} 缺失")
    if fails:
        print("\n自检失败：")
        for f in fails:
            print("  -", f)
        return 1

    if "--check" in sys.argv:
        print("\n自检通过（--check 模式，未写文件）")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n自检通过，已写入 {OUT}（{os.path.getsize(OUT)//1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
