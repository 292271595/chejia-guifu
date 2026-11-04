# -*- coding: utf-8 -*-
"""数字核对（number_guard）的验证：既测它抓不抓得住错，也测它会不会冤枉对的。

三组用例，缺一不可：

  A 正查（不误报）  116 条已人工确认正确的答案 → 应该 0 个数字被判无据。
                    这一组不过，说明核对过严，用户会看到满屏"数值可能不准确"。

  B 硬篡改（库外值）把答案里的每个数字换成知识库里根本不存在的值 → 必须逐个抓住。
                    这一组才是"单个数字写错能不能拦"的真答案。

  C 软篡改（库内错配）把 12 分改成 6 分（两者库里都有）→ 预期抓不住。
                    这一组是刻意的反向记录：它量化了本层的能力边界，
                    以后有人问"数字核对是不是万无一失"，看这组数字就知道不是。

用法：
    python tools/test_number_guard.py            # 全量
    python tools/test_number_guard.py --quick    # 每组取前 20 条
"""
import io
import json
import os
import re
import sys
from decimal import Decimal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import number_guard as ng  # noqa: E402

RESULT = os.path.join(ROOT, "tools", "_eval100_result.json")


def load_answers():
    with open(RESULT, encoding="utf-8") as f:
        rows = json.load(f)
    return [(r["id"], r["q"], r.get("answer") or "") for r in rows if r.get("answer")]


def _outer_value(idx, unit, items):
    """造一个"库里和答案里都没有"的值，避免篡改值恰好被当成合计或巧合命中"""
    pool = {Decimal(v) for v in (idx.glob.get(unit) or [])}
    pool |= {Decimal(it["value"]) for it in items if it["unit"] == unit}
    base = max(pool) if pool else Decimal(10)
    cand = base * 2 + 13
    while cand in pool:
        cand += 1
    return cand


def case_positive(idx, answers):
    bad = []
    total = 0
    for rid, q, ans in answers:
        res = idx.check(ans)
        total += res["total"]
        for it in res["ungrounded"]:
            bad.append((rid, q, it["raw"], it["key"]))
    return total, bad


def case_rewrite(idx, answers, mode="outer"):
    """逐个数字改写，看能否抓住被改的那一个"""
    hits, missed = 0, []
    total = 0
    for rid, q, ans in answers:
        items = ng.extract_numbers(ans)
        for i, it in enumerate(items):
            if mode == "outer":
                new_val = _outer_value(idx, it["unit"], items)
            else:
                # 库内值：换成本量纲下另一个真实存在的数值（模拟"12分→6分"）
                pool = [v for v in (idx.glob.get(it["unit"]) or []) if v != it["value"]]
                if it["unit_raw"] == "年":
                    # 量纲已归一到「月」，换值后要能整除回整年，否则"36年"会变成
                    # 库里不存在的 432 个月，成了假检出
                    pool = [v for v in pool if Decimal(v) % 12 == 0]
                if not pool:
                    continue
                new_val = Decimal(pool[(i * 7 + 3) % len(pool)])
                if it["unit_raw"] == "年":
                    new_val = new_val / 12
            new_raw = f"{ng._fmt(new_val)}{it['unit_raw']}"
            tampered = ans[:it["pos"]] + new_raw + ans[it["pos"] + len(it["raw"]):]
            total += 1
            res = idx.check(tampered)
            # 用位置匹配：被篡改的那一个数字位置不变（前面的字符没动），
            # 不能比值——「1 年」改成「733年」后归一值是 8796，与 733 不相等
            got = any(b["pos"] == it["pos"] for b in res["ungrounded"])
            if got:
                hits += 1
            else:
                missed.append((rid, q, it["raw"], f"→{new_raw}"))
    return total, hits, missed


def main():
    quick = "--quick" in sys.argv
    answers = load_answers()
    if quick:
        answers = answers[:20]
    idx = ng.index()
    if not idx.load():
        print("索引加载失败")
        return 1

    print(f"样本：{len(answers)} 条已确认正确的答案\n")

    total, bad = case_positive(idx, answers)
    print(f"[A] 正查：{total} 个法规数字，误判无据 {len(bad)} 个")
    for b in bad[:10]:
        print(f"     ✗ {b[0]} {b[1]} -> {b[2]} ({b[3]})")

    total, hits, missed = case_rewrite(idx, answers, "outer")
    print(f"\n[B] 硬篡改（换成库里不存在的值）：{total} 个变体，抓住 {hits} 个 "
          f"（{hits/max(1,total):.1%}）")
    for m in missed[:10]:
        print(f"     ✗ 漏 {m[0]} {m[1]} {m[2]} {m[3]}")

    total2, hits2, missed2 = case_rewrite(idx, answers, "inner")
    print(f"\n[C] 软篡改（换成本量纲下另一个真实值）：{total2} 个变体，抓住 {hits2} 个 "
          f"（{hits2/max(1,total2):.1%}）")
    print("     ↑ 这一组抓不住是设计上的边界：值级核对只能判「库里有没有」，「位置对不对」判不了")

    out = {
        "samples": len(answers),
        "positive": {"items": total, "false_alarm": len(bad), "detail": bad[:50]},
        "hard_rewrite": {"variants": total, "caught": hits, "missed": missed[:50]},
        "soft_rewrite": {"variants": total2, "caught": hits2, "missed": missed2[:50]},
    }
    with open(os.path.join(ROOT, "tools", "_number_guard_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n细项已写入 tools/_number_guard_result.json")

    ok = (len(bad) == 0) and hits == total
    print("\n结果：", "通过（无误报，硬篡改全抓）" if ok else "未达标")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
