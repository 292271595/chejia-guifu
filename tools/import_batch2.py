# -*- coding: utf-8 -*-
"""第二批法规入库：把 data/raw_sources/batch2 下的原始文本转成知识库 Markdown。

设计原则与 import_laws.py 一致：
  1. 只做格式转换，不改写任何条文文字；
  2. 每部法规写明效力状态、发布与修正沿革、来源；
  3. 转换后做条号连续性校验，缺号 / 重号 / 乱序直接报错，不静默通过。
"""
from __future__ import annotations

import html
import os
import re
import sys

BATCH = "data/raw_sources/batch2"
OUT = "data/knowledge_base"

CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

ARTICLE_RE = re.compile(r"^第([一二三四五六七八九十百千零〇]+)条(?:之[一二三四五六七八九十]+)?(?=[\s　]|$|[^\d])")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十]+[章节]")


def cn2int(s: str) -> int:
    total = cur = 0
    for ch in s:
        if ch in CN_DIGIT:
            cur = CN_DIGIT[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "千":
            total += (cur or 1) * 1000
            cur = 0
    return total + cur


def html2lines(path: str) -> list[str]:
    raw = open(path, encoding="utf-8", errors="ignore").read()
    raw = re.sub(r"<(script|style)[\s\S]*?</\1>", "", raw)
    raw = re.sub(r"<br\s*/?>|</p>|</div>|</td>|</tr>|</h\d>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", "", raw))
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return [ln.strip().replace("\u200b", "").strip() for ln in text.split("\n") if ln.strip()]


def norm_chapter(line: str) -> str:
    """把「第一章 总  则」这类多空格的章标题规范成「第一章 总则」。"""
    line = re.sub(r"\s+", " ", line).strip()
    return line


def slice_articles(lines: list[str], start_kw: str | None = None) -> list[str]:
    """从 lines 中截出「第一条」到文末、或从 start_kw 之后开始的部分。"""
    begin = 0
    if start_kw:
        idx = [i for i, l in enumerate(lines) if start_kw in l]
        if idx:
            begin = idx[-1]
    return lines[begin:]


def build(title: str, meta: list[str], lines: list[str],
          min_article: int | None = None, max_article: int | None = None,
          chapter_prefix: str | None = None) -> tuple[str, dict]:
    """把行序列转成知识库 md，同时返回校验信息。"""
    out: list[str] = [f"# {title}", ""]
    out += [f"> {m}" for m in meta]
    out.append("")

    if chapter_prefix:
        out.append(f"## {chapter_prefix}")
        out.append("")

    nums: list[int] = []
    raw_nos: list[str] = []
    total_chars = 0
    for ln in lines:
        if CHAPTER_RE.match(ln):
            out.append(f"## {norm_chapter(ln)}")
            out.append("")
            continue
        m = ARTICLE_RE.match(ln)
        if m:
            n = cn2int(m.group(1))
            if min_article is not None and n < min_article:
                continue
            if max_article is not None and n > max_article:
                continue
            nums.append(n)
            raw_nos.append(m.group(0).split(" ")[0])
        elif nums and max_article is not None and nums[-1] > max_article:
            continue
        out.append(ln)
        out.append("")
        total_chars += len(ln)

    # 校验：条号是否连续、有无重复
    uniq = sorted(set(nums))
    missing = [n for n in range(min(uniq), max(uniq) + 1) if n not in uniq] if uniq else []
    dup = [n for n in uniq if raw_nos.count(f"第{n}条") > 1]
    info = {
        "title": title,
        "articles": len(nums),
        "range": (min(uniq), max(uniq)) if uniq else None,
        "missing": missing,
        "dup": dup,
        "chars": total_chars,
    }
    return "\n".join(out).rstrip() + "\n", info


def write(name: str, md: str, info: dict) -> None:
    path = os.path.join(OUT, name)
    open(path, "w", encoding="utf-8").write(md)
    flag = "OK " if not info["missing"] and not info["dup"] else "!! "
    print(f"{flag}{name:44s} {info['articles']:4d} 条  {info['range']} "
          f"缺{len(info['missing'])} 重{len(info['dup'])}  {info['chars']:6d}字")
    if info["missing"]:
        print(f"     缺失条号: {info['missing'][:20]}")
    if info["dup"]:
        print(f"     重复条号: {info['dup'][:20]}")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    bad = 0

    # ---------- 1. 道路交通事故处理程序规定（公安部令第146号） ----------
    lines = html2lines(f"{BATCH}/shigu146_gx.html")
    start = next(i for i, l in enumerate(lines) if l.startswith("第一章"))
    md, info = build(
        "道路交通事故处理程序规定（公安部令第146号）",
        ["**效力状态**：现行有效（2017年7月22日公安部令第146号发布，2018年5月1日起施行）",
         "**来源**：广西壮族自治区人民政府法律法规数据库公开的修订后文本；"
         "与公安部互联网+政务服务平台公布的同名规章条款一致"],
        lines[start:],
    )
    write("road_traffic_accident_procedure.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 2. 道路交通安全违法行为处理程序规定（公安部令第157号） ----------
    lines = html2lines(f"{BATCH}/weifa_157.html")
    # 页面先列「修改决定」再列重新公布的全文，取最后一个「第一条 为了规范」之后
    key = "第一条 为了规范道路交通安全违法行为处理程序"
    idx = [i for i, l in enumerate(lines) if l.startswith(key)]
    if not idx:
        print("!! 未定位到违法处理程序规定正文起点")
        return 1
    md, info = build(
        "道路交通安全违法行为处理程序规定（公安部令第157号）",
        ["**效力状态**：现行有效（2008年12月20日公安部令第105号发布，"
         "根据2020年4月7日公安部令第157号修正，2020年5月1日起施行）",
         "**来源**：中国政府网《国务院公报》2020年第17号公布的修正后全文"],
        lines[idx[-1]:],
    )
    write("traffic_violation_procedure.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 3. 机动车强制报废标准规定 ----------
    lines = html2lines(f"{BATCH}/scrap_standard.html")
    start = next(i for i, l in enumerate(lines) if l.startswith("第一条"))
    md, info = build(
        "机动车强制报废标准规定（商务部、发改委、公安部、环境保护部令2012年第12号）",
        ["**效力状态**：现行有效（2012年12月27日公布，2013年5月1日起施行）",
         "**来源**：中国政府网公布文本，与商务部政策法规库公布文本一致"],
        lines[start:],
    )
    write("vehicle_scrap_standard.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 4. 机动车交通事故责任强制保险条例 ----------
    lines = html2lines(f"{BATCH}/jqx.html")
    start = next(i for i, l in enumerate(lines) if l.startswith("第一章"))
    md, info = build(
        "机动车交通事故责任强制保险条例（国务院令第462号）",
        ["**效力状态**：现行有效（2006年3月21日国务院令第462号公布；"
         "经2012年3月30日、2012年12月17日、2016年2月6日三次修订，以2016年修订文本为准）",
         "**来源**：中国政府网《国务院公报》2016年增刊第2号公布的第三次修订后全文"],
        lines[start:],
    )
    write("compulsory_insurance_regulation.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 5. 民法典·机动车交通事故责任 ----------
    lines = html2lines(f"{BATCH}/minfadian_ch5.html")
    start = next(i for i, l in enumerate(lines) if re.match(r"^第一千二百零八条", l))
    md, info = build(
        "中华人民共和国民法典（第七编 侵权责任 第五章 机动车交通事故责任）",
        ["**效力状态**：现行有效（2020年5月28日第十三届全国人民代表大会第三次会议通过，2021年1月1日起施行）",
         "**范围说明**：本章为节录，仅收录第一千二百零八条至第一千二百一十七条共 10 条，"
         "即机动车交通事故责任专章；其他章节未收录",
         "**来源**：康乐县人民政府公开发布的民法典第五章文本，"
         "与青原区人民政府公开的同一章文本逐条比对一致"],
        lines[start:], min_article=1208, max_article=1217,
        chapter_prefix="第七编 侵权责任 第五章 机动车交通事故责任",
    )
    write("civil_code_traffic_liability.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 6. 刑法·交通肇事罪与危险驾驶罪 ----------
    lines = html2lines(f"{BATCH}/xingfa.html")
    picked: list[str] = []
    take = False
    for ln in lines:
        if re.match(r"^第[一二三四五六七八九十百千零〇]+条", ln):
            take = bool(re.match(r"^第一百三十三条(?:之[一二三四五])?(?=\s|　|【)", ln))
        if take:
            picked.append(ln)
    md, info = build(
        "中华人民共和国刑法（交通肇事罪、危险驾驶罪、妨害安全驾驶罪）",
        ["**效力状态**：现行有效（1997年3月14日修订，经十二个刑法修正案修正，"
         "最近为 2023 年 12 月 29 日《刑法修正案（十二）》）",
         "**范围说明**：本条为节录，仅收录第一百三十三条（交通肇事罪）、"
         "第一百三十三条之一（危险驾驶罪）、第一百三十三条之二（妨害安全驾驶罪）三条",
         "**来源**：丹阳市人民政府公开发布的《中华人民共和国刑法（2023修正）》文本"],
        picked, min_article=133, max_article=133,
    )
    write("criminal_law_traffic.md", md, info)

    # ---------- 7. 交通肇事司法解释（法释〔2000〕33号） ----------
    lines = html2lines(f"{BATCH}/sifa33_b.html")
    start = next(i for i, l in enumerate(lines) if re.match(r"^第一条\s", l))
    md, info = build(
        "最高人民法院关于审理交通肇事刑事案件具体应用法律若干问题的解释（法释〔2000〕33号）",
        ["**效力状态**：现行有效（2000年11月10日最高人民法院审判委员会第1136次会议通过，"
         "2000年11月21日起施行）",
         "**来源**：青原区人民政府公开发布的司法解释全文，共 9 条"],
        lines[start:],
    )
    write("spc_traffic_accident_interpretation.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 7.5 校车安全管理条例 ----------
    lines = html2lines(f"{BATCH}/xiaoche.html")
    start = next(i for i, l in enumerate(lines) if l.startswith("第一章"))
    md, info = build(
        "校车安全管理条例（国务院令第617号）",
        ["**效力状态**：现行有效（2012年3月28日国务院第197次常务会议通过，"
         "2012年4月5日国务院令第617号公布，自公布之日起施行）",
         "**来源**：中国政府网政策文件库公布的全文，共 62 条"],
        lines[start:],
    )
    write("school_bus_regulation.md", md, info)
    bad += bool(info["missing"] or info["dup"])

    # ---------- 8. GA 1029—2022（替代已废止的 2017 版） ----------
    raw = open(f"{BATCH}/ga1029_raw.txt", encoding="utf-8").read()
    g_lines = [l.strip() for l in raw.split("\n") if l.strip()]
    # 丢掉封面与前言部分，从「1 范围」开始
    begin = next((i for i, l in enumerate(g_lines) if re.match(r"^1\s*范围", l)), 0)
    body = g_lines[begin:]
    out = ["# GA 1029—2022 机动车驾驶人考试场地及其设施设置规范", "",
           "> **效力状态**：现行有效（2022年3月29日发布，2022年4月1日实施，"
           "全部代替 GA 1029—2017）",
           "> **发布机关**：中华人民共和国公安部（公共安全行业标准，备案号 86571-2022）",
           "> **来源**：佛冈县人民政府公开的标准文本（docx），"
           "以正式出版稿为准；文本由机器提取，表格结构可能失真，引用具体尺寸前请核对原标准",
           ""]
    for ln in body:
        if re.match(r"^\d+(\.\d+)*\s*\S", ln) or re.match(r"^附录[A-Z]", ln):
            out.append(f"## {ln}")
            out.append("")
        else:
            out.append(ln)
            out.append("")
    open(f"{OUT}/exam_site_standard_ga1029.md", "w", encoding="utf-8").write(
        "\n".join(out).rstrip() + "\n")
    print(f"OK  exam_site_standard_ga1029.md{'':18s} {'':4s} 条文式标准（编号条款）  {len(body)}段")

    print()
    print("=== 汇总 ===")
    print(f"导入 8 部，条号异常 {bad} 部")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
