# -*- coding: utf-8 -*-
"""分层评测：把「一条答案对不对」拆成四层分别打分

为什么需要
----------
现有 eval_100 只有二值判分（must 全中、must_not 全不中），报出来是 116/116。
但那一个数回答不了关键问题：结论对了，依据是不是真的？召回到了没有？是不是该答没答？
业内成熟做法（法律 RAG 评测、合同审查评测）都按层打分，因为不同层的失效要不同的人修：

  结论层  答对了吗           -> 改处理器 / 改提示词
  引用层  引的条号真实存在吗  -> 改生成约束 / 加护栏
  召回层  依据条文进没进片段  -> 改切分 / 改检索 / 补术语
  弃答层  该答的是不是答了    -> 改路由 / 补知识

最有价值的交叉项是「结论对、但引用了不存在的条号」：用户一查就发现对不上，
比答错更伤可信度，而单一正确率完全看不见这一类。

用法
----
    python tools/eval_layers.py                  # 分析最近一次 eval_100 结果
    python tools/eval_layers.py --file xxx.json  # 指定结果文件
（先跑 python tools/eval_100.py --via-engine 生成结果）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import citation_guard as cg  # noqa: E402

# 明确表示「没答」的措辞；注意不能把「不记分」「不能申请」这类否定结论算进来
ABSTAIN_RE = re.compile(
    r"(暂无(准确)?(信息|数据|相关)|未收录|知识库(中)?(暂)?未|"
    r"无法确定|不能确定|不确定|不清楚|没有找到相关|无相关信息)"
)
ART_RE = re.compile(r"第[一二三四五六七八九十百千零\d]+条")


def basis_recalled(basis: str, titles):
    """依据里的条号是否出现在召回片段的标题里。basis 无条号时返回 None（不可判定）。"""
    arts = ART_RE.findall(basis or "")
    if not arts:
        return None
    for t in titles or []:
        if not t:
            continue
        for a in arts:
            if a in t:
                return True
    return False


def basis_matches_answer(basis: str, cites):
    """事实层专用：basis 里的条号，是否出现在答案实际引用的条号里。

    事实层不检索，所以「召回率」对它没有意义。它该被检查的是另一件事：
    依据标的是第 75 条，答案实际引的是不是第 75 条。两者不一致说明
    依据标注与实现脱节——63 岁那次事故（依据应是第 65 条、答的却是第 75 条）
    正是这种形态，而端到端判分看不见。
    """
    arts = {cg.cn2int(a[1:-1]) for a in ART_RE.findall(basis or "")}
    arts.discard(None)
    if not arts:
        return None
    got = {c.get("article") for c in cites if c.get("article")}
    return bool(arts & got)


def analyze(rows):
    idx = cg.index()
    if not idx.load():
        print("引用索引加载失败，无法评引用层")
        return

    per = []
    for r in rows:
        ans = r.get("answer") or ""
        cites = idx.extract(ans)
        counted = [c for c in cites
                   if c.get("status") in ("exists", "no_article", "unknown_law")]
        # 只有「法规收了、但该条号不存在」才算编造；
        # 「法规本身没收录」（政策文件、部门意见）属于无法核验，单列，不混算成假引用。
        fake = [c for c in counted if c.get("status") == "no_article"]
        unknown = [c for c in counted if c.get("status") == "unknown_law"]
        per.append({
            "id": r["id"], "q": r["q"], "layer": r["layer"], "ok": r["ok"],
            "cite_total": len(counted), "cite_ok": len(counted) - len(fake) - len(unknown),
            "cite_fake": fake, "cite_unknown": unknown,
            "recalled": basis_recalled(r.get("basis", ""), r.get("titles")),
            "basis_ok": basis_matches_answer(r.get("basis", ""), cites),
            "abstain": bool(ABSTAIN_RE.search(ans)),
            "degraded": bool(r.get("degraded")),
        })

    n = len(per)
    ok_n = sum(1 for p in per if p["ok"])
    print(f"样本 {n} 条\n")
    print(f"{'层':<6}{'指标':<22}{'结果':<26}说明")
    print("-" * 92)

    # 1 结论层
    print(f"{'结论':<6}{'答案正确率':<22}{f'{ok_n}/{n} = {ok_n / n:.1%}':<26}"
          f"现有口径")

    # 2 引用层（拆成三个口径：真实 / 编造 / 无法核验）
    cited = [p for p in per if p["cite_total"]]
    tot_c = sum(p["cite_total"] for p in cited)
    ok_c = sum(p["cite_ok"] for p in cited)
    fake_c = sum(len(p["cite_fake"]) for p in cited)
    unk_c = sum(len(p["cite_unknown"]) for p in cited)
    if tot_c:
        clean = sum(1 for p in cited if not p["cite_fake"])
        print(f"{'引用':<6}{'条号真实率':<22}{f'{ok_c}/{tot_c} = {ok_c / tot_c:.1%}':<26}"
              f"法规已收录且条号确实存在")
        print(f"{'':<6}{'编造条号':<22}{f'{fake_c} 处（{len([p for p in cited if p['cite_fake']])} 条答案）':<26}"
              f"法规收了、该条号不存在 —— 真错")
        print(f"{'':<6}{'无法核验':<22}{f'{unk_c} 处':<26}"
              f"所引法规不在收录范围（政策文件等），不等于编造")
        print(f"{'':<6}{'引用干净的题':<22}{f'{clean}/{len(cited)}':<26}"
              f"没有出现不存在的条号")
    else:
        print(f"{'引用':<6}{'条号真实率':<22}{'无可核引用':<26}")

    # 3 依据层：事实层与 RAG 层统一用「来源标题」比对，不从答案正文抽。
    #   原因：事实层答案常写「（第三条）」「依据第二十五条」这种裸条号，正文抽不出来，
    #   但 sources 里存的是完整的「《法规》第X条」，那才是可信的结构化依据。
    #   事实层不检索，所以这里的「命中」含义是「答案挂的来源与题库标注的依据对得上」。
    for lay, label, note in (("fact", "事实层依据一致", "答案所挂来源条号与题库依据是否对得上"),
                             ("rag", "RAG 依据进片段", "依据条号是否出现在召回标题")):
        sub = [p for p in per if p["layer"] == lay and p["recalled"] is not None]
        if not sub:
            print(f"{'依据' if lay == 'fact' else '召回':<6}{label:<22}{'不可判定':<26}")
            continue
        hit = sum(1 for p in sub if p["recalled"])
        print(f"{'依据' if lay == 'fact' else '召回':<6}{label:<22}"
              f"{f'{hit}/{len(sub)} = {hit / len(sub):.1%}':<26}{note}")

    # 4 弃答层
    abst = [p for p in per if p["abstain"]]
    deg = [p for p in per if p["degraded"]]
    print(f"{'弃答':<6}{'声称无信息':<22}{f'{len(abst)}/{n}':<26}"
          f"答案里出现「暂无/未收录/无法确定」")
    print(f"{'':<6}{'降级为原文':<22}{f'{len(deg)}/{n}':<26}生成超时或护栏触发")

    # 交叉：结论对但引用假 —— 单一正确率看不见
    both = [p for p in per if p["ok"] and p["cite_fake"]]
    print()
    if both:
        print(f"⚠ 结论对、但引用了不存在的条号：{len(both)} 条")
        for p in both:
            labels = [c.get("label") or c.get("law_raw") for c in p["cite_fake"]]
            print(f"   [{p['id']}] {p['q'][:36]}  →  {labels}")
    else:
        print("结论对且引用全真：无「结论对依据错」的情形")

    wrong_cite = [p for p in per if not p["ok"] and p["cite_fake"]]
    if wrong_cite:
        print(f"\n答案本就错、且引用也假：{len(wrong_cite)} 条（这类双重失效最伤可信度）")
        for p in wrong_cite[:8]:
            print(f"   [{p['id']}] {p['q'][:36]}")

    # 分层正确率（沿用原口径，便于对照）
    print()
    for lay in ("fact", "rag"):
        sub = [p for p in per if p["layer"] == lay]
        if sub:
            p_ok = sum(1 for p in sub if p["ok"])
            print(f"  {lay:4s} 层：{p_ok}/{len(sub)} = {p_ok / len(sub) * 100:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(ROOT, "tools", "_eval100_result.json"))
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if not os.path.exists(args.file):
        print(f"找不到 {args.file}，先跑 python tools/eval_100.py --via-engine")
        return 1
    with io.open(args.file, encoding="utf-8") as f:
        rows = json.load(f)
    analyze(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
