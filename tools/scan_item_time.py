# -*- coding: utf-8 -*-
"""
GA 1026「项目完成时间」全文对账
================================
背景（2026-11-13）：用户反馈「考试通过限宽门时间是多少」答错。
根因不是写错了某个数字，而是**核对方法本身有漏洞**：
我当时拿 grep 搜 "min|秒|时间超过" 去查条文，而源文件是 OCR 排版，
「0.5min」被断行成 "0.5mi" + "n"，grep 一条都不命中，
于是把「限宽门 0.5min」「窄路掉头 5min」都当成了"没有时间限制"。

grep 查条文对 OCR 文本天然不可靠。这个脚本改用**按项目段落切分**的方式，
把每个项目「操作要求」段里的时限抽出来，再和 fact_exam.ITEM_TABLE 的答案对账。
新项目、漏项目、单位换算错，都会在这里现形。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

KB = ROOT / "data" / "knowledge_base" / "exam_methods.md"

# 科目二带完成时限/操作要求的项目（按源文件出现顺序）
PROJECTS = [
    "倒车入库", "桩考", "坡道定点停车和起步", "侧方停车",
    "曲线行驶", "直角转弯", "通过单边桥", "通过限宽门", "窄路掉头",
]


def load_text() -> str:
    t = KB.read_text(encoding="utf-8")
    # OCR 断行修复：这里的每一次漏检都出自这一类断行
    #   "0.5mi\nn"  → 0.5min   （限宽门，就是这次用户反馈的那条）
    #   "1.\n5min"  → 1.5min   （侧方停车"其他车型"那一档，不修就只剩重挂的 2min）
    t = re.sub(r"mi\s*\n\s*n\b", "min", t)
    t = re.sub(r"(\d)\s*\.\s*\n\s*(\d)", r"\1.\2", t)
    t = re.sub(r"(\d)\s*\n\s*(min|s)\b", r"\1\2", t)
    return t


def split_sections(text: str) -> dict:
    """按项目标题切段。标题是行首、单独成行、无缩进的中文项目名。"""
    lines = text.split("\n")
    marks = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s in PROJECTS and not ln.startswith((" ", "\t")):
            marks.append((i, s))
    out = {}
    for k, (i, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        # 同名的第二段（如桩考分车型）合并进来
        body = "\n".join(lines[i:end])
        out.setdefault(name, []).append(body)
    return out


def extract_times(body: str):
    """抽操作要求段里的完成时限。返回 (分钟列表, 秒列表)。

    不能只匹配"项目完成时间…不应超过X"：侧方停车那一句是
    "项目完成时间重型牵引挂车不应超过2min，其他车型不应超过1.5min"，
    第二个数字前面没有"项目完成时间"前缀，只认前缀会丢掉"其他车型"那一档。
    """
    mins, secs = [], []
    # 操作要求段内所有"不应超过 X min"都算完成时限（该段不会出现"米"这类单位）
    for m in re.finditer(r"不应超过\s*(\d+(?:\.\d+)?)\s*min\b", body):
        mins.append(float(m.group(1)))
    for m in re.finditer(r"起步时间[^。；]{0,24}?不应超过\s*(\d+(?:\.\d+)?)\s*s(?!\w)", body):
        secs.append(float(m.group(1)))
    return mins, secs


def probe_answer(name: str):
    """拿事实层对「X 时间是多少」的实际回答，不再直接读 ITEM_TABLE。"""
    import fact_table as FT
    q = f"{name}时间是多少"
    r = FT.lookup(q)
    return (r or {}).get("fact"), (r or {}).get("answer", "")


def main():
    text = load_text()
    secs = split_sections(text)
    print("=" * 74)
    print("GA 1026「项目完成时间」原文抽取 —— 按段落，不用 grep")
    print("=" * 74)

    std = {}
    for name in PROJECTS:
        bodies = secs.get(name)
        if not bodies:
            print(f"  !! 源文件里找不到项目段落：{name}")
            continue
        all_min, all_sec = [], []
        for b in bodies:
            mn, sc = extract_times(b)
            all_min += mn
            all_sec += sc
        std[name] = (sorted(set(all_min)), sorted(set(all_sec)))
        if all_min or all_sec:
            parts = []
            if all_min:
                parts.append(" / ".join(f"{x:g}min" for x in sorted(set(all_min))))
            if all_sec:
                parts.append(" / ".join(f"{x:g}s" for x in sorted(set(all_sec))))
            print(f"  {name:<12} {' + '.join(parts)}")
        else:
            print(f"  {name:<12} （操作要求段无完成时限）")

    print("\n" + "=" * 74)
    print("与事实层答案对账")
    print("=" * 74)

    bad = 0
    for name in PROJECTS:
        mins, s_list = std.get(name, ([], []))
        fact, ans = probe_answer(name)
        if not fact:
            # 无时限项目也可能被分支认下（项目名匹配），所以只提示不多判
            print(f"  ?? {name:<12} 未命中事实层")
            bad += 1
            continue

        if not mins and not s_list:
            ok = "没有时间限制" in ans
            flag = "OK " if ok else "错 "
            print(f"  {flag}{name:<12} 原文无时限 → 答案{'说明无限制' if ok else '未说明无限制'}")
            if not ok:
                bad += 1
            continue

        # 原文有时限：答案必须能对上数字（分钟或换算后的秒）
        wants = [f"{x:g}" for x in mins] + [f"{x * 60:g}" for x in mins] + [f"{x:g}" for x in s_list]
        hit = any(w in ans for w in wants)
        flag = "OK " if hit else "错 "
        print(f"  {flag}{name:<12} 原文={'/'.join(wants)} → {'答案已对上' if hit else '答案对不上！'}")
        if not hit:
            bad += 1
            print(f"       实际答案：{ans[:80]}…")

    print()
    if bad:
        print(f"对账不通过：{bad} 项有问题")
        return 1
    print("对账通过：所有项目时限与原文一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
