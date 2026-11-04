# -*- coding: utf-8 -*-
"""实验二：模型复核到底能抓哪类错？

实验一暴露了问题：跨领域的离谱错配抓 95%，但三个真实事故只抓 1 个（33%）。
怀疑是"错配相似度"这个变量在起作用——越像真实场景的错配，越抓不住。

本实验把错配按相似度分三档，并对比两档提示词：

错配等级
  L1 同族   SCORE:9 ← SCORE:12        同一处理器族，最像真实事故
  L2 同模块 RENEW    ← AGE_DRIVE      同在"驾驶证"议题内
  L3 跨模块 ACCIDENT ← VEHICLE        风马牛不相及（实验一已测 95%）

提示词两档
  loose   宽松：判断"是不是同一件事"（实验一用的那版）
  strict  严格：找出回答针对的情形要件，逐项对照用户问句

关键约束：strict 版的 few-shot 示例刻意**不使用**三个真实事故的形态，
否则测真实事故的命中率就是泄题。测的是泛化，不是记忆。

    python tools/exp2_review_levels.py            # 全量
    python tools/exp2_review_levels.py --quick    # 每档 12 条，快速看趋势
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

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 提示词 ────────────────────────────────────────────────

LOOSE = """你是交通管理法规问答系统的质检员。

用户的问题：{q}

系统给出的回答：
{ans}

请判断：这个回答是否真正回答了用户问的那一件事？

判定 OK：回答针对的正是用户问的同一项业务、同一项制度、同一个情形。
判定 WRONG：回答讲的是另一项制度，或者根本没有回答用户问的那件事。

注意：
- 回答比问题更详细，只要主体针对的是同一件事，算 OK
- 不要在意味文风、格式、详略、有没有分点

只输出一行：
OK
或
WRONG: <一句话说明答成了哪件事>
"""

STRICT = """你在核查一个交通法规问答系统是否"答错了对象"。

用户的问题：{q}

系统给出的回答：
{ans}

不要评价回答对不对、全不全、啰不啰嗦。只判断一件事：这个回答讲的是不是用户问的那件事。

方法：
第一步，找出回答针对的具体情形——它在讲哪种业务、哪类车型、哪个年龄段、哪个触发前提。
第二步，对照用户的问题，用户问的是不是同一个情形。
第三步，不是同一个情形，判 WRONG。

"答错对象"的典型形态：
- 用户问「B2 增驾要什么条件」，回答讲的是「A1 增驾条件」——车型不对
- 用户问「号牌丢了怎么补」，回答讲的是「行驶证丢了怎么补」——证件不对
- 用户问「60 岁能不能开大货」，回答讲的是「70 岁以上不得驾驶大货」——年龄段不对
- 用户问「记 6 分要不要审验」，回答讲的是「记满 12 分怎么处理」——触发条件不对

注意：
- 用户问句往往很简短，他没提到的细节不算"情形不符"，只核对他明确问的那件事
- 回答顺带提到别的制度、做了对比说明，只要主体答的是用户问的那件事，判 OK
- 回答比问题详细很多，不算错

只输出一行：
OK
或
WRONG: <一句话说明回答讲的什么情形、用户问的什么情形>
"""

# ── 真实历史事故（线上一字不差发出去的错答）───────────────

REAL_BUGS = [
    {
        "q": "我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗",
        "a": ("**延期换证：最长不超过 3 年；延期期间不得驾驶机动车。**\n\n"
              "依据第七十五条：机动车驾驶人因服兵役、出国（境）等原因，无法在规定时间内办理驾驶证期满换证、"
              "审验、提交身体条件证明的，可以在驾驶证有效期内或者有效期届满 1 年内向核发地车辆管理所申请延期办理。\n\n"
              "⚠️ 两个要点：\n1. 延期期限最长不超过 3 年；\n2. 延期期间不得驾驶机动车——延期只是保留驾驶资格，不是允许继续开车。"),
        "fact": "RENEW:DELAY",
        "note": "问第六十五条驾驶资格延期，答成第七十五条延期换证",
    },
    {
        "q": "70岁C1还能开吗",
        "a": ("**不能开了。** 年满 70 周岁的，不得驾驶 D、E、F 类摩托车，应当换领轻便摩托车（F）驾驶证。\n\n"
              "依据第七十六条：年龄在 70 周岁以上的，不得驾驶大型客车、牵引车、城市公交车、中型客车、"
              "大型货车、无轨电车和有轨电车。"),
        "fact": "AGE_DRIVE:70:MOTO",
        "note": "C1 到 70 岁仍可开，却套了摩托车模板",
    },
    {
        "q": "驾驶证因超期未换证被注销怎么恢复",
        "a": ("**驾驶证过期换证流程：** 驾驶证有效期满前 90 日内，向核发地车辆管理所申请换证。\n\n"
              "需要提交：身份证明、身体条件证明、近期一寸白底照片。逾期未换证的，不得驾驶机动车。"),
        "fact": "RENEW:EXPIRED",
        "note": "问注销后如何恢复，答成过期如何换证",
    },
]

# 议题分组：用于区分"同模块"与"跨模块"
GROUPS = {
    "license": {"RENEW", "AGE_DRIVE", "AGE_APPLY", "RECOVER", "INTERNSHIP", "OVERSEAS",
                "FOREIGN", "EXAM", "STATUS_DRIVE", "PHYSICAL", "REVOKE", "PHYSICAL_REPORT"},
    "score": {"SCORE", "FULL_SCORE", "PERIOD", "REVIEW"},
    "vehicle": {"VEHICLE", "REG", "INS"},
    "accident": {"ACCIDENT", "ACCIDENT_DEATH"},
}


def group_of(fact):
    fam = (fact or "").split(":")[0]
    for g, fams in GROUPS.items():
        if fam in fams:
            return g
    return "other"


# ── 样本 ────────────────────────────────────────────────

def load_env():
    cfg = {}
    with open(os.path.join(HERE, "..", ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def correct_samples():
    import fact_table

    qs = []
    for fn in ("eval_100_part1.json", "eval_100_part2.json"):
        p = os.path.join(HERE, "..", "data", fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        for row in (data.get("questions") if isinstance(data, dict) else data) or []:
            if isinstance(row, dict) and row.get("q"):
                qs.append(row["q"])

    out, seen = [], set()
    for q in qs:
        try:
            r = fact_table.lookup(q)
        except Exception:
            r = None
        if not r:
            continue
        if (q, r.get("fact")) in seen:
            continue
        seen.add((q, r.get("fact")))
        out.append({"q": q, "a": r["answer"], "fact": r.get("fact"), "kind": "correct"})
    return out


def cross_samples(cor, level, n=25, seed=11):
    """按错配相似度生成样本

    L1 同族：fact 前缀相同、具体分支不同（SCORE:9 ← SCORE:12）
    L2 同模块：同一议题、不同前缀（RENEW ← AGE_DRIVE）
    L3 跨模块：不同议题（ACCIDENT ← VEHICLE）
    """
    rnd = random.Random(seed + {"L1": 0, "L2": 100, "L3": 200}[level])
    out, tried = [], 0
    while len(out) < n and tried < n * 120:
        tried += 1
        a, b = rnd.choice(cor), rnd.choice(cor)
        fa, fb = a.get("fact") or "", b.get("fact") or ""
        if fa == fb:
            continue
        fam_a, fam_b = fa.split(":")[0], fb.split(":")[0]
        ga, gb = group_of(fa), group_of(fb)
        if level == "L1":
            ok = fam_a == fam_b
        elif level == "L2":
            ok = ga == gb and fam_a != fam_b
        else:
            ok = ga != gb
        if not ok:
            continue
        out.append({"q": a["q"], "a": b["a"], "fact": f"{fa} <- {fb}",
                    "kind": level, "note": ""})
    return out


# ── 调用 ────────────────────────────────────────────────

def review(client, model, prompt, q, ans, timeout=20.0):
    t0 = time.time()
    try:
        resp = client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是质检员，只输出 OK 或 WRONG: 原因。"},
                {"role": "user", "content": prompt.format(q=q, ans=ans[:900])},
            ],
            temperature=0.0,
            max_tokens=90,
        )
        txt = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {"verdict": "ERROR", "reason": f"{type(e).__name__}: {e}", "sec": time.time() - t0}
    sec = time.time() - t0
    if txt.upper().startswith("WRONG"):
        m = re.search(r"WRONG\s*[:：]?\s*(.*)", txt, re.S)
        return {"verdict": "WRONG", "reason": (m.group(1).strip() if m else txt)[:120], "sec": sec}
    return {"verdict": "OK", "reason": txt[:80], "sec": sec}


def run_batch(client, model, prompt, samples, tag, show=True):
    rows = []
    for i, s in enumerate(samples, 1):
        r = review(client, model, prompt, s["q"], s["a"])
        r.update({"q": s["q"], "kind": s["kind"], "fact": s.get("fact"),
                  "note": s.get("note"), "prompt": tag})
        rows.append(r)
        if show:
            mark = {"OK": "·", "WRONG": "×", "ERROR": "!"}[r["verdict"]]
            print(f"  [{i:>3}/{len(samples)}] {mark} {r['verdict']:<5} {r['sec']:>4.1f}s "
                  f"{s['kind']:<8} {s['q'][:30]}")
    return rows


def summarize(rows, tag):
    print(f"\n{'=' * 64}\n提示词：{tag}")
    for kind, label in (("correct", "正确样本"), ("real_bug", "真实事故"),
                        ("L1", "L1 同族错配"), ("L2", "L2 同模块错配"), ("L3", "L3 跨模块错配")):
        sub = [r for r in rows if r["kind"] == kind]
        if not sub:
            continue
        ok = sum(1 for r in sub if r["verdict"] == "OK")
        wr = sum(1 for r in sub if r["verdict"] == "WRONG")
        er = sum(1 for r in sub if r["verdict"] == "ERROR")
        if kind == "correct":
            print(f"  {label:<12} {len(sub):>3} 条｜误报 {wr:>2} 条（{wr / len(sub):>5.1%}）")
        else:
            print(f"  {label:<12} {len(sub):>3} 条｜抓出 {wr:>2} 条（{wr / len(sub):>5.1%}）"
                  + (f"，失败 {er}" if er else ""))
    secs = sorted(r["sec"] for r in rows if r["verdict"] != "ERROR")
    if secs:
        print(f"  耗时：均值 {sum(secs) / len(secs):.2f}s｜中位 {secs[len(secs) // 2]:.2f}s"
              f"｜P90 {secs[int(len(secs) * 0.9)]:.2f}s")


def main():
    quick = "--quick" in sys.argv
    n = 12 if quick else 25

    cfg = load_env()
    client = OpenAI(base_url=cfg["API_BASE"], api_key=cfg["API_KEY"], timeout=30.0)
    model = cfg.get("MODEL", "glm-4-flash")

    cor = correct_samples()
    if quick:
        cor = cor[:20]
    # 公平对照：两档提示词跑完全相同的样本
    all_sets = {
        "correct": cor,
        "real_bug": REAL_BUGS,
        "L1": cross_samples(correct_samples(), "L1", n),
        "L2": cross_samples(correct_samples(), "L2", n),
        "L3": cross_samples(correct_samples(), "L3", n),
    }
    samples = []
    for k in ("correct", "real_bug", "L1", "L2", "L3"):
        for s in all_sets[k]:
            s.setdefault("kind", k)
            samples.append(s)
    print(f"样本合计 {len(samples)} 条：正确 {len(all_sets['correct'])}、真实事故 3、"
          f"L1 {len(all_sets['L1'])}、L2 {len(all_sets['L2'])}、L3 {len(all_sets['L3'])}")
    print(f"模型 {model}\n")

    all_rows = []
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    plans = [("loose", LOOSE), ("strict", STRICT)]
    if only:
        plans = [(t, p) for t, p in plans if t == only]
    for tag, prompt in plans:
        print(f"\n──── 提示词 {tag} ────")
        rows = run_batch(client, model, prompt, samples, tag)
        all_rows.extend(rows)
        summarize(rows, tag)

    out = os.path.join(HERE, "_exp2_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")

    # 逐条看关键差异：strict 比 loose 多抓到什么、多误杀什么
    def idx(rows, tag):
        return {(r["q"], r["kind"], r["fact"]): r for r in rows if r["prompt"] == tag}
    lo, st = idx(all_rows, "loose"), idx(all_rows, "strict")
    gained = [k for k in st if k in lo and lo[k]["verdict"] != "WRONG" and st[k]["verdict"] == "WRONG"]
    lost = [k for k in st if k in lo and lo[k]["verdict"] == "WRONG" and st[k]["verdict"] != "WRONG"]
    print(f"\nstrict 比 loose 多抓 {len(gained)} 条：")
    for k in gained[:10]:
        print(f"  + {k[1]:<8} {k[0][:32]}｜{st[k]['reason'][:70]}")
    print(f"strict 比 loose 少抓 {len(lost)} 条：")
    for k in lost[:10]:
        print(f"  - {k[1]:<8} {k[0][:32]}")

    # strict 对真实事故的判定理由
    print("\n真实事故在 strict 下的判定：")
    for r in all_rows:
        if r["kind"] == "real_bug" and r["prompt"] == "strict":
            print(f"  {r['verdict']:<5} {r['q'][:30]}｜{r['reason'][:80]}")


if __name__ == "__main__":
    main()
