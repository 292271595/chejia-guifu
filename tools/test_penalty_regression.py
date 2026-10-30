# -*- coding: utf-8 -*-
"""吊销 / 注销混淆专项回归（离线，不调大模型，秒级）

为什么单独建这个回归集
----------------------
bank_200 里的吊销类题目全是法条原话（"醉驾被吊销驾驶证几年能重新申请"），
而线上真实问法是"醉驾后多久可以继续考驾驶证"——不提"吊销"、不提"重考"。
题库与处理器的正则同源，必然共享同一个盲区：200/200 全绿，线上照样答错。

本文件专门覆盖两类问题：
A. **口语改写**：继续考 / 还能考 / 什么时候能考 / 重新拿证 / 撞死人 …
B. **制度缝合**：答案把"吊销后重新申领"写成"注销未超过2年恢复驾驶资格"（护栏层）

用法：
    python tools/test_penalty_regression.py       # 全部通过则退出码 0
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "backend"))

import fact_table as FT                       # noqa: E402
from rag_engine import RAGEngine              # noqa: E402

# ============================================================
# A. 事实层：口语问法必须被接住，且给出正确期限
# ------------------------------------------------------------
# (问题, must 必须出现, forbid 禁止出现, 期望 fact 前缀, 不应命中的 fact 前缀)
# ============================================================
FACT_CASES = [
    # ── 醉驾（非营运）5 年 ──
    ("醉驾后多久可以继续考驾驶证", ["5 年", "不得申请"], ["可以恢复驾驶资格"], "REVOKE:5", None),
    ("醉驾后多久能重新拿证", ["5 年", "不得申请"], ["可以恢复驾驶资格"], "REVOKE:5", None),
    ("醉驾以后什么时候能再考驾照", ["5 年"], ["可以恢复驾驶资格"], "REVOKE:5", None),
    ("喝醉酒开车被吊销了还能考驾照吗", ["5 年"], ["可以恢复驾驶资格"], "REVOKE:5", None),
    # ── 醉驾营运 10 年 ──
    ("醉驾营运车多久能再考", ["10 年"], ["可以恢复驾驶资格"], "REVOKE:10", None),
    ("醉酒驾驶营运机动车几年不能考", ["10 年"], ["可以恢复驾驶资格"], "REVOKE:10", None),
    # ── 致死 / 逃逸 → 终生（由第十八条禁申表或吊销表接住均可，结论必须是终生）──
    ("醉驾撞死人还能考驾照吗", ["终生"], ["5 年"], "BAN", None),
    ("饮酒后驾驶机动车发生事故致人死亡还能考驾照吗", ["终生"], [], "BAN", None),
    ("肇事逃逸构成犯罪还能考驾照吗", ["终生"], [], "BAN", None),
    # ── 原因不明时给对照表，而不是统一"2年" ──
    ("吊销驾照后几年可以重考", ["5年", "终生", "取决于"], [], "REVOKE:TABLE", None),
    # ── 记分类问题不能被吊销规则抢走 ──
    ("饮酒后驾驶机动车记几分", ["12 分"], ["不得申请"], "SCORE:12", "REVOKE"),
    ("醉驾扣几分", ["吊销"], [], "SCORE:DRUNK", "REVOKE"),
    # ── 注销恢复（原有的正确口径不能被改坏）──
    ("驾驶证注销后多长时间内可以恢复", ["2 年", "科目一"], ["5 年"], "RECOVER", "REVOKE"),
    ("恢复驾驶资格需要考什么", ["科目一"], ["5 年"], "RECOVER", "REVOKE"),
    ("驾驶证过期2年怎么恢复", ["科目一"], ["5 年"], "RENEW:EXPIRED", "REVOKE"),
    # ── 业务类"多久能考"不能被吊销规则抢答 ──
    ("科目二考完多久可以考科目三", [], ["不得申请", "终生"], None, "REVOKE"),
    ("学习驾驶证明过期怎么重考", ["3 年"], ["不得申请"], None, "REVOKE"),
    ("换证后多久能考摩托车", [], ["不得申请", "终生"], None, "REVOKE"),
    # ── AB 类禁申（第十八条）保持原口径 ──
    ("有醉驾记录能增驾A1吗", ["不能"], [], "BAN", None),
]

# ============================================================
# B. 护栏层：把"吊销→注销→2年恢复"缝合成一句的硬错误必须被整体纠正
# ============================================================
# 线上真实出现的错误答案（截图原文）
_BAD_ANSWER = (
    "醉驾后，如果机动车驾驶证被依法吊销，根据《机动车驾驶证申领和使用规定》第八项，"
    "注销未超过2年的，机动车驾驶人可以参加道路交通安全法律、法规和相关知识考试合格后恢复驾驶资格。"
    "因此，醉驾后可以在驾驶证被注销未超过2年的情况下，通过考试恢复驾驶资格。"
)

GUARD_CASES = [
    # (问题, 输入答案, must 必须出现, forbid 禁止出现)
    ("醉驾后多久可以继续考驾驶证", _BAD_ANSWER,
     ["5 年", "重新参加科目一至科目四全部考试"], ["可以在驾驶证被注销未超过2年的情况下"]),
    ("醉驾营运车多久能再考", _BAD_ANSWER,
     ["10 年"], ["可以在驾驶证被注销未超过2年的情况下"]),
    ("醉驾撞死人多久能再考", _BAD_ANSWER,
     ["终生"], ["可以在驾驶证被注销未超过2年的情况下"]),
    # 正确回答不应被改写（护栏不能误伤）
    ("醉驾被吊销驾驶证几年能重新申请", "5 年内不得申请机动车驾驶证。", ["5 年内不得申请"], []),
    ("驾驶证注销后多长时间内可以恢复", "注销未超过2年的，可以恢复驾驶资格。", ["2年"], []),
]


def _check(name, text, must, forbid):
    miss = [m for m in must if m not in text]
    bad = [f for f in forbid if f in text]
    if miss or bad:
        print(f"  ✗ {name}")
        if miss:
            print(f"      缺少: {miss}")
        if bad:
            print(f"      命中禁止项: {bad}")
        return False
    return True


def run_fact():
    print("\n[A] 事实层 · 口语改写与边界")
    ok = 0
    for q, must, forbid, want, not_want in FACT_CASES:
        r = FT.lookup(q)
        fact = (r or {}).get("fact") or ""
        answer = (r or {}).get("answer", "")
        good = True
        if want and not fact.startswith(want):
            print(f"  ✗ {q}\n      fact={fact or '未命中（掉进 RAG）'}，期望 {want}*")
            good = False
        if not_want and fact.startswith(not_want):
            print(f"  ✗ {q}\n      fact={fact}，不应命中 {not_want}*")
            good = False
        if not r:
            print(f"  ✗ {q}\n      事实层未接住，掉进 RAG（这正是本题集的回归目标）")
            good = False
        elif not _check(q, answer, must, forbid):
            good = False
        if good:
            ok += 1
            print(f"  ✓ {q}  →  {fact}")
    print(f"  合计 {ok}/{len(FACT_CASES)}")
    return ok == len(FACT_CASES)


def run_guard():
    print("\n[B] 护栏层 · 吊销≠注销 消歧")
    engine = RAGEngine.__new__(RAGEngine)   # 不初始化模型，只测纯函数护栏
    ok = 0
    for q, draft, must, forbid in GUARD_CASES:
        out = RAGEngine._apply_guardrails(engine, draft, q)
        if _check(q, out, must, forbid):
            ok += 1
            print(f"  ✓ {q}")
        else:
            print(f"      实际输出: {out[:160]}")
    print(f"  合计 {ok}/{len(GUARD_CASES)}")
    return ok == len(GUARD_CASES)


def main():
    a = run_fact()
    b = run_guard()
    print("\n" + ("全部通过" if a and b else "存在失败用例，请检查上方明细"))
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
