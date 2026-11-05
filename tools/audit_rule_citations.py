# -*- coding: utf-8 -*-
"""条款 ↔ 规则 一致性校验（效仿业内「条款到规则可追溯」的做法）

背景
----
事实层的 100 多个处理器，每个都用 source("《法规》第X条", "原文片段") 标注依据。
但这份标注从来没人校验过：条号写错了、写成了别的法规的条号、或者引了一条
根本不存在的条文，系统一样照常输出，用户看到的就是「依据《某规定》第X条」，
翻开却找不到 —— 这比答错更难被发现，因为格式完全正常。

业内成熟做法（合规条款结构化、政策知识库）都会建「条款 ↔ 规则」的双向绑定：
正向由条款查受影响的规则（法规修订时定位影响面），反向由规则查条款（校验依据真实性）。
本项目已有 backend/kb_index/citation_index.json（23 部法规、1069 个条号），
正好是校验用的白名单，不需要新数据。

做三件事
--------
1. 抽取：AST 解析 backend/fact_*.py，取出每处 source() 的标题与所在处理器
2. 校验：法规名归一后查白名单，条号必须在该法规的 articles 内
3. 反查：输出「条号 -> 引用它的规则」映射，供法规修订时定位影响面

用法
----
    python tools/audit_rule_citations.py            # 校验并打印问题
    python tools/audit_rule_citations.py --map      # 附带打印条款反查表
    python tools/audit_rule_citations.py --json     # 机器可读输出
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
INDEX = os.path.join(BACKEND, "kb_index", "citation_index.json")

CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(s: str) -> int | None:
    """中文数字转整数，支持到 9999（第X条最大 1217）。"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "千":
            section += (num or 1) * 1000
            num = 0
        else:
            return None
    return total + section + num


# 一条标题里可能并列多个条号："第十六条第一款第二项、第十五条"
_ART_RE = re.compile(r"第([一二三四五六七八九十百千零〇\d]+)条(?:之([一二三四五六七八九十\d]+))?")
_LAW_RE = re.compile(r"《\s*([^》]{2,60}?)\s*》")
# 合法但没有条号的依据：附件、章/节、国标/行标、政策文件
_NON_ARTICLE_RE = re.compile(
    r"(附件\s*\d|第[一二三四五六七八九十]+[章节]|第[一二三四五六七八九十]+编|"
    r"\bGA\s*\d|\bGB\s*\d|\bGB/T\s*\d|意见|通知|改革措施|办法（.{0,20}）$|"
    r"指导意见|工作规范|标准$)")


def load_index() -> dict:
    with open(INDEX, encoding="utf-8") as f:
        data = json.load(f)
    alias2doc = {}
    for doc in data["docs"]:
        for name in [doc["name"]] + doc.get("aliases", []):
            alias2doc[name.strip()] = doc
    return {"docs": data["docs"], "alias2doc": alias2doc}


def module_globals(path: str) -> dict:
    """取模块级简单字符串赋值（LAW / SRC / SRC_FILE 等），用于还原 f-string 占位符。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    g = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                g[t.id] = node.value.value
    return g


def render_title(node: ast.AST, g: dict) -> str | None:
    """把 source() 的第一个参数还原成字符串（支持 f-string 与常量拼接）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name):
                out.append(g.get(v.value.id, "{" + v.value.id + "}"))
            else:
                return None
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = render_title(node.left, g)
        right = render_title(node.right, g)
        if left is not None and right is not None:
            return left + right
    return None


def enclosing_func(tree: ast.AST) -> dict:
    """source() 所在行 -> 所属函数名。"""
    line2func = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    line2func[sub.lineno] = node.name
    return line2func


def func_params(tree: ast.AST) -> dict:
    """函数名 -> 形参名集合。用于识别「包装函数里的 source(title, ...)」。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = {a.arg for a in node.args.args + node.args.kwonlyargs}
    return out


def collect() -> list:
    """抽出全部 source() 调用。

    分四类，后三类不计入「问题」，因为它们无法或不该用白名单校验：
      ok        能静态确定法规+条号 -> 可校验
      wrapper   包装函数定义（source(title, ...)，title 是形参）-> 跳过
      dynamic   f-string 里含数据表字段（{rule['law']}），条号运行时才确定 -> 跳过
      no_law    标题里没有《法规名》（多为标准/意见/改革措施）-> 单列
      no_art    只写了法规名或只写「附件1/第三节」，没有条号 -> 单列
    """
    items = []
    for fn in sorted(os.listdir(BACKEND)):
        if not (fn.startswith("fact_") and fn.endswith(".py")):
            continue
        path = os.path.join(BACKEND, fn)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        g = module_globals(path)
        line2func = enclosing_func(tree)
        params = func_params(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "source" and node.args):
                continue
            func = line2func.get(node.lineno, "?")
            first = node.args[0]

            # 包装函数：参数是所在函数的形参
            if isinstance(first, ast.Name) and first.id in params.get(func, set()):
                continue

            title = render_title(first, g)
            base = {"file": fn, "line": node.lineno, "func": func,
                    "title": title if title is not None else "<动态>"}
            # 残留占位符：条号由局部变量在运行时拼出（如 {law_no}），静态无法确定
            if title is None or "{" in title:
                base["kind"] = "dynamic"
                items.append(base)
                continue

            law_m = _LAW_RE.search(title)
            base["law"] = law_m.group(1).strip() if law_m else None
            base["arts"] = [n for n in (cn2int(m.group(1))
                                        for m in _ART_RE.finditer(title)) if n is not None]
            if not base["law"]:
                # 没有书名号，但可能引的是政策文件/改革措施（本来就没有条号结构）
                base["kind"] = ("non_article" if _NON_ARTICLE_RE.search(title) else "no_law")
            elif base["arts"]:
                base["kind"] = "ok"
            elif _NON_ARTICLE_RE.search(title):
                # 依据落在附件、章/节、国家标准或政策文件上，没有条号是正常的
                base["kind"] = "non_article"
            else:
                base["kind"] = "no_art"
            items.append(base)
    return items


def resolve_doc(law: str, alias2doc: dict):
    """法规名归一：先精确，再包含匹配（避免「机动车登记规定」误配「机动车登记工作规范」）。"""
    if not law:
        return None
    if law in alias2doc:
        return alias2doc[law]
    cands = [d for name, d in alias2doc.items() if law in name or name in law]
    if not cands:
        return None
    # 取名字最接近的（长度差最小）
    cands.sort(key=lambda d: abs(len(d["name"]) - len(law)))
    return cands[0]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    items = collect()
    idx = load_index()
    alias2doc = idx["alias2doc"]

    checked = [it for it in items if it["kind"] == "ok"]
    problems = {"art_missing": [], "law_missing": [], "no_art": []}
    for it in checked:
        doc = resolve_doc(it["law"], alias2doc)
        if doc is None:
            problems["law_missing"].append(it)
            continue
        pool = set(doc["articles"])
        missing = [a for a in it["arts"] if a not in pool]
        if missing:
            it["doc"] = doc["name"]
            it["missing"] = missing
            it["max_article"] = doc.get("max_article")
            problems["art_missing"].append(it)
    problems["no_art"] = [it for it in items if it["kind"] == "no_art"]
    problems["no_law"] = [it for it in items if it["kind"] == "no_law"]
    dynamic = [it for it in items if it["kind"] == "dynamic"]
    non_article = [it for it in items if it["kind"] == "non_article"]

    total = len(items)
    hard = len(problems["art_missing"])
    soft = len(problems["no_art"]) + len(problems["no_law"])
    print(f"扫描 fact_*.py 中 source() 依据标注：{total} 处")
    print(f"  可校验条号 {len(checked)} 处 ｜ 附件/节/国标等非条号依据 {len(non_article)} 处"
          f" ｜ 动态条号 {len(dynamic)} 处")
    print(f"  条号不存在（硬错）{hard} 处 ｜ 依据不完整（软错）{soft} 处\n")

    labels = {
        "art_missing": ("条号在该法规中不存在（硬错，必须修）", None),
        "no_art": ("只写了法规名，没有条号也不算附件/节（软错，依据不具体）", None),
        "no_law": ("标题里没有《法规名》（需确认是否政策文件）", None),
        "law_missing": ("法规名在知识库索引里找不到（无法校验，需确认是否该补索引）", None),
    }
    for key in ("art_missing", "no_art", "no_law", "law_missing"):
        rows = problems.get(key) or []
        if not rows:
            continue
        title, tpl = labels[key]
        print(f"── {title}（{len(rows)}）──")
        for r in rows:
            extra = ""
            if key == "art_missing":
                extra = f"  ← 缺 {r['missing']}，该法规最大第 {r['max_article']} 条"
            print(f"  [{r['file']}:{r['line']}] {r['func']}：{r['title']}{extra}")
        print()

    if "--map" in sys.argv:
        rev = defaultdict(list)
        for it in checked:
            doc = resolve_doc(it["law"], alias2doc)
            name = doc["name"] if doc else it["law"]
            for a in it["arts"]:
                rev[(name, a)].append(it["func"])
        print("── 条款反查表（法规修订时用它定位受影响的处理器）──")
        for (name, art), funcs in sorted(rev.items(), key=lambda x: (x[0][0], x[0][1])):
            print(f"  {name} 第{art}条  <-  {', '.join(sorted(set(funcs)))}")

    if "--json" in sys.argv:
        out = {"total": total, "checked": len(checked),
               "hard": hard, "soft": soft,
               "problems": problems, "dynamic": dynamic, "items": items}
        with open(os.path.join(ROOT, "tools", "_audit_rule_citations.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\n已写入 tools/_audit_rule_citations.json")

    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
