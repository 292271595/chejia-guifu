# -*- coding: utf-8 -*-
"""
对象维泄漏扫描（subject / vehicle leak scan）
==============================================
起因：六个「科目二还要等多久」的问句被同一个科目三处理器抢答，返回同一段科目三答案。
单题回归测不出来（每条断言只钉死了自己那一句），必须换一种扫法：
**固定处理器，换对象，看答案是否跟着变。**

两个维度：
  科目维 —— 一/二/三/四（安全文明）交叉
  车型维 —— A1/A2/A3/B1/B2/C1/C2/C6/D/E/F 交叉

三类告警：
  [错]  问句点名对象 A，答案里只有对象 B —— 答非所问（最严重）
  [疑]  同处理器同模板，不同对象答案文本完全相同 —— 没分档，套了同一段
  [漏]  该模板在所有对象上都没被事实层接住 —— 只统计，不算错

用法：
  python tools/scan_subject_leak.py           # 全量
  python tools/scan_subject_leak.py -v        # 附答案前 120 字
"""
import io
import sys
import re
import os
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import fact_table as FT
import fact_common as C

VERBOSE = "-v" in sys.argv

# ---------- 对象抽取 ----------
# 科目四在法规里的正式名是「科目三安全文明驾驶常识考试」，所以先匹配安全文明/科目四
_S4 = re.compile(r"安全文明|科目四|科目4|文明驾驶")
_S1 = re.compile(r"科目一|科目1|理论(?:考试|考)")
_S2 = re.compile(r"科目二|科目2|场地")
_S3 = re.compile(r"科目三|科目3|道路驾驶|路考")


def subjects(text):
    """返回文本里出现的科目集合。'科目三安全文明' 只算 S4，不重复算 S3。"""
    got = set()
    if _S4.search(text):
        got.add("S4")
        # 去掉安全文明片段，避免「科目三安全文明」被同时算成 S3
        rest = _S4.sub("", text)
    else:
        rest = text
    if _S1.search(rest):
        got.add("S1")
    if _S2.search(rest):
        got.add("S2")
    if _S3.search(rest):
        got.add("S3")
    return got


_CODE2NAME = {
    "A1": ["大型客车", "大客"],
    "A2": ["牵引车", "重型牵引挂车", "半挂"],
    "A3": ["城市公交"],
    "B1": ["中型客车", "中客"],
    "B2": ["大型货车", "大货车", "重型货车"],
    "C1": ["小型汽车", "手动挡"],
    "C2": ["小型自动挡", "自动挡"],
    "C3": ["低速载货"],
    "C4": ["三轮汽车"],
    "C5": ["残疾人专用"],
    "C6": ["轻型牵引挂车", "拖挂"],
    "D": ["普通三轮摩托"],
    "E": ["普通二轮摩托"],
    "F": ["轻便摩托"],
    "M": ["轮式专用机械"],
}


def vehicles(text):
    """文本里出现的准驾车型代码（含中文名）。"""
    got = set(C.extract_codes(text))
    for code, names in _CODE2NAME.items():
        for n in names:
            if n in text:
                got.add(code)
                break
    return got


# ---------- 问句矩阵 ----------
# 科目槽位把三种写法都放进来：手机输入「科目2」「科四」很常见，
# 而调度层与处理器内部到处是「科目二」「安全文明」的硬字面量，写法换一下就整句漏掉。
SUBJ_SLOTS = ["科目一", "科目二", "科目三", "科目四", "科目2", "科四"]

SUBJ_TEMPLATES = [
    "{S}考试不合格多久能再约",
    "{S}考什么内容",
    "{S}多少分及格",
    "{S}能考几次",
    "{S}挂了多久能重考",
    "{S}考试有几个项目",
    "{S}预约考试要提前几天",
    "{S}合格标准是什么",
    # —— 以下为补充真口语问法，覆盖补考费/有效期/次数上限/跳过/异地 ——
    "{S}最多能约几次",
    "{S}五次不过怎么办",
    "{S}补考要交钱吗",
    "{S}考试有效期多久",
    "{S}可以跳过不考吗",
    "{S}能异地考吗",
    "{S}考试多长时间",
    "{S}考不过要等几天",
    "{S}满分多少",
    "{S}考试费多少钱",
    "{S}一年能考几次",
    "{S}挂了要重新交钱吗",
    "{S}考试顺序是什么",
    "{S}成绩多久出来",
    "{S}能补考几次",
    "{S}考试难不难",
]

VEH_SLOTS = ["A1", "A2", "A3", "B1", "B2", "C1", "C2", "C6", "D", "E", "F"]

VEH_TEMPLATES = [
    "{V}增驾需要什么条件",
    "{V}多大年龄能考",
    "{V}能直接考吗",
    "{V}考试有几个科目",
    "{V}实习期多久",
    "{V}驾驶证几年一审",
    "{V}能开什么车",
    "{V}驾驶证有效期几年",
]


def conclusion(answer):
    """答案的结论部分（第一段）。

    为什么只看第一段：事实层的答案都是"结论在前、细则在后"的结构，
    后面常举例（如实习期答案写"同大类内增驾（如 C1→B2、B2→A1）不产生新实习期"）。
    拿全文比对会把举例当成结论对象，扫出一堆假警报——实测实习期就假报了 4 条。
    括号里的内容同理要剥掉，那基本都是在举例。
    """
    parts = [p for p in re.split(r"\n\s*\n|\n", answer or "") if p.strip()]
    head = parts[0] if parts else ""
    return re.sub(r"[（(][^）)]*[）)]", "", head)


def norm_text(s):
    """答案归一化：去掉标点空白，用于判断"完全一样"。"""
    return re.sub(r"[\s，。、；：！？,.;:!?（）()\[\]【】\"'—\-]+", "", s or "")


def run_matrix(name, slots, templates):
    rows = []
    for tpl in templates:
        for slot in slots:
            q = tpl.format(S=slot, V=slot)
            r = FT.lookup(q)
            rows.append({
                "tpl": tpl,
                "slot": slot,
                "q": q,
                "hit": bool(r),
                "handler": (r or {}).get("match") or "",
                "fact": (r or {}).get("fact") or "",
                "answer": (r or {}).get("answer") or "",
            })
    return rows


def report(name, rows, slot_kind):
    """slot_kind='subject' 用科目抽取，'vehicle' 用车型抽取。"""
    extract = subjects if slot_kind == "subject" else vehicles

    print("\n" + "=" * 78)
    print(f"【{name}】共 {len(rows)} 句，命中事实层 {sum(1 for r in rows if r['hit'])} 句")
    print("=" * 78)

    # ---- 告警一：问句点名 A，答案结论只有 B ----
    wrong = []
    for r in rows:
        if not r["hit"]:
            continue
        want = extract(r["q"])
        # 只比"这一个对象"，避免「C1增驾B2」这种双车型句误判
        want = {x for x in want if str(x) in r["q"] or _CODE2NAME.get(str(x), [""])[0] in r["q"]}
        have = extract(conclusion(r["answer"]))
        if want and have and not (want & have):
            wrong.append((r, want, have))

    if wrong:
        print(f"\n[错] 答案结论里的对象与问句不符：{len(wrong)} 句")
        for r, want, have in wrong:
            print(f"  · {r['q']}")
            print(f"    问的是 {sorted(want)}，答案结论只有 {sorted(have)}；处理器 {r['handler']} / {r['fact']}")
            if VERBOSE:
                print(f"    结论：{conclusion(r['answer'])[:120]}…")

    # ---- 告警二：同模板同处理器，不同对象答案完全一致 ----
    by_tpl = defaultdict(list)
    for r in rows:
        if r["hit"]:
            by_tpl[(r["tpl"], r["handler"])].append(r)

    same = []
    for (tpl, handler), group in by_tpl.items():
        if len(group) < 2:
            continue
        buckets = defaultdict(list)
        for r in group:
            # 槽位先归一：'科目2' 和 '科目二' 是同一科，不归一就会把自己的两种写法
            # 当成"两个对象共用一段答案"，扫出一堆假警报（实测假报 4 组）。
            buckets[norm_text(r["answer"])].append(FT.fact_exam.norm_subject(r["slot"]))
        for text, slot_list in buckets.items():
            slot_list = sorted(set(slot_list))
            if len(slot_list) < 2:
                continue
            # 答案结论里只点名了一个对象，却被多个对象共用 —— 典型"套了第一档"
            named = extract(text)
            if len(named) == 1:
                same.append((tpl, handler, slot_list, sorted(named), group[0]["fact"]))

    if same:
        print(f"\n[疑] 多个对象共用同一段答案，且答案只点名一个对象：{len(same)} 组")
        for tpl, handler, slot_list, named, fact in same:
            print(f"  · 模板「{tpl}」→ {slot_list} 共用同一答案，答案只提 {named}")
            print(f"    处理器 {handler} / {fact}")

    # ---- 告警三：完全没命中（只统计） ----
    miss = [r for r in rows if not r["hit"]]
    if miss:
        print(f"\n[漏] 未进事实层（走 RAG）：{len(miss)} 句")
        print("   " + "、".join(r["q"] for r in miss[:12]) + ("…" if len(miss) > 12 else ""))

    # ---- 附：命中分布 ----
    cnt = defaultdict(int)
    for r in rows:
        if r["hit"]:
            cnt[r["handler"]] += 1
    if cnt:
        print("\n[分布] " + "、".join(f"{k}:{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1])))

    return wrong, same


def main():
    s_rows = run_matrix("科目维", SUBJ_SLOTS, SUBJ_TEMPLATES)
    w1, s1 = report("科目维扫描", s_rows, "subject")

    v_rows = run_matrix("车型维", VEH_SLOTS, VEH_TEMPLATES)
    w2, s2 = report("车型维扫描", v_rows, "vehicle")

    print("\n" + "=" * 78)
    print(f"汇总：[错] {len(w1) + len(w2)} 句，[疑] {len(s1) + len(s2)} 组")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
