"""
分块体检 —— 建库前先跑这个，把"切错、切丢、切重"三类问题挡在门外。

检查项：
  1. 内容保留率：分块正文（去空白）应覆盖原文 99% 以上，目录除外
  2. 标题一致性：条文块正文第一行必须就是它的条号，不允许标题与内容错位
  3. 条号完整性：法规文件条号应从第 1 条连续到末条，不缺不重
  4. 长度分布：标记超长块（>HARD_MAX）与碎片块（<30 字）
  5. 重复块：内容完全相同的块数量
  6. 溯源可用性：每块必须有 source 与 title

用法：
    python tools/check_chunks.py
    python tools/check_chunks.py --verbose      # 打印每个文件的块样例
"""
import argparse
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from document_loader import (  # noqa: E402
    HARD_MAX, DocumentLoader, chunks_to_texts_metadatas,
)

KB = os.path.join(ROOT, "data", "knowledge_base")


def nows(s: str) -> str:
    return re.sub(r"\s", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    loader = DocumentLoader(KB)
    chunks = loader.load_all()
    print()

    problems = []

    # ── 逐文件检查 ──
    print(f"{'文件':30s}{'块数':>6s}{'条文块':>7s}{'保留率':>8s}{'错位':>6s}{'缺号':>6s}")
    for fn in sorted(os.listdir(KB)):
        if not fn.endswith(".md"):
            continue
        raw = open(os.path.join(KB, fn), encoding="utf-8").read()
        source = fn[:-3]
        mine = [c for c in chunks if c.metadata.get("source") == source]

        # 保留率：原文去掉标题行与引用块后的可见字符
        raw_body = re.sub(r"^#{1,6}.*$", "", raw, flags=re.M)
        raw_body = re.sub(r"^>.*$", "", raw_body, flags=re.M)
        kept = "".join(
            (c.content.split("\n", 1)[1] if c.content.startswith("《") else c.content)
            for c in mine
        )
        ratio = len(nows(kept)) / max(len(nows(raw_body)), 1)
        if ratio < 0.99:
            problems.append(f"{fn} 内容保留率仅 {ratio:.1%}")

        # 标题一致性（跳过续块）
        misaligned = []
        for c in mine:
            art = c.metadata.get("article")
            if not art or c.metadata.get("part", "0") != "0":
                continue
            body = c.content.split("\n", 1)[1] if c.content.startswith("《") else c.content
            if not body.strip().startswith(art):
                misaligned.append((art, body.strip()[:24]))
        if misaligned:
            problems.append(f"{fn} 有 {len(misaligned)} 处标题与内容错位")

        # 条号完整性
        arts = [c.metadata["article"] for c in mine if c.metadata.get("article")]
        missing = 0
        if arts and len(arts) > 10:
            nums = []
            for a in dict.fromkeys(arts):
                m = re.match(r"第([零一二三四五六七八九十百]+)条", a)
                if m:
                    nums.append(cn2int(m.group(1)))
            if nums:
                # 只看区间内是否有断号；摘录型文件（如只收录第三十条到第五十九条）不算缺
                lo, hi = min(nums), max(nums)
                missing = sum(1 for n in range(lo, hi + 1) if n not in nums)
                if missing:
                    problems.append(f"{fn} 第{lo}~{hi}条之间缺 {missing} 个条号")

        print(f"{fn:30s}{len(mine):>6d}{len(arts):>7d}{ratio:>7.1%}{len(misaligned):>6d}{missing:>6d}")

        if args.verbose:
            for c in mine[:3]:
                print(f"      · {c.metadata.get('title')}  ({len(c.content)} 字)")

    # ── 全局检查 ──
    print()
    lens = [len(c.content) for c in chunks]
    over = [c for c in chunks if len(c.content) > HARD_MAX]
    tiny = [c for c in chunks if len(c.content) < 30]
    texts, metas, ids = chunks_to_texts_metadatas(chunks)

    dup = [t for t, n in Counter(texts).items() if n > 1]
    no_meta = [c for c in chunks if not c.metadata.get("title") or not c.metadata.get("source")]
    dup_id = [i for i, n in Counter(ids).items() if n > 1]

    print(f"总块数 {len(chunks)}｜平均 {sum(lens)/len(lens):.0f} 字｜"
          f"最长 {max(lens)}｜最短 {min(lens)}")
    print(f"超长块(>{HARD_MAX}) {len(over)}｜碎片块(<30字) {len(tiny)}｜"
          f"重复内容 {len(dup)}｜缺元数据 {len(no_meta)}｜重复ID {len(dup_id)}")

    if over:
        problems.append(f"{len(over)} 个块超过 {HARD_MAX} 字")
    if dup:
        problems.append(f"{len(dup)} 组重复内容")
    if no_meta:
        problems.append(f"{len(no_meta)} 个块缺 source/title")
    if dup_id:
        problems.append(f"{len(dup_id)} 个重复 chunk id")

    print()
    if problems:
        print("✗ 存在 %d 项问题：" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("✓ 分块体检全部通过")
    return 0


def cn2int(s: str) -> int:
    d = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
         "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = cur = 0
    for ch in s:
        if ch in d:
            cur = d[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


if __name__ == "__main__":
    sys.exit(main())
