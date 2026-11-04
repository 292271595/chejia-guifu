# -*- coding: utf-8 -*-
"""实验：事实层答案 + 大模型复核，是否可行？

要回答三个问题：
1. 对「正确答案」的误报率有多高？—— 高就没法用，好答案被拦是体验灾难
2. 对「跨制度错配」的命中率有多高？—— 低就白做
3. 单次复核耗时多久？—— 事实层本来是 0.1s 秒回，加这个变成几秒

样本：
- 正确样本：题库里事实层命中的题，问句 + 事实层真实答案（已人工确认正确）
- 错误样本：跨制度错配（把 A 题的答案配给 B 题的问句），外加三个真实历史事故

跑法：
    python tools/exp_fact_llm_review.py            # 全量
    python tools/exp_fact_llm_review.py --limit 20 # 先跑 20 条看趋势
"""
import os
import re
import sys
import io
import json
import time
import random

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from openai import OpenAI


def load_env():
    """从 .env 读生成侧配置（与 main.py 同口径）"""
    cfg = {}
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


REVIEW_PROMPT = """你是交通管理法规问答系统的质检员。

用户的问题：{q}

系统给出的回答：
{ans}

请判断：这个回答是否真正回答了用户问的那一件事？

判定 OK：回答针对的正是用户问的同一项业务、同一项制度、同一个情形。
判定 WRONG：回答讲的是另一项制度（例如问「驾驶资格延期」却答「延期换证」，
问「驾驶证注销后恢复」却答「驾驶证过期换证」，问「C1 能不能开」却答「摩托车换证」），
或者根本没有回答用户问的那件事。

判定时请注意：
- 回答比问题更详细、多解释了几句相关制度，只要主体针对的是同一件事，算 OK
- 不要在意味文风、格式、详略、有没有分点
- 只有当「回答的主体在讲另一件事」时，才算 WRONG

只输出一行，不要任何多余文字：
OK
或
WRONG: <一句话说明答成了哪件事>
"""


def review(client, model, q, ans, timeout=20.0):
    t0 = time.time()
    try:
        resp = client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是严格但不多疑的质检员，只输出 OK 或 WRONG: 原因。"},
                {"role": "user", "content": REVIEW_PROMPT.format(q=q, ans=ans[:900])},
            ],
            temperature=0.0,
            max_tokens=80,
        )
        txt = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {"verdict": "ERROR", "reason": f"{type(e).__name__}: {e}", "sec": time.time() - t0}
    sec = time.time() - t0
    head = txt.upper()
    if head.startswith("WRONG"):
        m = re.search(r"WRONG\s*[:：]?\s*(.*)", txt, re.S)
        return {"verdict": "WRONG", "reason": (m.group(1).strip() if m else txt)[:120], "sec": sec}
    return {"verdict": "OK", "reason": txt[:80], "sec": sec}


def build_correct_samples():
    """正确样本：题库里事实层命中的问句 + 真实答案"""
    import fact_table

    qs = []
    for fn in ("eval_100_part1.json", "eval_100_part2.json"):
        p = os.path.join(os.path.dirname(__file__), "..", "data", fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("questions") if isinstance(data, dict) else data
        for row in rows or []:
            if isinstance(row, dict) and row.get("q"):
                qs.append(row["q"])

    samples = []
    seen = set()
    for q in qs:
        r = None
        try:
            r = fact_table.lookup(q)
        except Exception:
            pass
        if not r:
            continue
        key = (q, r.get("fact"))
        if key in seen:
            continue
        seen.add(key)
        samples.append({"q": q, "a": r["answer"], "fact": r.get("fact"), "kind": "correct"})
    return samples


# 真实历史事故：这三条是线上一字不差发出去的错误答案
REAL_BUGS = [
    {
        "q": "我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗",
        "a": ("**延期换证：最长不超过 3 年；延期期间不得驾驶机动车。**\n\n"
              "依据第七十五条：机动车驾驶人因服兵役、出国（境）等原因，无法在规定时间内办理驾驶证期满换证、"
              "审验、提交身体条件证明的，可以在驾驶证有效期内或者有效期届满 1 年内向核发地车辆管理所申请延期办理。\n\n"
              "⚠️ 两个要点：\n1. 延期期限最长不超过 3 年；\n2. 延期期间不得驾驶机动车——延期只是保留驾驶资格，不是允许继续开车。"),
        "fact": "RENEW:DELAY",
        "kind": "real_bug",
        "note": "问第六十五条驾驶资格延期，答成第七十五条延期换证",
    },
    {
        "q": "70岁C1还能开吗",
        "a": ("**不能开了。** 年满 70 周岁的，不得驾驶 D、E、F 类摩托车，应当换领轻便摩托车（F）驾驶证。\n\n"
              "依据第七十六条：年龄在 70 周岁以上的，不得驾驶大型客车、牵引车、城市公交车、中型客车、"
              "大型货车、无轨电车和有轨电车。"),
        "fact": "AGE_DRIVE:70:MOTO",
        "kind": "real_bug",
        "note": "C1 到 70 岁仍可开，却套了摩托车模板",
    },
    {
        "q": "驾驶证因超期未换证被注销怎么恢复",
        "a": ("**驾驶证过期换证流程：** 驾驶证有效期满前 90 日内，向核发地车辆管理所申请换证。\n\n"
              "需要提交：身份证明、身体条件证明、近期一寸白底照片。逾期未换证的，不得驾驶机动车。"),
        "fact": "RENEW:EXPIRED",
        "kind": "real_bug",
        "note": "问注销后如何恢复，答成过期如何换证",
    },
]


def build_cross_samples(correct, n=40, seed=7):
    """错误样本：跨制度错配 —— 把 A 题的问句配上 B 题的答案

    真实事故就是这种形态（关键词把另一个处理器叫起来了），
    所以用「同领域不同处理器」的配对来模拟最有代表性。
    """
    rnd = random.Random(seed)
    by_fact = {}
    for s in correct:
        by_fact.setdefault((s.get("fact") or "").split(":")[0], []).append(s)

    out = []
    keys = list(by_fact)
    tries = 0
    while len(out) < n and tries < n * 60:
        tries += 1
        s = rnd.choice(correct)
        fam = (s.get("fact") or "").split(":")[0]
        others = [x for k in keys if k != fam for x in by_fact[k]]
        if not others:
            continue
        other = rnd.choice(others)
        if other["fact"] == s["fact"]:
            continue
        out.append({
            "q": s["q"],
            "a": other["a"],
            "fact": f"{s['fact']} <- {other['fact']}",
            "kind": "cross",
        })
    return out


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    cfg = load_env()
    client = OpenAI(base_url=cfg["API_BASE"], api_key=cfg["API_KEY"], timeout=30.0)
    model = cfg.get("MODEL", "glm-4-flash")

    correct = build_correct_samples()
    cross = build_cross_samples(correct)
    samples = correct + REAL_BUGS + cross
    if limit:
        # 限量时保证三类都有
        samples = correct[:limit] + REAL_BUGS + cross[: max(6, limit // 3)]

    print(f"样本：正确 {len(correct)}，真实事故 {len(REAL_BUGS)}，跨制度错配 {len(cross)}"
          f"（本次实际跑 {len(samples)}）")
    print(f"模型：{model}\n")

    rows = []
    for i, s in enumerate(samples, 1):
        r = review(client, model, s["q"], s["a"])
        r.update({"q": s["q"], "kind": s["kind"], "fact": s.get("fact"), "note": s.get("note")})
        rows.append(r)
        mark = {"OK": "·", "WRONG": "×", "ERROR": "!"}[r["verdict"]]
        print(f"[{i:>3}/{len(samples)}] {mark} {r['verdict']:<5} {r['sec']:>4.1f}s  {s['kind']:<9} {s['q'][:34]}")

    # ── 统计 ──
    def stat(kind):
        sub = [r for r in rows if r["kind"] == kind]
        if not sub:
            return None
        ok = sum(1 for r in sub if r["verdict"] == "OK")
        wr = sum(1 for r in sub if r["verdict"] == "WRONG")
        er = sum(1 for r in sub if r["verdict"] == "ERROR")
        secs = [r["sec"] for r in sub if r["verdict"] != "ERROR"]
        return {
            "n": len(sub), "ok": ok, "wrong": wr, "error": er,
            "ok_rate": ok / len(sub),
            "avg_sec": sum(secs) / len(secs) if secs else 0,
            "p90_sec": sorted(secs)[int(len(secs) * 0.9)] if secs else 0,
        }

    print("\n" + "=" * 60)
    cs = stat("correct")
    if cs:
        print(f"正确样本 {cs['n']} 条：判 OK {cs['ok']}（{cs['ok_rate']:.1%}），"
              f"误判 WRONG {cs['wrong']} 条（误报率 {cs['wrong'] / cs['n']:.1%}），错误 {cs['error']}")
    bs = stat("real_bug")
    if bs:
        print(f"真实事故 {bs['n']} 条：抓出 {bs['wrong']}（{bs['wrong'] / bs['n']:.1%}）")
    xs = stat("cross")
    if xs:
        print(f"跨制度错配 {xs['n']} 条：抓出 {xs['wrong']}（{xs['wrong'] / xs['n']:.1%}）")

    allsec = [r["sec"] for r in rows if r["verdict"] != "ERROR"]
    if allsec:
        allsec.sort()
        print(f"\n复核耗时：均值 {sum(allsec) / len(allsec):.2f}s，"
              f"中位 {allsec[len(allsec) // 2]:.2f}s，P90 {allsec[int(len(allsec) * 0.9)]:.2f}s，"
              f"最大 {allsec[-1]:.2f}s")

    out = os.path.join(os.path.dirname(__file__), "_exp_fact_review.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")

    # 打印误报与漏报样本，便于人工复核判得对不对
    fp = [r for r in rows if r["kind"] == "correct" and r["verdict"] == "WRONG"]
    if fp:
        print("\n--- 误报样本（模型说错，但答案是对的）---")
        for r in fp[:10]:
            print(f"  [{r['fact']}] {r['q']}")
            print(f"    理由：{r['reason']}")
    fnn = [r for r in rows if r["kind"] != "correct" and r["verdict"] == "OK"]
    if fnn:
        print("\n--- 漏报样本（模型说对，但答案是错的）---")
        for r in fnn[:10]:
            print(f"  [{r['fact']}] {r['q']}")
            print(f"    {r.get('note') or ''}")


if __name__ == "__main__":
    main()
