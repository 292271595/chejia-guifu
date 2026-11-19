# -*- coding: utf-8 -*-
"""事实层覆盖体检：哪些「法规里有明确答案」的条款还没进事实层。

为什么需要这件工具
──────────────────
事实层是**列举式**的：每条答案都得有人写一个处理器、配一组入口词。
法规里有明确答案，不等于系统答得出来——两者之间隔着一份没人维护的清单。
结果就是永远被动：用户问到、答错了、才补一条，补完还是不知道下一块空白在哪。

这个脚本把「该覆盖什么」变成可枚举的：从知识库 25 部法规里逐条抽出条文，
按「是否含明确要素」（年龄、期限、材料、罚则、数值）筛出**答得出确定答案**的条款，
再拿后端 fact_*.py 里出现过的条文原文去比对，剩下来的就是缺口。

口径与限制（务必先读）
──────────────────────
- 判定「已覆盖」用的是**条文原文前 12 字是否出现在 fact 代码里**。这是下界：
  改写式回答（把原文嚼碎重写、不逐字引用）会被误判成缺口。
- 反过来也有误判：不同法规常有相同的起句（如「机动车发生交通事故造成」），
  一条命中会让同起句的几条都算覆盖。
- 所以这个数字只能看**趋势和分布**（哪部法规薄、哪类要素薄），
  不能当 KPI，也不能拿来宣称「覆盖率 13%」这种精确结论。
- 本脚本只读不写业务数据，产出 markdown 报告，供人工挑批补覆盖。

用法
────
    python tools/scan_fact_coverage.py              # 生成报告
    python tools/scan_fact_coverage.py --top 40     # 每部法规列多少条缺口
    python tools/scan_fact_coverage.py --stdout     # 只打印不写文件
"""
import argparse
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(ROOT, "data", "knowledge_base")
OUT = os.path.join(ROOT, "data", "evaluation", "fact_coverage.md")

CN_NUM = "零一二三四五六七八九十百千两"
# 带「之一/之二」：刑法、部分部令用这种编号，漏掉会把几条并成一条
ART_RE = re.compile(r"第([" + CN_NUM + r"]+)条(之[" + CN_NUM + r"]+)?")

# 术语表不是法规，逐条抽会把整张对照表当条文
SKIP_FILES = {"colloquial_terms.md"}

# 「有明确答案」的四种要素：出现任意一种，就说明这条能给出确定结论，
# 属于事实层该管的事；纯原则性表述（如「应当遵守法律法规」）不算。
ELEMENT_RULES = [
    ("年龄", re.compile(r"周岁|年龄|岁以上|岁以下")),
    ("期限", re.compile(r"日内|个工作日内|个月|有效期|届满|超过\s*\d|之内")),
    ("材料", re.compile(r"下列|材料|提交|携带|证明|凭证")),
    ("罚则", re.compile(r"罚款|记[0-9一二三四五六七八九十]+分|吊销|扣留|拘留|警告|撤销")),
    ("数值", re.compile(r"\d+\s*(元|米|公里|千米|%|分|座|人|年|日|小时)")),
]


def load_fact_source() -> str:
    src = []
    for p in sorted(glob.glob(os.path.join(ROOT, "backend", "fact_*.py"))):
        try:
            src.append(open(p, encoding="utf-8", errors="replace").read())
        except Exception:
            continue
    return "\n".join(src)


def parse_articles(path: str) -> list:
    """按「第X条」切条文，同一部法规内条号去重，保留**最长**那段。

    为什么保留最长而不是第一次出现：文件开头的「范围说明」常常写
    「仅收录第一百三十三条、第一百三十三条之一」，那句话先出现、极短，
    按首次出现去重会把正文整条挤掉，最后得出「这部法规没有条款」的假结论。
    """
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return []
    marks = list(ART_RE.finditer(raw))
    best = {}
    for i, m in enumerate(marks):
        no = m.group(0)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        text = raw[m.end():end].strip()
        text = re.sub(r"^[、：:．.\s]+", "", text)
        text = re.sub(r"\s+", " ", text)
        if len(text) < 12:
            continue
        if no not in best or len(text) > len(best[no]):
            best[no] = text
    return [{"no": no, "text": t} for no, t in best.items()]


def elements_of(text: str) -> list:
    return [name for name, rx in ELEMENT_RULES if rx.search(text)]


def covered(text: str, src: str) -> bool:
    """条文原文前 12 字出现在 fact 代码里，就当作已被引用（下界判定）。"""
    head = text[:12]
    return len(head) >= 8 and head in src


def scan(top: int) -> dict:
    src = load_fact_source()
    files = []
    skipped = []
    for p in sorted(glob.glob(os.path.join(KB_DIR, "*.md"))):
        name = os.path.basename(p)
        if name in SKIP_FILES:
            skipped.append((name, "术语对照表，不是法规"))
            continue
        arts = parse_articles(p)
        if not arts:
            # 国标/行标（GA、GB）用的是章节号（5.2.3），没有「第X条」，
            # 抽不出来不等于没内容——这类文件是覆盖统计的盲区，必须点名。
            skipped.append((name, "无「第X条」编号（国标/行标按章节编排）"))
            continue
        solid = [a for a in arts if elements_of(a["text"])]
        hit = [a for a in solid if covered(a["text"], src)]
        gaps = [a for a in solid if not covered(a["text"], src)]
        for a in gaps:
            a["elements"] = elements_of(a["text"])
        files.append({"name": name, "arts": len(arts), "solid": len(solid),
                      "hit": len(hit), "gaps": gaps, "top": top})
    return {"files": files, "skipped": skipped}


def render(data: dict) -> str:
    files = data["files"]
    tot_art = sum(f["arts"] for f in files)
    tot_solid = sum(f["solid"] for f in files)
    tot_hit = sum(f["hit"] for f in files)
    tot_gap = tot_solid - tot_hit
    lines = []
    lines.append("# 事实层覆盖体检")
    lines.append("")
    lines.append("这份报告回答一个问题：法规里有明确答案的条款，有多少还没进事实层。")
    lines.append("")
    lines.append(f"- 参与统计的法规：{len(files)} 部")
    lines.append(f"- 抽出的条款：{tot_art} 条（同一部法规内条号去重）")
    lines.append(f"- 其中含明确要素（年龄/期限/材料/罚则/数值）：{tot_solid} 条")
    lines.append(f"- 条文原文在事实层代码中出现过：{tot_hit} 条")
    lines.append(f"- 缺口：{tot_gap} 条")
    lines.append("")
    lines.append("口径提醒：判定用的是「条文前 12 字是否逐字出现在 fact_*.py 里」，")
    lines.append("所以是**下界**——改写式回答会被算成缺口；相同起句也会被连带算成已覆盖。")
    lines.append("看分布和趋势，不要当精确覆盖率用。")
    lines.append("")
    lines.append("## 一、按法规看厚薄")
    lines.append("")
    ranked = sorted(files, key=lambda f: (-len(f["gaps"]), f["name"]))
    for f in ranked:
        rate = (f["hit"] / f["solid"] * 100) if f["solid"] else 0.0
        lines.append(f"- **{f['name']}**：条款 {f['arts']} 条，含明确要素 {f['solid']} 条，"
                     f"已引用 {f['hit']} 条，缺口 {len(f['gaps'])} 条（覆盖 {rate:.0f}%）")
    lines.append("")
    lines.append("## 二、缺口最多的地方（按法规，逐条列）")
    lines.append("")
    for f in ranked[:6]:
        if not f["gaps"]:
            continue
        lines.append(f"### {f['name']}（缺口 {len(f['gaps'])} 条）")
        lines.append("")
        for a in f["gaps"][:f["top"]]:
            el = "/".join(a["elements"])
            lines.append(f"- {a['no']}［{el}］{a['text'][:60]}…")
        lines.append("")
    lines.append("## 三、没纳入统计的文件（盲区，别忽略）")
    lines.append("")
    for name, why in data.get("skipped", []):
        lines.append(f"- `{name}`：{why}")
    lines.append("")
    lines.append("国标/行标这几部里恰恰装着考试项目的硬数值（限宽门时长、侧方停车时限等），")
    lines.append("事实层对它们是有覆盖的，只是这套扫描按「第X条」切分，看不见章节号，")
    lines.append("所以上面那张表对考试类的覆盖是**低估**的。")
    lines.append("")
    lines.append("## 四、下一步怎么用这份清单")
    lines.append("")
    lines.append("不要按这份报告从上到下补。先拿真实问句日志对照：")
    lines.append("哪条缺口被用户真问过、问过几次，就先补哪条。没有真实需求的条款可以放着，")
    lines.append("生成层能兜住；但被问过三次以上还答错的，就是硬缺口。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25, help="每个法规最多列多少条缺口")
    ap.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    data = scan(args.top)
    text = render(data)
    if args.stdout:
        print(text)
        return 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    files = data["files"]
    solid = sum(f["solid"] for f in files)
    hit = sum(f["hit"] for f in files)
    print(f"已生成：{args.out}")
    print(f"{len(files)} 部法规｜含明确要素 {solid} 条｜已引用 {hit} 条｜缺口 {solid - hit} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
