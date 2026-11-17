# -*- coding: utf-8 -*-
"""一键回归（正式工具，长期保留）

为什么要有这个东西：

    项目的回归是十几个独立脚本，平时靠手工一条条跑、再肉眼扫输出。
    这个做法已经骗过人两次——
      · `python x.py | tail -3; echo $?` 拿到的是 tail 的返回码，永远是 0；
      · `test_fact_table.py` 里 241 条断言，全挂了也返回 0。
    两次都让人以为"跑过了"，其实没跑过。

    用户的要求是"不要修了一步又破坏另一步"。这话翻译成工程语言就是：
    每次改动后必须有一套固定入口，一条命令给出一个总判定。
    只靠人记得跑哪几个脚本，迟早漏。

判定规则：

    只看退出码。非 0 就是失败，0 就是通过。

    这里一开始还加了「扫输出里的 FAIL 字样」做兜底，结果立刻误报：
    `verify_web100_judge.py` 有一行标题写着「应判 FAIL（检验没放松）」，
    是描述用的，被当成了失败。这个教训和 check_table_overlap 第一版一样——
    靠猜输出文本判断成败，迟早判错。脚本自己该负责给退出码。

    顺带补齐两个历史欠账：`test_fact_table.py` 断言全挂也返回 0；
    `test_guardrails.py` 根本没有断言，只打印给人看，所以「护栏全过」
    这句话一直是空的。两个都已改成真断言 + 真退出码。

用法：
    python tools/run_all_checks.py              # 全量
    python tools/run_all_checks.py --fast       # 跳过需要模型的慢项
    python tools/run_all_checks.py --only 判分  # 只跑名字含关键字的项
"""
import argparse
import io
import os
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# (名称, 脚本, 是否依赖模型所以较慢, 说明, 额外参数)
# 注意 eval_web100 必须带 --fresh：不带的话它会复用上次的结果文件，
# 3 秒就"通过"，等于没跑。第一次接进来时就是这样，显示 3.3s 才发现。
CHECKS = [
    ("表体检", "check_knowledge_table.py", False, "结构、依据原文、法条对账", []),
    ("表查重", "check_table_overlap.py", False, "新增行是否重复覆盖已有答案", []),
    ("金标准", "check_golden.py", False, "20 条冻结答案 + 依据原文 + 尺子自检", []),
    ("精准开发集", "eval_precision.py", False, "独立挑战题 + 首段/完整性 + 错误归因", ["--split", "dev"]),
    ("事实表", "test_fact_table.py", False, "key 断言 + 文案要素断言（含表生成）", []),
    ("判分负向", "verify_web100_judge.py", False, "喂错答案，确认判分器抓得住", []),
    ("抢答回归", "test_penalty_regression.py", False, "该让位的问题没被事实层抢答", []),
    ("上下文改写", "test_context_rewrite.py", False, "多轮追问的指代改写", []),
    ("护栏", "test_guardrails.py", False, "越界、编造、无关问题的拦截", []),
    ("数字守卫", "test_number_guard.py", False, "无出处的数字是否被发现", []),
    ("反馈推送", "test_feedback_push.py", False, "飞书格式、截断、字节上限", []),
    ("问句埋点", "test_query_log.py", False, "全量问句字段、分层统计与敏感字段", []),
    ("入口词准入", "check_entry_terms.py", False, "新增入口词的正向同类与反向守卫", []),
    ("对象维扫描", "scan_subject_leak.py", False, "同一处理器套多对象是否串答案", []),
    ("时限对账", "scan_item_time.py", False, "考试项目时限与 OCR 原文逐项核对", []),
    ("100题实测", "eval_web100.py", True, "真实链路端到端（需要模型，约 2 分钟）", ["--fresh"]),
]


def run(name, script, extra, timeout):
    path = os.path.join(ROOT, "tools", script)
    if not os.path.isfile(path):
        return name, "缺失", 0.0, f"脚本不存在：{script}"

    t0 = time.time()
    try:
        p = subprocess.run([PY, path] + list(extra), cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return name, "超时", time.time() - t0, f">{timeout}s"

    cost = time.time() - t0
    if p.returncode != 0:
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        detail = f"退出码 {p.returncode}"
        if tail:
            detail += "｜" + tail[-1].strip()[:80]
        return name, "失败", cost, detail

    return name, "通过", cost, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过需要模型的慢项")
    ap.add_argument("--only", default="", help="只跑名称或说明含该关键字的项")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    todo = []
    for name, script, slow, note, extra in CHECKS:
        if args.fast and slow:
            continue
        if args.only and args.only not in name and args.only not in note:
            continue
        todo.append((name, script, extra))

    if not todo:
        print("没有匹配的检查项")
        return 1

    print("=" * 78)
    print(f"一键回归：{len(todo)} 项")
    print("=" * 78)

    results = []
    for name, script, extra in todo:
        print(f"  ... {name}", end="", flush=True)
        r = run(name, script, extra, args.timeout)
        results.append(r)
        flag = {"通过": "OK", "失败": "FAIL", "缺失": "MISS", "超时": "TIME"}[r[1]]
        print(f"\r  [{flag:4s}] {name:10s} {r[2]:6.1f}s  {r[3]}")

    bad = [r for r in results if r[1] != "通过"]
    total = sum(r[2] for r in results)

    print("=" * 78)
    print(f"通过 {len(results) - len(bad)}/{len(results)}，总耗时 {total:.1f}s")
    if bad:
        print("\n未通过：")
        for name, status, cost, detail in bad:
            print(f"  {name}（{status}）：{detail}")
        print("\n任何一项没通过，就不算改完。别只看自己改的那部分。")
        return 1

    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
