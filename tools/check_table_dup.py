# -*- coding: utf-8 -*-
"""表行重复实现检查（正式工具，长期保留）

为什么需要这个脚本：

  知识点表的每一行都是「答案 + 依据 + 守卫 + 判分要素」的迁移结果，注册成
  `table_<id>` 处理器，并且排在调度表**最前面**。于是一件事如果手写处理器没删，
  两份实现会同时存在：表行的主题正则先命中，表行赢，测试全绿、回归通过，
  谁也不知道手写那份还在。等哪天有人改手写的那份，改了半天没效果——
  因为线上跑的是表行。

  这个坑本项目栽过两次：
    · 2026-11-19 第二批 B：给 REVIEW:BASICS / SCORE:REDUCE 写了表行，
      可 q_review / q_study_reduce 早就答同一件事，等于同一答案两处写。
    · 2026-11-19 第四批 D：REG:KEEP_PLATE 也一样，fact_vehicle.q_keep_plate
      从 2026-11 就在，我照着「未命中」的假象又写了一行。

  根因是判断方法错了：只看 `lookup` 没命中就以为「系统不会答」，实际是
  「系统会答，只是入口不认这个问法」。手工 `audit()` 一次只能发现一条，
  于是做成检查。

两类问题：

  【重复实现】表行的 id 在 backend/*.py 里还能搜到字符串字面量
      → 手写处理器仍在返回同一个 fact。处理：删表行，或删手写处理器及其
        调度注册，只留一处。
  【被抢答】表行的 verify 问句赢家不是它自己
      → 前序处理器把问句接走了，表行的守卫或调度顺序要收紧。
  【入口漏】表行的 verify 问句无人应答
      → 这行连自己的问法都答不出，等于没有回归保护。

已知豁免写在表行里：`"_dup_ok": "原因"`。只给「手写与表行并存是刻意的」用，
不是给「懒得删」用。

退出码：有问题返回 1。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
TABLE_DIR = os.path.join(ROOT, "backend", "knowledge_data")
# 生成器自身不算实现，否则每行都会被自己搜到
SKIP_FILES = {"knowledge_table.py"}


def table_name(rid: str) -> str:
    return "table_" + rid.replace(":", "_").lower()


def backend_text():
    """把所有手写处理器源码拼成一段，供字面量搜索。"""
    parts = []
    for fn in sorted(os.listdir(BACKEND)):
        if not fn.endswith(".py") or fn in SKIP_FILES:
            continue
        with open(os.path.join(BACKEND, fn), encoding="utf-8", errors="ignore") as f:
            parts.append((fn, f.read()))
    return parts


def code_lines(text):
    """只保留非注释行：注释里提到某个 fact id 是说明，不是实现。

    `knowledge_table.find("X")` 这类行也要排掉——那是「读表行」，不是
    「另写一份实现」。实测 APPLY:MATERIAL 就栽在这个假阳性上。
    """
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        # 去掉行尾注释（不处理字符串里带 # 的极端情况，宁可误报也不漏报）
        line = line.split("#", 1)[0]
        if "find(" in line:
            continue
        yield line


def main():
    sources = backend_text()
    files = [f for f in sorted(os.listdir(TABLE_DIR)) if f.endswith(".json")]

    dup, stolen, dead = [], [], []
    total = 0
    for fn in files:
        with open(os.path.join(TABLE_DIR, fn), encoding="utf-8") as f:
            payload = json.load(f)
        for row in payload.get("rows") or []:
            if (row.get("status") or "active") != "active":
                continue
            rid = row.get("id") or ""
            allow = (row.get("_dup_ok") or "").strip()

            if not allow:
                hits = []
                for name, text in sources:
                    for line in code_lines(text):
                        if f'"{rid}"' in line or f"'{rid}'" in line:
                            hits.append(name)
                            break
                if hits:
                    dup.append((rid, sorted(set(hits))))

            if rid and rid not in ("", None):
                qs = (row.get("verify") or {}).get("questions") or []
                if qs:
                    import fact_table as FT
                    want = table_name(rid)
                    for q in qs:
                        total += 1
                        a = FT.audit(q)
                        got, fact = a.get("winner_match"), a.get("winner")
                        if got == want:
                            continue
                        if fact:
                            stolen.append((rid, q, fact, got))
                        else:
                            dead.append((rid, q))

    print(f"== 表行重复实现检查：{len(files)} 个表、{total} 个 verify 问句 ==")
    if not (dup or stolen or dead):
        print("[通过] 没有重复实现；每个表行的 verify 问句都由它自己作答")
        return 0

    if dup:
        print(f"\n[重复实现] {len(dup)} 行：手写处理器还在返回同一个 fact")
        for rid, hits in dup:
            print(f"  {rid}  仍出现在 {', '.join(hits)}")
            print("    处理：删表行，或删手写处理器和它的调度注册，只留一处；")
            print("    确属刻意并存才在表行写 \"_dup_ok\": \"原因\"")
    if stolen:
        print(f"\n[被抢答] {len(stolen)} 条：问句被前序处理器接走")
        for rid, q, fact, got in stolen:
            print(f"  {rid}  「{q}」 被 {got} 答成 {fact}")
    if dead:
        print(f"\n[入口漏] {len(dead)} 条：表行自己的问句都不命中")
        for rid, q in dead:
            print(f"  {rid}  「{q}」 无人应答，检查 topic / require / exclude")
    return 1


if __name__ == "__main__":
    sys.exit(main())
