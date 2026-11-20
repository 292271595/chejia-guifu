# -*- coding: utf-8 -*-
"""知识点表体检（正式工具，长期保留）

《问答精准化路线》里两条约束的执行机构：

  约束一「同一事实只存一处」——表里的依据是知识库原文的摘录，法条一修订，
      摘录就对不上。这个脚本把「对不上」变成可执行的检查，而不是等人发现。
  约束五「每个答案能回到条文」——每条依据必须能在对应文件里找到出处。

检查分两类：

  【结构】id 唯一、必填字段齐全、正则能编译、slot 声明完整、日期格式合法。
        结构错误直接退出码 1，因为它会让加载器静默跳过整行。

  【一致性】把 quote 按省略号/句号切成段，逐段到 data/knowledge_base 对应
        文件里找。命中率低于 100% 不判失败（法规原文常有「……」省略和跨段
        拼接），但要打印出来，让人决定是改摘录还是改文案。

用法：
    python tools/check_knowledge_table.py            # 全部检查
    python tools/check_knowledge_table.py --strict   # 一致性不达标也返回 1
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_DIR = os.path.join(ROOT, "backend", "knowledge_data")
KB_DIR = os.path.join(ROOT, "data", "knowledge_base")

REQUIRED = ["id", "topic", "answer", "sources"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_SEG = 10  # 少于这个字数的片段不参与一致性检查，太短必然到处都能匹配


def normalize(text: str) -> str:
    """去掉空白、markdown 粗体、省略号，只留实义字符，便于跨排版比对。"""
    text = re.sub(r"\*+|`+", "", text or "")
    text = re.sub(r"[…\.]{2,}", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def segments(quote: str):
    """按省略号/句号/分号切段，剔除过短片段。"""
    parts = re.split(r"[…]{2,}|\.{3,}|[。；;]", quote or "")
    return [p for p in (normalize(x) for x in parts) if len(p) >= MIN_SEG]


def check_structure(rows):
    errs, seen = [], set()
    for i, row in enumerate(rows):
        rid = row.get("id") or f"<第{i}行>"
        for key in REQUIRED:
            val = row.get(key)
            empty = (not val.strip()) if isinstance(val, str) else not val
            if empty:
                errs.append(f"{rid}: 缺必填字段 {key}")
        if rid in seen:
            errs.append(f"{rid}: id 重复")
        seen.add(rid)

        topic = row.get("topic") or ""
        if topic:
            try:
                re.compile(topic)
            except re.error as e:
                errs.append(f"{rid}: topic 正则编译失败 {e}")

        for field in ("require", "exclude"):
            for p in row.get(field) or []:
                try:
                    re.compile(p)
                except re.error as e:
                    errs.append(f"{rid}: {field} 正则编译失败 {e}")

        slot = row.get("slot")
        if slot:
            for key in ("name", "pattern"):
                if not slot.get(key):
                    errs.append(f"{rid}: slot 缺 {key}")
            if slot.get("pattern"):
                try:
                    rx = re.compile(slot["pattern"])
                    if rx.groups < 1:
                        errs.append(f"{rid}: slot.pattern 必须含 1 个捕获组")
                except re.error as e:
                    errs.append(f"{rid}: slot.pattern 编译失败 {e}")
            if slot.get("kind") not in (None, "cn2num"):
                errs.append(f"{rid}: slot.kind 不支持 {slot.get('kind')}")

        for s in row.get("sources") or []:
            if not s.get("file"):
                errs.append(f"{rid}: source 缺 file")
            elif not os.path.isfile(os.path.join(KB_DIR, s["file"])):
                errs.append(f"{rid}: source 文件不存在 {s['file']}")
            if not (s.get("quote") or "").strip():
                errs.append(f"{rid}: source 缺 quote（没有原文就谈不上可回溯）")

        if not row.get("sources"):
            errs.append(f"{rid}: sources 为空")

        for field in ("effective", "expires"):
            val = (row.get(field) or "").strip()
            if val and not DATE_RE.match(val):
                errs.append(f"{rid}: {field} 日期格式应为 YYYY-MM-DD，实际 {val!r}")

        if (row.get("status") or "active") not in ("active", "retired"):
            errs.append(f"{rid}: status 只能是 active/retired")

        # verify 是回归保护的唯一来源。缺了它，这行知识点就是「没有任何测试盯着」，
        # 答案改错也不会有人知道——按结构错误处理，不给静默通过的机会。
        v = row.get("verify") or {}
        if not isinstance(v, dict):
            errs.append(f"{rid}: verify 必须是对象")
        else:
            if not (v.get("questions") or []):
                errs.append(f"{rid}: verify.questions 为空（至少要有一个用于回归的问法）")
            if not (v.get("must") or []):
                errs.append(f"{rid}: verify.must 为空（没有判分要素等于没有回归保护）")
            if not isinstance(v.get("must_not") or [], list):
                errs.append(f"{rid}: verify.must_not 必须是数组")
    return errs


# 法条号：第X条（含中文数字），后面可能跟第Y款/项，这里只取到「条」这一级。
ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千零〇\d]+条")


def check_citations(rows):
    """verify.must 里提到的法条号，必须在该行 sources 里出现过。

    这是「结论与依据对账」的机器版：答案写「依据第八十条」，依据列表里却只有
    第七十六条 —— 语义上可能都对，但读者拿着答案去核对会找不到出处，等于
    「答案能回到条文」这条约束没落地。机器能查的就别指望人眼。
    """
    bad = []
    for row in rows:
        rid = row.get("id")
        titles = " ".join((s.get("title") or "") for s in row.get("sources") or [])
        must = " ".join((row.get("verify") or {}).get("must") or [])
        cited = set(ARTICLE_RE.findall(must))
        absent = sorted(a for a in cited if a not in titles)
        if absent:
            bad.append((rid, absent, titles.strip()[:80]))
    return bad


def check_consistency(rows):
    """quote 各段能否在知识库里找到。返回 [(rid, 缺失段, 命中率)]。"""
    cache = {}
    bad = []
    for row in rows:
        rid = row.get("id")
        total = hit = 0
        missing = []
        for s in row.get("sources") or []:
            fname = s.get("file") or ""
            path = os.path.join(KB_DIR, fname)
            if not os.path.isfile(path):
                continue
            if fname not in cache:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    cache[fname] = normalize(f.read())
            blob = cache[fname]
            for seg in segments(s.get("quote")):
                total += 1
                if seg in blob:
                    hit += 1
                else:
                    missing.append((fname, seg))
        if total and hit < total:
            bad.append((rid, missing, hit / total))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="一致性不达标也返回 1")
    args = ap.parse_args()

    files = [f for f in os.listdir(TABLE_DIR) if f.endswith(".json")] if os.path.isdir(TABLE_DIR) else []
    if not files:
        print(f"[FAIL] 表目录为空：{TABLE_DIR}")
        return 1

    all_rows = []
    for fn in files:
        with open(os.path.join(TABLE_DIR, fn), encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows") or []
        print(f"== {fn}：{len(rows)} 行 ==")
        all_rows.extend(rows)

    errs = check_structure(all_rows)
    if errs:
        print(f"\n[结构错误] {len(errs)} 处")
        for e in errs:
            print("  " + e)
    else:
        print(f"\n[结构] 通过：{len(all_rows)} 行，id 唯一、字段齐全、正则可编译")

    bad = check_consistency(all_rows)
    if not bad:
        print("[一致性] 通过：所有依据摘录都能在知识库里找到")
    else:
        print(f"[一致性] {len(bad)} 行有摘录找不到出处（可能是法规修订，也可能是拼接）：")
        for rid, missing, rate in bad:
            print(f"  {rid}  命中率 {rate:.0%}")
            for fname, seg in missing[:3]:
                print(f"    {fname}: 找不到「{seg[:50]}」")

    cite_bad = check_citations(all_rows)
    if not cite_bad:
        print("[法条对账] 通过：判分要素里引的每一条，依据里都能找到")
    else:
        print(f"[法条对账] {len(cite_bad)} 行的判分要素引了依据里没有的条文：")
        for rid, absent, titles in cite_bad:
            print(f"  {rid}  缺 {absent}")
            print(f"    依据现有：{titles}")

    if errs or cite_bad:
        return 1
    if bad and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
