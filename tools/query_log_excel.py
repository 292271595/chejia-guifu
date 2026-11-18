# -*- coding: utf-8 -*-
"""把问句日志（JSONL）同步成一张可筛选的 Excel 表格。

为什么还要一张表
────────────────
JSONL 是底稿：字段全、不怕丢、脚本好处理，但人看着累。
真实问题是拿来看分布的——哪类问得多、哪些降级、哪个事实 key 被问爆——
用表格排序筛选比翻日志快得多。所以两边都留：JSONL 保真，Excel 好看。

怎么保证不重复
──────────────
每次都用 JSONL 全量重建，不做增量追加。数据量是几百到几万行，重建不到一秒，
但追加一旦漏判就会产生重复行，后面所有统计都不可信。宁可慢一点，不要脏。

输出
────
三个工作表：
  问句日志  —— 一行一次提问，冻结表头、带筛选
  统计汇总  —— 层级、领域、事实 key、高频问法、耗时
  说明      —— 字段含义与更新方式
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import query_log_report as R  # noqa: E402

HEADERS = [
    ("时间", 20, "ts"),
    ("用户原话", 42, "q"),
    ("改写后问句", 32, "resolved_q"),
    ("命中层级", 18, "layer"),
    ("事实 key", 34, "fact"),
    ("回答开头", 50, "__answer_head"),
    ("回答全文", 120, "__answer_full"),
    ("业务领域", 16, None),
    ("是否降级", 14, "degraded"),
    ("上下文原因", 18, "ctx_reason"),
    ("耗时(ms)", 11, "ms"),
    ("对话轮数", 10, "history_turns"),
    ("来源数", 9, "source_count"),
    ("答案字数", 10, "answer_chars"),
    ("状态码", 8, "status"),
]

HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
LAYER_FILL = {
    "fact": "E2EFDA",
    "rag": "FFF2CC",
    "rag_fallback": "FCE4D6",
    "fact_review_fallback": "FCE4D6",
    "rewrite": "DDEBF7",
    "refused": "EDEDED",
    "error": "FFC7CE",
}


def load_records(directory: str):
    rows = []
    for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
        if os.path.basename(path).startswith("_"):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("[QL]"):
                    line = line[4:].strip()
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("q"):
                    rows.append(rec)
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    return rows


def _sheet_log(wb, rows):
    ws = wb.active
    ws.title = "问句日志"
    for i, (name, width, _) in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 24

    for r, rec in enumerate(rows, 2):
        for i, (_, _, key) in enumerate(HEADERS, 1):
            if key is None:
                val = R._domain(rec.get("fact") or "")
            elif key == "__answer_head":
                # 表格里只放开头的 80 字：判断"答得对不对"够用，又能一眼扫过去。
                # 全文在 JSONL 的 answer 字段里，需要细看时去那里取。
                val = " ".join(str(rec.get("answer") or "").split())
                if len(val) > 80:
                    val = val[:80] + "…"
            elif key == "__answer_full":
                # 全文单独一列，不换行、不调行高：想细看就点单元格看编辑栏，
                # 上千行也不会因为自动换行把表格撑成一片高墙。
                val = " ".join(str(rec.get("answer") or "").split())
            else:
                val = rec.get(key, "")
                if key in ("ms", "history_turns", "source_count", "answer_chars", "status"):
                    try:
                        val = int(val or 0)
                    except Exception:
                        val = 0
                elif val is None:
                    val = ""
            c = ws.cell(row=r, column=i, value=val)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=(i in (2, 3, 6, 10)))
        layer = str(rec.get("layer") or "")
        color = LAYER_FILL.get(layer)
        if color:
            ws.cell(row=r, column=4).fill = PatternFill("solid", fgColor=color)

    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"
    ws.freeze_panes = "A2"
    return ws


def _sheet_stats(wb, rows):
    ws = wb.create_sheet("统计汇总")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 46

    def title(text, row):
        c = ws.cell(row=row, column=1, value=text)
        c.font = Font(bold=True, size=12, color="1F4E79")
        return row + 1

    layers = Counter(r.get("layer") or "unknown" for r in rows)
    domains = Counter(R._domain(r.get("fact") or "") for r in rows)
    facts = Counter(r.get("fact") or "" for r in rows if r.get("fact"))
    questions = Counter((r.get("q") or "").strip() for r in rows)
    lat = [int(r.get("ms") or 0) for r in rows]

    row = 1
    row = title("总量", row)
    stats = [
        ("提问次数", len(rows)),
        ("去重问法", len(questions)),
        ("多轮对话次数", sum(1 for r in rows if int(r.get("history_turns") or 0) > 0)),
        ("改写生效次数", sum(1 for r in rows if r.get("resolved_q"))),
        ("错误次数", sum(1 for r in rows if r.get("layer") == "error")),
    ]
    for k, v in stats:
        ws.cell(row=row, column=1, value=k)
        ws.cell(row=row, column=2, value=v)
        row += 1

    row += 1
    row = title("耗时(ms)", row)
    for k, v in (("平均", int(sum(lat) / len(lat)) if lat else 0),
                 ("P50", R._pct(lat, 0.5)), ("P90", R._pct(lat, 0.9)),
                 ("P99", R._pct(lat, 0.99)), ("最大", max(lat) if lat else 0)):
        ws.cell(row=row, column=1, value=k)
        ws.cell(row=row, column=2, value=v)
        row += 1

    for name, counter, note in (
        ("命中层级", layers, ""),
        ("业务领域", domains, ""),
        ("事实 key", facts, "被问得最多的考点，优先检查这些答案"),
    ):
        row += 1
        row = title(name, row)
        if note:
            ws.cell(row=row, column=3, value=note).font = Font(size=9, color="808080")
            row += 1
        if not counter:
            ws.cell(row=row, column=1, value="（暂无数据）")
            row += 1
        for k, v in counter.most_common(30):
            ws.cell(row=row, column=1, value=k)
            ws.cell(row=row, column=2, value=v)
            row += 1

    row += 1
    row = title("高频问法 Top 20", row)
    if not questions:
        ws.cell(row=row, column=1, value="（暂无数据）")
    for q, n in questions.most_common(20):
        ws.cell(row=row, column=2, value=n)
        ws.cell(row=row, column=1, value=q).alignment = Alignment(wrap_text=True)
        row += 1
    return ws


def _sheet_help(wb, directory, path):
    ws = wb.create_sheet("说明")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 96
    lines = [
        ("这张表是什么", "每次用户提问一行，用来算真实问题分布。原始底稿是同目录下的 JSONL，表格由它生成。"),
        ("谁在更新", "计划任务 RAG-QueryLog-Pull 每天拉取后自动重建；也可手动跑 tools/query_log_excel.py。"),
        ("会不会重复", "每次全量重建，不做追加，所以不会出现重复行。"),
        ("为什么不是空的", "表里没有数据，就说明还没有成功拉取过，或线上还没发布含导出接口的版本。"),
        ("命中层级含义", "fact=确定性查表；rag=检索生成；rag_fallback=事实层复核后回退；rewrite=多轮改写；refused=超纲拒答；error=报错。"),
        ("业务领域", "按事实 key 前缀归类，用来快速看哪类问题最集中。"),
        ("回答开头是什么", "系统实际回答的前 80 字，用来判断答得对不对（答错对象、答非所问、车轱辘话只在这里看得出来）。右边「回答全文」是同一段话的完整版，不换行、不撑行高，点单元格在编辑栏里看；底稿 JSONL 里存的是完整原文，最多 1500 字。小程序那种只回结构化块的模式会自动还原成文字。"),
        ("为什么不记账号", "刻意不记 openid、IP 和源文件路径。能定位问题就够了，多记一样就多一份泄露风险。"),
        ("怎么关掉回答留存", "云端加 QUERY_LOG_ANSWER=off 即只记元数据不记正文；=head 只记前 200 字；QUERY_LOG_ANSWER_MAX 可改 1500 这个上限。"),
        ("怎么排优先级", "先看高频问法，再看降级与错误占比。没被点踩不等于答对了，点踩只是旁证。"),
        ("数据在哪", os.path.abspath(directory)),
        ("本文件", os.path.abspath(path)),
    ]
    for i, (k, v) in enumerate(lines, 1):
        a = ws.cell(row=i, column=1, value=k)
        a.font = Font(bold=True)
        a.alignment = Alignment(vertical="top")
        b = ws.cell(row=i, column=2, value=v)
        b.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[i].height = 32
    return ws


def sync(directory: str, out_path: str, keep_sheets=()) -> dict:
    rows = load_records(directory)
    wb = Workbook()
    _sheet_log(wb, rows)
    _sheet_stats(wb, rows)
    _sheet_help(wb, directory, out_path)
    # 拉取记录：表里暂时没数据时，至少能看出是没令牌、没发布，还是真没人提问
    run_log = os.path.join(directory, "_pull.log")
    if os.path.isfile(run_log):
        with open(run_log, encoding="utf-8", errors="replace") as f:
            lines = [x.rstrip("\n") for x in f if x.strip()][-200:]
        keep_sheets = list(keep_sheets) + [("拉取记录", lines)]
    # 自选额外工作表，放在最后
    for name, sheet_rows in keep_sheets:
        ws = wb.create_sheet(name)
        for r, line in enumerate(sheet_rows, 1):
            ws.cell(row=r, column=1, value=line)
        ws.column_dimensions["A"].width = 110
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    # 先写临时文件再替换：写到一半被打断不会留下损坏的 xlsx
    tmp = out_path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, out_path)
    return {"rows": len(rows), "path": out_path}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.getenv("QUERY_LOG_LOCAL_DIR", "").strip()
                    or os.path.join(ROOT, "data", "query_log"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = args.out or os.path.join(args.dir, "问句日志.xlsx")
    res = sync(args.dir, out)
    print(f"已生成：{res['path']}（{res['rows']} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
