# -*- coding: utf-8 -*-
"""事实层复核的验收测试

三组：
  A 不误伤   93 条题库里事实层命中的正确问句，复核应判 OK
  B 能拦住   三条线上真实错答，复核应判 WRONG
  C 端到端   把错误答案注入事实层，确认链路真的会降级、不发错答

C 组是这轮的重点：前两组只证明"模型判得对"，只有 C 证明"系统真的会拦"。

    python tools/test_fact_review.py            # 全跑
    python tools/test_fact_review.py --A 20     # A 组只跑 20 条
    python tools/test_fact_review.py --only C   # 只跑端到端
"""
import os
import sys
import io
import json
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

from openai import OpenAI  # noqa: E402
import fact_review as fr  # noqa: E402

CFG = {
    "base": os.getenv("API_BASE"),
    "key": os.getenv("API_KEY"),
    "model": os.getenv("MODEL", "glm-4-flash"),
}
CLIENT = OpenAI(base_url=CFG["base"], api_key=CFG["key"], timeout=30.0)

REAL_BUGS = [
    ("我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗",
     "**延期换证：最长不超过 3 年；延期期间不得驾驶机动车。**\n\n"
     "依据第七十五条：机动车驾驶人因服兵役、出国（境）等原因，无法在规定时间内办理驾驶证期满换证、"
     "审验、提交身体条件证明的，可以在驾驶证有效期内或者有效期届满 1 年内向核发地车辆管理所申请延期办理。\n\n"
     "⚠️ 两个要点：\n1. 延期期限最长不超过 3 年；\n2. 延期期间不得驾驶机动车——延期只是保留驾驶资格，不是允许继续开车。"),
    ("70岁C1还能开吗",
     "**不能开了。** 年满 70 周岁的，不得驾驶 D、E、F 类摩托车，应当换领轻便摩托车（F）驾驶证。\n\n"
     "依据第七十六条：年龄在 70 周岁以上的，不得驾驶大型客车、牵引车、城市公交车、中型客车、"
     "大型货车、无轨电车和有轨电车。"),
    ("驾驶证因超期未换证被注销怎么恢复",
     "**驾驶证过期换证流程：** 驾驶证有效期满前 90 日内，向核发地车辆管理所申请换证。\n\n"
     "需要提交：身份证明、身体条件证明、近期一寸白底照片。逾期未换证的，不得驾驶机动车。"),
]


def test_A(limit=None):
    """A 组：正确答案不应被误报"""
    import fact_table

    qs, seen = [], set()
    for fn in ("eval_100_part1.json", "eval_100_part2.json"):
        p = os.path.join(ROOT, "data", fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        for row in (data.get("questions") if isinstance(data, dict) else data) or []:
            if isinstance(row, dict) and row.get("q"):
                qs.append(row["q"])

    rows = []
    for q in qs:
        try:
            r = fact_table.lookup(q)
        except Exception:
            r = None
        if not r or (q, r.get("fact")) in seen:
            continue
        seen.add((q, r.get("fact")))
        sig = fr.entity_signals(q, r["answer"])
        v = fr.review(q, r["answer"], CLIENT, CFG["model"], timeout=4.0, signals=sig)
        rows.append({"q": q, "fact": r.get("fact"), "signals": sig, **v})
        if limit and len(rows) >= limit:
            break

    ok = sum(1 for x in rows if x["verdict"] == "OK")
    wr = [x for x in rows if x["verdict"] == "WRONG"]
    sk = [x for x in rows if x["verdict"] == "SKIP"]
    secs = [x["sec"] for x in rows if x["verdict"] != "SKIP"]
    print(f"\n[A 不误伤] {len(rows)} 条｜判 OK {ok}（{ok / len(rows):.1%}）"
          f"｜判 WRONG {len(wr)}（{len(wr) / len(rows):.1%}）｜跳过 {len(sk)}")
    if secs:
        s = sorted(secs)
        print(f"  耗时：均值 {sum(s) / len(s):.2f}s｜中位 {s[len(s) // 2]:.2f}s｜P90 {s[int(len(s) * 0.9)]:.2f}s")
    for x in wr:
        print(f"  ✗ {x['q']}\n     {x['reason']}｜信号 {x['signals']}")
    return rows


def test_B():
    """B 组：真实错答应被抓"""
    print("\n[B 能拦住] 三条线上真实错答")
    got = 0
    for q, a in REAL_BUGS:
        sig = fr.entity_signals(q, a)
        v = fr.review(q, a, CLIENT, CFG["model"], timeout=4.0, signals=sig)
        mark = "抓到" if v["verdict"] == "WRONG" else "漏掉"
        if v["verdict"] == "WRONG":
            got += 1
        print(f"  {mark} | {q}")
        print(f"         {v['reason']}｜信号 {sig}")
    print(f"  合计 {got}/{len(REAL_BUGS)}（{got / len(REAL_BUGS):.0%}）")
    return got


def test_C():
    """C 组：端到端——错误答案注入事实层，链路必须降级而不是发出去"""
    import fact_table
    from rag_engine import create_rag_engine

    eng = create_rag_engine(
        os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"), os.getenv("API_KEY"),
        os.getenv("MODEL"), os.getenv("EMBED_BACKEND", "cloud"),
        os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
    )

    q, bad = REAL_BUGS[0]
    real_lookup = fact_table.lookup
    fact_table.lookup = lambda _q, *a, **k: (
        {"answer": bad, "sources": ["《机动车驾驶证申领和使用规定》第七十五条"],
         "fact": "RENEW:DELAY"} if _q == q else real_lookup(_q, *a, **k)
    )
    try:
        t0 = time.time()
        out = eng.query(q, budget=10.0)
        sec = time.time() - t0
    finally:
        fact_table.lookup = real_lookup

    verdict = (out.get("fact_review") or {}).get("verdict")
    degraded = bool(out.get("degraded"))
    print(f"\n[C 端到端] 注入错答后提问：{q}")
    print(f"  耗时 {sec:.1f}s｜复核 {verdict}｜降级 {degraded}｜fact_hit {out.get('fact_hit')}")
    print(f"  实际返回前 160 字：{(out.get('answer') or '')[:160]}")
    passed = verdict == "WRONG" and degraded
    print(f"  {'通过：错答没有发出去' if passed else '不通过：错答仍被直接返回'}")

    # 反向：正确答案必须原样返回，不能被降级
    t0 = time.time()
    ok_out = eng.query("闯红灯怎么处罚", budget=10.0)
    print(f"\n  反向验证（正确答案不应被拦）：耗时 {time.time() - t0:.1f}s"
          f"｜降级 {bool(ok_out.get('degraded'))}｜复核 "
          f"{(ok_out.get('fact_review') or {}).get('verdict')}")
    print(f"  {'通过' if not ok_out.get('degraded') else '不通过：正确答案被误降级'}")
    return passed


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    a_limit = None
    if "--A" in sys.argv:
        a_limit = int(sys.argv[sys.argv.index("--A") + 1])

    t_all = time.time()
    if only in (None, "A"):
        test_A(a_limit)
    if only in (None, "B"):
        test_B()
    if only in (None, "C"):
        test_C()
    print(f"\n总耗时 {time.time() - t_all:.0f}s")


if __name__ == "__main__":
    main()
