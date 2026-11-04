"""
把《道路交通安全法》《道路交通安全法实施条例》官方原文导入知识库。

为什么单独写脚本而不是手写 Markdown：
  法规原文共 239 条，人工誊抄必然出错。这里只做"格式规整"，
  不改动任何条文用字，并在导入前后做三道校验（条数、条号连续性、关键条抽查）。

输入（已下载并存档，见 data/raw_sources/README.md）：
  law_raw.txt            —— 道交法 2021 修正版，取自中国法院网 docx
  regulation_body.txt    —— 实施条例 2017 修订版，取自国务院公报网页

输出：
  data/knowledge_base/road_traffic_safety_law.md
  data/knowledge_base/road_traffic_safety_regulation.md

用法：
  python tools/import_laws.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw_sources")
KB = os.path.join(ROOT, "data", "knowledge_base")

CN_NUM = "零一二三四五六七八九十百"
CHAPTER_RE = re.compile(r"^第([%s]+)([章节])\s*(.*)$" % CN_NUM)
ARTICLE_RE = re.compile(r"^第([%s]+)条\s*(.*)$" % CN_NUM)


def normalize_chapter(line: str) -> str:
    """'第一章 总  则' -> '第一章 总则'；'第一节 机 动 车' -> '第一节 机动车'"""
    m = CHAPTER_RE.match(line.strip())
    if not m:
        return line.strip()
    num, kind, title = m.groups()
    title = re.sub(r"[\s\u3000]+", "", title)
    return f"第{num}{kind} {title}".strip()


def split_articles(lines):
    """把正文行切成 (章节标题列表, 段落列表)，段落以条为单位。

    返回 [(chapter_path, first_line, body_lines), ...]
    """
    blocks = []
    chapter, section = "", ""
    cur = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        m_ch = CHAPTER_RE.match(line.strip())
        if m_ch:
            if cur:
                blocks.append(cur)
                cur = None
            head = normalize_chapter(line)
            if m_ch.group(2) == "章":
                chapter, section = head, ""
            else:
                section = head
            continue
        m_art = ARTICLE_RE.match(line.strip())
        if m_art:
            if cur:
                blocks.append(cur)
            cur = {
                "chapter": chapter,
                "section": section,
                "head": line.strip(),
                "body": [],
            }
        elif cur is not None:
            cur["body"].append(line.strip())
    if cur:
        blocks.append(cur)
    return blocks


def render(title: str, meta_lines, blocks) -> str:
    out = [f"# {title}", ""]
    for m in meta_lines:
        out.append(f"> {m}")
    out.append("")
    last_chapter = last_section = None
    for b in blocks:
        if b["chapter"] != last_chapter:
            if last_chapter is not None:
                out.append("")
            out.append(f"## {b['chapter']}")
            out.append("")
            last_chapter, last_section = b["chapter"], None
        if b["section"] and b["section"] != last_section:
            out.append(f"### {b['section']}")
            out.append("")
            last_section = b["section"]
        out.append(b["head"])
        for ln in b["body"]:
            out.append(ln)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def verify(blocks, expect, name):
    nums = []
    for b in blocks:
        m = ARTICLE_RE.match(b["head"])
        if m:
            nums.append(cn2int(m.group(1)))
    ok = True
    if len(nums) != expect:
        print(f"  ✗ {name} 条数 {len(nums)}，期望 {expect}")
        ok = False
    missing = [n for n in range(1, expect + 1) if n not in nums]
    if missing:
        print(f"  ✗ {name} 缺条号: {missing}")
        ok = False
    if nums != sorted(nums):
        print(f"  ✗ {name} 条号未递增")
        ok = False
    if len(set(nums)) != len(nums):
        print(f"  ✗ {name} 存在重复条号")
        ok = False
    if ok:
        print(f"  ✓ {name}: {len(nums)} 条，号码连续无缺无重")
    return ok


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


def build_law():
    src = open(os.path.join(RAW, "law_raw.txt"), encoding="utf-8").read().split("\n")
    # 0 行是标题，1 行是制定/修正沿革，2~17 行是目录，从第一个正文"第一章"开始
    title = src[0].strip()
    history = src[1].strip()
    # 目录里的"第一章 总则"后面紧跟的是"第二章"，正文里的后面紧跟"第一条"，
    # 用这个特征跳过目录，避免把目录条目当成章节。
    body_start = next(
        i for i, l in enumerate(src)
        if i > 2 and CHAPTER_RE.match(l.strip())
        and i + 1 < len(src) and ARTICLE_RE.match(src[i + 1].strip())
    )
    blocks = split_articles(src[body_start:])
    meta = [
        "**效力状态**：现行有效（2021年4月29日第三次修正）",
        f"**制定与修正**：{history}",
        "**来源**：中国法院网公布的《中华人民共和国道路交通安全法》（2021年修正）文本，"
        "已与蓬安县人民政府公开的 2021 年修正版逐条比对（124 条全部一致，仅纠正第 30 条「坑漕」为「坑槽」）",
    ]
    text = render(title, meta, blocks)
    path = os.path.join(KB, "road_traffic_safety_law.md")
    open(path, "w", encoding="utf-8").write(text)
    print(f"  写入 {os.path.relpath(path, ROOT)}  字符数 {len(text)}")
    return blocks, 124


def build_regulation():
    src = open(os.path.join(RAW, "regulation_body.txt"), encoding="utf-8").read().split("\n")
    blocks = split_articles(src)
    # 前三条规定总则，原网页正文起始处缺"第一章 总则"行，这里按官方目录补回
    for b in blocks[:3]:
        b["chapter"] = "第一章 总则"
    title = "中华人民共和国道路交通安全法实施条例"
    meta = [
        "**效力状态**：现行有效（2017年10月7日修订）",
        "**制定与修正**：2004年4月30日中华人民共和国国务院令第405号公布，"
        "根据2017年10月7日《国务院关于修改部分行政法规的决定》修订",
        "**来源**：中国政府网国务院公报（2019年第1号增刊）公布文本，"
        "已与镇赉县人民政府公开的 2017 年修订版逐条比对（115 条全部一致）",
    ]
    text = render(title, meta, blocks)
    path = os.path.join(KB, "road_traffic_safety_regulation.md")
    open(path, "w", encoding="utf-8").write(text)
    print(f"  写入 {os.path.relpath(path, ROOT)}  字符数 {len(text)}")
    return blocks, 115


def main():
    print("[1/3] 导入道路交通安全法")
    law_blocks, law_n = build_law()
    ok1 = verify(law_blocks, law_n, "道路交通安全法")

    print("[2/3] 导入实施条例")
    reg_blocks, reg_n = build_regulation()
    ok2 = verify(reg_blocks, reg_n, "实施条例")

    print("[3/3] 关键条抽查")
    checks = [
        (law_blocks, "第一百零一条", "吊销机动车驾驶证"),
        (law_blocks, "第一百零一条", "终生不得重新取得"),
        (law_blocks, "第九十九条", "二百元以上二千元以下罚款"),
        (law_blocks, "第二十四条", "累积记分制度"),
        (law_blocks, "第七十六条", "机动车一方不承担赔偿责任"),
        (reg_blocks, "第九十一条", "确定当事人的责任"),
        (reg_blocks, "第九十二条", "逃逸的当事人承担全部责任"),
        (reg_blocks, "第十六条", "安全技术检验"),
    ]
    bad = 0
    def index_by_article(blocks):
        d = {}
        for b in blocks:
            m = ARTICLE_RE.match(b["head"])
            if m:
                d[f"第{m.group(1)}条"] = b
        return d
    idx_law = index_by_article(law_blocks)
    idx_reg = index_by_article(reg_blocks)
    for blocks, art, kw in checks:
        idx = idx_law if blocks is law_blocks else idx_reg
        b = idx.get(art)
        text = b["head"] + "".join(b["body"]) if b else ""
        hit = kw in text
        print(f"  {'✓' if hit else '✗'} {art} 含「{kw}」")
        bad += 0 if hit else 1

    if not (ok1 and ok2) or bad:
        print("\n导入失败，请检查上方输出")
        return 1
    print("\n全部校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
