# -*- coding: utf-8 -*-
"""构建「法规名 → 条号集合」索引，供生成侧引用对账（citation_guard）使用。

产物：backend/kb_index/citation_index.json
  随 backend/ 一起进镜像（Dockerfile 只 COPY backend/ chroma_db/ frontend/，
  data/ 不进镜像，所以索引必须落在 backend 下）。

为什么需要它（2026-11-04 线上错答案例）：
  用户问「造成交通事故致人死亡，负同等以上责任如何处理」，模型给出的
  「记分办法第八条记12分」「第十四条不得申请」三处引用**全是编造的**。
  这类错误靠调 prompt 压不住——模型对小模型而言"编一个像样的条号"比
  "承认知识库没有"更省力。唯一可靠的办法是拿真实法条清单做对账。

索引粒度取「法规 × 条号」而不是「法规」：只有知道该法规到底有没有第九十九条，
才能把「引用了不存在的条号」和「引用了存在但本次未检索到的条号」区分开。

用法：
    python tools/build_citation_index.py            # 重建
    python tools/build_citation_index.py --check    # 只校验，不写文件
"""
import io
import json
import os
import re
import sys
from datetime import date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(ROOT, "data", "knowledge_base")
OUT = os.path.join(ROOT, "backend", "kb_index", "citation_index.json")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from document_loader import cn2int  # noqa: E402  中文数字解析与建库同源，避免两套口径

_CN = "零〇一二三四五六七八九十百千"
# 「第一百三十三条之一」这种分条，单独记成 133-1
ART_RE = re.compile(rf"第([{_CN}]+)条(?:之([{_CN}]+))?")
TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)

# 别名按**文件名**配置：md 首行标题偶有「（公安部令第172号）」这类后缀，
# 用文件名做锚点才不会因标题措辞变化导致对账失配。
ALIASES = {
    "road_traffic_safety_law.md": [
        "道路交通安全法", "道交法", "交法", "交通安全法", "道路交通安全法(2021修正)",
    ],
    "road_traffic_safety_regulation.md": [
        "道路交通安全法实施条例", "实施条例", "道交法实施条例", "安全法实施条例",
    ],
    "road_traffic_accident_procedure.md": [
        "道路交通事故处理程序规定", "事故处理程序规定", "交通事故处理程序规定",
        "处理程序规定", "146号令",
    ],
    "traffic_violation_procedure.md": [
        "道路交通安全违法行为处理程序规定", "违法行为处理程序规定", "157号令",
    ],
    "traffic_violations.md": [
        "道路交通安全违法行为记分管理办法", "记分管理办法", "记分办法", "记分规定", "163号令",
    ],
    "driving_license.md": [
        "机动车驾驶证申领和使用规定", "驾驶证申领和使用规定", "驾驶证申领规定",
        "申领和使用规定", "172号令",
    ],
    "vehicle_registration.md": [
        "机动车登记规定", "登记规定", "164号令",
    ],
    "criminal_law_traffic.md": [
        "刑法", "中华人民共和国刑法", "刑法典",
    ],
    "civil_code_traffic_liability.md": [
        "民法典", "中华人民共和国民法典", "侵权责任编",
    ],
    "spc_traffic_accident_interpretation.md": [
        "最高人民法院关于审理交通肇事刑事案件具体应用法律若干问题的解释",
        "交通肇事解释", "审理交通肇事刑事案件解释", "法释〔2000〕33号", "33号解释",
    ],
    "compulsory_insurance_regulation.md": [
        "机动车交通事故责任强制保险条例", "交强险条例", "强制保险条例",
    ],
    "school_bus_regulation.md": [
        "校车安全管理条例", "校车条例",
    ],
    "vehicle_scrap_standard.md": [
        "机动车强制报废标准规定", "报废标准规定", "强制报废标准",
    ],
    "vehicle_inspection.md": ["GB 38900", "GB38900", "机动车安全技术检验项目和方法"],
    "vehicle_standards.md": ["GB 1589", "GB1589", "汽车、挂车及汽车列车外廓尺寸"],
    "exam_site_standard_ga1029.md": ["GA 1029", "GA1029", "考试场地及其设施设置规范"],
    "exam_methods.md": ["GA 1026", "GA1026", "机动车驾驶人考试内容和方法"],
    "exam_acceptance_standards.md": ["GA/T 1030", "GA/T1030", "考场使用验收规范"],
    "vehicle_inspection_work.md": ["GA 801", "GA801", "机动车查验工作规程"],
    "driving_work_standard.md": ["机动车驾驶证业务工作规范", "业务工作规范"],
    "driving_exam.md": ["机动车驾驶人考试知识库"],
    "vehicle_inspection_current.md": ["机动车检验现行执行口径"],
    "colloquial_terms.md": ["口语对照"],
}

# 非「第X条」体例的文件：国标/行标用章节号（5.2、4.1.3），术语表与执行口径是自编文档，
# 里面出现的「第X条」都是在引用别的法规。对这些文件只校验法规名，不校验条号——
# 否则「GB 38900 第5.2条」这种正常写法会被判成编造条号。
CHAPTER_STYLE_FILES = {
    "vehicle_standards.md",
    "exam_site_standard_ga1029.md",
    "exam_methods.md",
    "exam_acceptance_standards.md",
    "vehicle_inspection_work.md",
    "vehicle_inspection.md",
    "vehicle_inspection_current.md",
    "colloquial_terms.md",
}

# 首行不是「# 标题」的文件（如公安部工作规范以「**中华人民共和国公安部**」开头）
DEFAULT_NAMES = {
    "driving_work_standard.md": "机动车驾驶证业务工作规范",
}


def _doc_title(text: str, source: str) -> str:
    m = TITLE_RE.search(text)
    if not m:
        return DEFAULT_NAMES.get(source, source[:-3])
    t = m.group(1).strip()
    t = re.sub(r"[（(][^（）()]*[）)]\s*$", "", t).strip()
    # 「**中华人民共和国公安部**」这类加粗首行不是标题
    if not t or t.startswith("*"):
        return DEFAULT_NAMES.get(source, source[:-3])
    return t


def build():
    docs = []
    for fn in sorted(os.listdir(KB_DIR)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(KB_DIR, fn)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        name = _doc_title(text, fn)

        arts, subs = set(), set()
        for m in ART_RE.finditer(text):
            try:
                n = cn2int(m.group(1))
            except Exception:
                continue
            if n <= 0:
                continue
            arts.add(n)
            if m.group(2):
                try:
                    subs.add(f"{n}-{cn2int(m.group(2))}")
                except Exception:
                    pass

        aliases = []
        for a in [name] + ALIASES.get(fn, []):
            a = a.strip()
            # 兼容「中华人民共和国XX法」与「XX法」两种写法
            for v in {a, re.sub(r"^中华人民共和国", "", a)}:
                if v and v not in aliases:
                    aliases.append(v)

        docs.append({
            "name": name,
            "source": fn,
            "aliases": aliases,
            "articles": sorted(arts),
            "sub_articles": sorted(subs),
            "article_style": (len(arts) >= 1) and fn not in CHAPTER_STYLE_FILES,
            "max_article": max(arts) if arts else 0,
        })
    return {
        "generated_at": date.today().isoformat(),
        "kb_dir": "data/knowledge_base",
        "doc_count": len(docs),
        "docs": docs,
    }


def main():
    idx = build()
    total = sum(len(d["articles"]) for d in idx["docs"])
    print(f"法规 {idx['doc_count']} 部，条号 {total} 个")
    for d in idx["docs"]:
        flag = "" if d["article_style"] else "  [非条号体例，只校验法规名]"
        print(f"  {d['name'][:34]:<36} {len(d['articles']):>4} 条  别名{len(d['aliases']):>2}{flag}")

    # 自检：常见高频条号必须存在，防止解析静默失败
    fails = []
    must = {
        "道路交通安全法": [91, 99, 101, 76],
        "机动车驾驶证申领和使用规定": [14, 16, 76, 79],
        "道路交通安全违法行为记分管理办法": [8, 9, 10],
        "道路交通事故处理程序规定": [60, 83],
        "刑法": [133],
    }
    by_alias = {}
    for d in idx["docs"]:
        for a in d["aliases"]:
            by_alias[a] = d
    for law, arts in must.items():
        d = by_alias.get(law)
        if not d:
            fails.append(f"法规缺失：{law}")
            continue
        for n in arts:
            if n not in d["articles"]:
                fails.append(f"{law} 缺第{n}条")
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
