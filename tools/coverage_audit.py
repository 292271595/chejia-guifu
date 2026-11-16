# -*- coding: utf-8 -*-
"""条款覆盖普查：知识库里有多少硬事实条款，事实层接住了多少。

为什么先做普查，而不是直接"反向出题"：

  出题的前提是知道该出哪些题。如果直接让模板去套条款，会生成大量
  「醉酒驾驶机动车或者饮酒后驾驶营运机动车依法被吊销机动车驾驶证未满5年的
  是什么情况」这类没人会问的句子，跑出来的失败也大多是噪音。
  先把「哪些条款含可问的硬事实」「这些条款有没有人接」数清楚，
  才知道缺口是 30 条还是 300 条，值不值得建自动出题流水线。

做三件事：

  1. 切条：按 citation_index 的条号，从源文件里切出每条正文
  2. 判硬事实：正文里出现「N 年/月/日/周岁/分/次/人/厘米」这类可被追问的量
  3. 对账：事实层所有 source() 引到过哪些条号（复用 audit_rule_citations.collect）
     未覆盖 = 含硬事实 - 被引用

  注意：未覆盖不等于「必须补」。程序性、定义性、罚则兜底条款本来就没人问。
  这里只负责把缺口摊开，判断价值仍要人来做。

用法：
    python tools/coverage_audit.py              # 汇总 + 缺口清单
    python tools/coverage_audit.py --top 40     # 多列几条
    python tools/coverage_audit.py --doc 172号  # 只看某部法规
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "backend"))

KB_DIR = os.path.join(ROOT, "data", "knowledge_base")
INDEX = os.path.join(ROOT, "backend", "kb_index", "citation_index.json")

# 条号：中文数字，允许「第一百二十一条」
_ART_RE = re.compile(r"第([一二三四五六七八九十百零〇]+)条")

# 硬事实：能被用户追问「几年/几分/几日/多大」的量
_HARD = [
    re.compile(r"\d+\s*(?:年|个月|月|日|天|小时|分钟|秒)"),
    re.compile(r"\d+\s*周岁"),
    re.compile(r"一次记\s*\d+\s*分"),
    re.compile(r"\d+\s*(?:分|次|人|厘米|cm|米|m|千克|kg|%|％)"),
]


def cn2int(s):
    m = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
         "八": 8, "九": 9, "零": 0, "〇": 0}
    if not s:
        return None
    if s == "十":
        return 10
    if "百" in s:
        a, _, b = s.partition("百")
        h = m.get(a, 1) if a else 1
        if not b:
            return h * 100
        return h * 100 + (cn2int(b) or 0)
    if "十" in s:
        a, _, b = s.partition("十")
        tens = m.get(a, 1) if a else 1
        ones = m.get(b, 0) if b else 0
        return tens * 10 + ones
    if len(s) == 1:
        return m.get(s)
    return None


def split_articles(path):
    """把一份 md 切成 {条号: 正文}。正文取到下一个条号出现之前。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    hits = []
    for mt in _ART_RE.finditer(text):
        n = cn2int(mt.group(1))
        if n:
            hits.append((mt.start(), n))
    out = {}
    for i, (pos, n) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        body = text[pos:end].strip()
        # 同一部法规里条号可能重复出现（目录、引用），取最长的那段正文
        if n not in out or len(body) > len(out[n]):
            out[n] = body
    return out


def hard_facts(body):
    """返回这条里的硬事实片段（去重，最多 4 个），空列表=不是硬事实条款。"""
    found = []
    for pat in _HARD:
        for mt in pat.finditer(body):
            s = mt.group(0).strip()
            if s not in found:
                found.append(s)
    return found[:4]


def num_signature(body):
    """数字签名：条款正文里出现的全部阿拉伯数字。用于判断两条是不是在讲同一件事。"""
    return set(re.findall(r"\d+", body or ""))


def overlap_with(cand_sig, covered_items):
    """未覆盖条款是不是"另一个法源在讲同一件事"。

    为什么要这一步：直接报「116 条未覆盖」会误导。实施条例第二十二条讲
    「驾驶证有效期 6 年」、第二十六条讲「6 年→10 年→长期」，而 172 号令
    第十三条、第六十二条早就覆盖了同一事实。事实层只引更细更新的那部，
    这是合理取舍，不是缺口。不挤掉这层水分，覆盖率数字就是假的。

    判据：数字签名有 2 个以上交集（或一方被另一方包含）即视为疑似重叠。
    宁可多标「疑似」，也不要让人去补一堆重复条款。
    """
    hits = []
    for item in covered_items:
        a, b = cand_sig, num_signature(item["body"])
        if not a or not b:
            continue
        inter = a & b
        if len(inter) >= 2 or (a and (a <= b or b <= a)):
            hits.append((item, sorted(inter)))
    return hits


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    top = 40
    only = None
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    if "--doc" in sys.argv:
        only = sys.argv[sys.argv.index("--doc") + 1]

    from audit_rule_citations import collect, resolve_doc, load_index
    idx = load_index()
    alias2doc = idx["alias2doc"]

    # 事实层已经引到过的 (法规标准名, 条号)
    covered = set()
    for it in collect():
        if it.get("kind") != "ok":
            continue
        doc = resolve_doc(it.get("law"), alias2doc)
        if not doc:
            continue
        for a in it.get("arts") or []:
            covered.add((doc["name"], a))

    rows = []
    total_art = total_hard = total_cov = 0
    covered_items = []          # 已覆盖条款的正文，用于判「法源重叠」
    for doc in json.load(open(INDEX, encoding="utf-8"))["docs"]:
        name = doc["name"]
        if only and only not in name and only not in (doc.get("source") or ""):
            continue
        path = os.path.join(KB_DIR, doc.get("source") or "")
        if not doc.get("source") or not os.path.isfile(path):
            continue
        arts = split_articles(path)
        hard, cov = [], []
        for n in doc["articles"]:
            body = arts.get(n)
            if not body:
                continue
            total_art += 1
            hf = hard_facts(body)
            if not hf:
                continue
            total_hard += 1
            item = {"doc": name, "file": doc["source"], "art": n,
                    "hard": hf, "body": re.sub(r"\s+", " ", body)[:150]}
            hard.append(item)
            if (name, n) in covered:
                cov.append(item)
                covered_items.append(item)
                total_cov += 1
        rows.append({"doc": name, "file": doc["source"],
                     "hard": hard, "cov": cov, "gap": []})

    # 第二遍：把「未覆盖」拆成「法源重叠」和「真缺口」
    dup_total, real_total = 0, 0
    for r in rows:
        for x in r["hard"]:
            if (r["doc"], x["art"]) in covered:
                continue
            ov = overlap_with(num_signature(x["body"]), covered_items)
            if ov:
                x["dup"] = [(i["doc"], i["art"]) for i, _ in ov[:3]]
                r["gap"].append(x)
                dup_total += 1
            else:
                x["dup"] = None
                r["gap"].append(x)
                real_total += 1

    print("=" * 78)
    print("条款覆盖普查")
    print("=" * 78)
    print(f"知识库法规 {len(rows)} 部，切出带正文的条号 {total_art} 个")
    print(f"其中含硬事实（年限/分数/年龄/期限/数量）：{total_hard} 条")
    pct = (total_cov / total_hard * 100) if total_hard else 0
    print(f"事实层 source() 直接引用到的：{total_cov} 条")
    print(f"未直接引用：{total_hard - total_cov} 条，其中")
    print(f"  · 疑似同一事实的另一法源（数字签名重叠）：{dup_total} 条")
    print(f"  · 真缺口（没找到对应覆盖）：{real_total} 条")

    gaps = [r for r in rows if r["gap"]]
    gaps.sort(key=lambda r: -len(r["gap"]))
    print(f"\n按未引用条数排序（前 {min(8, len(gaps))} 部）：")
    for r in gaps[:8]:
        print(f"  {r['doc']}：硬事实 {len(r['hard'])} 条，直接引用 {len(r['cov'])}，"
              f"未引用 {len(r['gap'])}")

    # 优先列真缺口——法源重叠的那些补了也是重复
    real_gap = [x for r in rows for x in r["gap"] if not x["dup"]]
    print(f"\n【真缺口】{len(real_gap)} 条（列前 {min(top, len(real_gap))} 条）：")
    for x in real_gap[:top]:
        print(f"  [{x['file']}] 第{x['art']}条  硬事实：{'、'.join(x['hard'])}")
        print(f"      {x['body'][:110]}")

    if "--dup" in sys.argv:
        dup_gap = [x for r in rows for x in r["gap"] if x["dup"]]
        print(f"\n【疑似法源重叠】{len(dup_gap)} 条（补之前先确认是不是同一事实）：")
        for x in dup_gap[:top]:
            print(f"  [{x['file']}] 第{x['art']}条  硬事实：{'、'.join(x['hard'])}")
            print(f"      可能与 {'、'.join(f'{d}第{a}条' for d, a in x['dup'])} 重复")

    print("\n提醒：真缺口也不等于必须补。程序性、定义性、罚则兜底条款本来就没人问，")
    print("      要按真实提问频次排序（第四步接反馈日志），不能按缺口大小盲补。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
