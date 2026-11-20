# -*- coding: utf-8 -*-
"""金标准冻结集校验（判分器漂移的护栏）。

做三件事，缺一不可：

1. 依据校验：每条金标准自带的 `quote` 必须能在知识库里找到原文。
   找不到 = 依据已漂移（法规修订，或当初就是拼接改写），必须人来处理。
   `anchored=false` 的条目跳过这一步，但会打印出来——不允许静默放过。

2. 答案校验：拿系统当前答案，`must` 必须全中，`must_not` 必须全不中。
   这里不复用 eval_web100 的「对比句豁免」：金标准是冻结的，不该有豁免空间。

3. 尺子自检（这一步才是关键）：对每条做两次破坏——
   删掉答案里的一个必须要素 → 必须判失败
   往答案里塞一句错误说法   → 必须判失败
   两次都拦住，才证明这把尺子真的在量东西，而不是永远说「过」。
   如果尺子只会说通过，那它就是没用的检查器（项目里已经踩过两次这个坑）。

用法：
    python tools/check_golden.py
    python tools/check_golden.py --verbose
退出码：0 全过；1 有失败
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

GOLDEN = os.path.join(ROOT, "backend", "knowledge_data", "golden.json")
KB_DIR = os.path.join(ROOT, "data", "knowledge_base")

# 全角→半角 + 中文数字→阿拉伯。quota 与知识库比对时两边都归一化，
# 否则「三个月」和「3 个月」会被当成两回事——我第一次查登记规定第五十四条
# 就是因为 grep「3个月」没命中，误判成「知识库没有这条依据」。
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
       "七": 7, "八": 8, "九": 9, "十": 10}


def _cn2num(s: str) -> str:
    """把 1-99 的中文数字换成阿拉伯。只处理「十二」「二十」「三十五」这类常见形式。"""
    def repl(m):
        t = m.group(0)
        if t == "十":
            return "10"
        if "十" in t:
            a, _, b = t.partition("十")
            tens = _CN.get(a, 1) if a else 1
            ones = _CN.get(b, 0) if b else 0
            return str(tens * 10 + ones)
        if len(t) == 1:
            return str(_CN[t])
        return t
    # 只在后面紧跟量词/单位时替换，避免把「统一」「一律」这类词改坏
    return re.sub(r"[一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九](?=[个条年月分日周天次人成倍档])",
                  repl, s)


def normalize(text: str) -> str:
    text = (text or "").replace("　", " ")
    text = re.sub(r"\s+", "", text)
    return _cn2num(text)


def repair_ocr(text: str) -> str:
    """修复知识库里的 OCR 断行。和 tools/scan_item_time.py 保持同一套规则，
    这两处的漏检都出自同一类断行（0.5mi\\nn → 0.5min）。"""
    text = re.sub(r"mi\s*\n\s*n\b", "min", text)
    text = re.sub(r"(\d)\s*\.\s*\n\s*(\d)", r"\1.\2", text)
    text = re.sub(r"(\d)\s*\n\s*(min|s)\b", r"\1\2", text)
    return text


def head_zone(answer: str) -> str:
    """首段 = 主结论区。金标准的 must 默认只在这里判。

    为什么要分：金标准如果全文搜索，等于没有金标准。实测把「倒车入库 3.5 分钟」
    改成 4.5 分钟，答案第二段的依据句还留着 3.5，全文搜照样命中、冻结集照样全过——
    这正是 W069「主次颠倒」那个病：数字对，但第一句答错了。
    用户只看第一句，所以判分也必须只看第一句。
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", (answer or "").strip()) if p.strip()]
    return paras[0] if paras else ""


def judge(answer: str, must, must_not, must_anywhere=()):
    """must 只看首段（主结论）；must_anywhere 全文找（细节/依据）；
    must_not 全文找（错话出现在哪儿都不行）。"""
    answer = answer or ""
    head = head_zone(answer)
    miss = [p for p in must if not re.search(p, head)]
    miss += [p for p in must_anywhere if not re.search(p, answer)]
    bad = [p for p in must_not if re.search(p, answer)]
    return (not miss and not bad), miss, bad


def main():
    verbose = "--verbose" in sys.argv
    with open(GOLDEN, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]
    meta = data.get("meta", {})

    errs, warns = [], []
    ids = set()
    for c in cases:
        cid = c.get("id")
        if not cid:
            errs.append("<无 id 的条目>")
            continue
        if cid in ids:
            errs.append(f"{cid}: id 重复")
        ids.add(cid)
        for key in ("q", "must", "must_not", "anchored"):
            if key not in c:
                errs.append(f"{cid}: 缺字段 {key}")
        if c.get("anchored"):
            for key in ("file", "law", "quote"):
                if not c.get(key):
                    errs.append(f"{cid}: anchored=true 但缺 {key}")

    if errs:
        print("[结构] 不合法：")
        for e in errs:
            print("  " + e)
        return 1
    print(f"[结构] {len(cases)} 条合法（冻结于 {meta.get('frozen_at', '未知')}）")

    # ── 1. 依据校验 ──
    kb_cache = {}
    drift, unanchored = [], []
    for c in cases:
        if not c.get("anchored"):
            unanchored.append(c)
            continue
        fname = c["file"]
        if fname not in kb_cache:
            path = os.path.join(KB_DIR, fname)
            if not os.path.isfile(path):
                kb_cache[fname] = None
            else:
                with open(path, encoding="utf-8") as f:
                    kb_cache[fname] = normalize(repair_ocr(f.read()))
        body = kb_cache[fname]
        if body is None:
            drift.append((c["id"], f"知识库文件不存在：{fname}", []))
            continue
        missing = []
        # quote 用「……」表示分处引用，逐段校验，避免拼接被当成连续原文
        for seg in str(c["quote"]).split("……"):
            seg_n = normalize(seg)
            if seg_n and seg_n not in body:
                missing.append(seg)
        if missing:
            drift.append((c["id"], fname, missing))

    if drift:
        print(f"[依据] {len(drift)} 条摘录在知识库里找不到原文：")
        for cid, fname, missing in drift:
            print(f"  {cid}  {fname}")
            for seg in missing:
                print(f"      找不到：「{seg[:60]}」")
        print("  → 法规可能已修订，或当初的 quote 是拼接改写。必须人工确认后更新，不能改尺子迁就答案。")
    else:
        print(f"[依据] anchored 条目全部在知识库找到原文（{len(cases) - len(unanchored)} 条）")

    if unanchored:
        print(f"[依据] {len(unanchored)} 条没有可机器校验的依据（只人工核对过）：")
        for c in unanchored:
            print(f"  {c['id']}  {c['q']} —— {c.get('basis', '未说明')[:70]}")

    # ── 2. 答案校验 ──
    import fact_table as FT

    rows = []
    for c in cases:
        r = FT.lookup(c["q"]) or {}
        answer = r.get("answer") or ""
        ok, miss, bad = judge(answer, c["must"], c["must_not"], c.get("must_anywhere") or ())
        rows.append({"case": c, "fact": r.get("fact"), "answer": answer,
                     "ok": ok, "miss": miss, "bad": bad})

    passed = [x for x in rows if x["ok"]]
    failed = [x for x in rows if not x["ok"]]
    print(f"\n[答案] {len(passed)}/{len(rows)} 通过")
    for x in failed:
        c = x["case"]
        print(f"  FAIL {c['id']} {c['q']}  (fact={x['fact'] or '走检索层'})")
        if x["miss"]:
            print(f"       缺要素：{x['miss']}")
        if x["bad"]:
            print(f"       踩了禁词：{x['bad']}")
        if verbose:
            print("       答案前 240 字：" + x["answer"][:240].replace("\n", " | "))

    # ── 3. 尺子自检：破坏答案必须被判失败 ──
    # 破坏方式有讲究：
    #  A 抹掉要素 —— 验证 must 真的在起作用
    #  B 注入已知错答案 —— 验证 must_not 真的在起作用
    # 不做「把正则翻译成中文再注入」，那样必然失真，会造出既不像中文、
    # 也匹配不上原正则的字符串，自检永远通过，等于没测（第一版就是这么错的）。
    print("\n[尺子自检] 对每条做两次破坏，都必须被判失败")
    self_total, self_ok = 0, 0
    self_bad, self_weak = [], []
    for x in rows:
        c, answer = x["case"], x["answer"]
        if not answer:
            self_bad.append((c["id"], "答案为空，无法做自检"))
            continue

        # 破坏 A：把这个要素彻底抹掉。
        # 不能只删第一处匹配——「12」会在「一次记12分」「记满12分」里出现多次，
        # 只删一处剩下的仍能让必须项命中，自检就永远通过。
        # must 和 must_anywhere 都要覆盖；抹完要重切首段（可能被抹短了）。
        all_must = list(c["must"]) + list(c.get("must_anywhere") or [])
        dropped = None
        for pat in all_must:
            if re.search(pat, answer):
                dropped = (pat, re.sub(pat, "", answer))
                break
        if dropped:
            self_total += 1
            ok_a, _, _ = judge(dropped[1], c["must"], c["must_not"],
                               c.get("must_anywhere") or ())
            if ok_a:
                self_bad.append((c["id"], f"抹掉全部「{dropped[0]}」后仍判通过——必须项没起作用"))
            else:
                self_ok += 1
        else:
            self_bad.append((c["id"], "答案里找不到任何必须项命中，无法做删除自检"))

        # 破坏 B：注入已知错答案
        for wrong in c.get("wrong") or []:
            self_total += 1
            ok_b, _, bad_b = judge(answer + "\n\n" + wrong, c["must"], c["must_not"],
                                   c.get("must_anywhere") or ())
            if ok_b:
                self_bad.append((c["id"], f"注入错答案「{wrong[:24]}」后仍判通过"))
            elif bad_b:
                self_ok += 1          # 禁词拦住的，说明 must_not 有效
            else:
                self_ok += 1          # 整体拦住了，但靠的是 must 缺要素
                self_weak.append((c["id"], wrong))

    print(f"  拦住 {self_ok}/{self_total}")
    for cid, why in self_bad:
        print(f"  [漏洞] {cid}: {why}")
    if self_weak:
        print(f"  [提示] {len(self_weak)} 条错答案是被 must（缺正确答案要素）拦住的，"
              f"不是被禁词拦住的——这些条目的 must_not 其实没被验证到：")
        for cid, w in self_weak:
            print(f"        {cid}「{w[:30]}」")

    # ── 结论 ──
    print("\n" + "=" * 70)
    problems = len(drift) + len(failed) + len(self_bad)
    if problems:
        print(f"未通过：依据漂移 {len(drift)} 条 / 答案失败 {len(failed)} 条 / 尺子漏洞 {len(self_bad)} 处")
        return 1
    print(f"全过：{len(rows)} 条答案、{len(rows) - len(unanchored)} 条依据原文、"
          f"{self_ok} 次尺子自检")
    if unanchored:
        print(f"提醒：{len(unanchored)} 条无条文依据，只能人工复核，别当成已机器验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
