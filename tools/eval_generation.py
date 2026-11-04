# -*- coding: utf-8 -*-
"""生成层回归测试：答案准不准，不能只靠人眼看。

【为什么要有这个文件】
2026-11-04 那次「交通事故致人死亡负同等以上责任」答错，暴露的不是单题 bug，而是
**生成层根本没有断言**：检索层有 eval_retrieval.py（20 条 anchor 命中率），
事实层有 test_fact_table.py，但"模型最终写出来的那句话对不对"完全没人管。
于是同一个错法可以反复出现三次（v1 把不构罪说成犯罪、v2 含糊不给分界、v3 刑罚档串位），
每错一次都要人工发现一次。

本文件把两类断言固化下来：
  A. 端到端：跑真实答案，断言"必须出现的要点"和"绝不能出现的错法"
  B. 引用对账：不调模型，纯单测 citation_guard 的判定边界（含防误伤用例）

用法：
    python tools/eval_generation.py           # 全跑（含模型，约 1~2 分钟）
    python tools/eval_generation.py --no-llm  # 只跑事实层 + 引用对账单测，秒级
    python tools/eval_generation.py -v        # 打印每条答案
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(ROOT)

import citation_guard as cg  # noqa: E402
import fact_table  # noqa: E402

VERBOSE = "-v" in sys.argv
NO_LLM = "--no-llm" in sys.argv

# ============================================================
# A. 端到端用例
#   fact    期望命中的事实层处理器（None ≈ 走 RAG 生成）
#   must    必须全部出现的要点（正则）
#   must_not 绝不能出现的错法（正则）
# ============================================================
CASES = [
    # ── 本次事故（根因用例）：责任等级 × 死亡人数 的二维判决 ──
    {
        "q": "造成交通事故致人死亡，负同等以上责任如何处理",
        "fact": "ACCIDENT_DEATH",
        "must": [r"死亡\s*3\s*人以上", r"不构成交通肇事罪", r"第一百零一条"],
        "must_not": [
            # 错法一：把"同等责任致一人死亡"也说成犯罪
            r"同等责任.{0,12}(处|判).{0,10}(三年以下|有期徒刑)",
            # 错法二：只说"可能构成"却不给人数/责任分界
            r"^(?!.*死亡\s*3\s*人以上).*可能构成交通肇事罪",
        ],
        "why": "同等责任要求死亡三人以上才构罪，这是最容易答错的分界",
    },
    {
        "q": "死亡1人负同等责任构成犯罪吗",
        "fact": "ACCIDENT_DEATH",
        "must": [r"不构成交通肇事罪"],
        "must_not": [r"构成交通肇事罪，处"],
        "why": "一人死亡 + 同等责任 = 不构罪，不能答成构罪",
    },
    {
        "q": "负主要责任死亡2人会判刑吗",
        "fact": "ACCIDENT_DEATH",
        "must": [r"三年以上七年以下"],
        "must_not": [r"负全部责任", r"三年以下有期徒刑"],
        "why": "主要责任不能写成全部责任；死亡2人属特别恶劣情节",
    },
    {
        "q": "致人死亡负次要责任怎么处理",
        "fact": "ACCIDENT_DEATH_MINOR",
        "must": [r"不构成交通肇事罪"],
        "must_not": [],
        "why": "次要责任一律不构罪",
    },
    # ── 分项条文：逃逸尚不构成犯罪（记分 + 罚款 + 可并处拘留，不是吊销）──
    # must_not 原先写 "逃逸.{0,12}吊销机动车驾驶证"，但正确答案里为纠错会写
    # "逃逸构成犯罪的，还要吊销驾驶证"——这是**说明另一档**，不是把本档判成吊销。
    # 正则区分不了这个，改用正向验证四个核心结论。
    {
        "q": "造成交通事故后逃逸尚不构成犯罪怎么处理",
        "fact": None,
        "must": [r"(罚款|二千元|2000)", r"(十五日以下拘留|15\s*日以下拘留|拘留)",
                 r"12\s*分", r"(不吊销|驾驶证还在|不适用吊销)"],
        "must_not": [],
        "why": "第99条：罚款+可并处拘留+记12分；吊销和终生禁驾是构成犯罪的另一档，不能混",
    },
    # ── 罚则档位：饮酒驾营运车 ──
    {
        "q": "饮酒后驾驶营运机动车怎么处罚",
        "fact": None,
        "must": [r"(十五日|15\s*日).{0,6}拘留", r"(五千|5000)", r"(五年|5\s*年)"],
        "must_not": [r"(十日以下拘留|暂扣六个月)"],
        "why": "饮酒驾营运车是15日拘留+5000罚款+吊销+5年，与普通饮酒(暂扣6个月)不同档",
    },
]

# ============================================================
# B. 引用对账单测（不调模型）
#   期望 bad 数量；防误伤用例的期望恒为 0
# ============================================================
CITE_CASES = [
    ("依据《道路交通安全违法行为记分管理办法》第八条第四项，记12分。", "第八条", 0, "条号真实"),
    ("依据《道路交通安全违法行为记分管理办法》第十四条，不得申请。", "", 0, "条号存在但内容不符→只提示不摘除"),
    ("依据《道路交通安全法》第一百二十五条，处拘留。", "", 1, "道交法只有124条→编造"),
    ("依据《道路交通安全法》第九十九条，处二百元以上二千元以下罚款。", "第九十九条", 0, "真实条号"),
    ("依据《机动车驾驶证申领和使用规定》第七十九条第八项，可恢复驾驶资格。", "第七十九条", 0, "真实条号"),
    ("依据《中华人民共和国刑法》第一百三十三条之一，处拘役。", "", 0, "分条存在"),
    ("依据《中华人民共和国刑法》第一百三十三条之九，处拘役。", "", 1, "分条不存在"),
    ("依据《民法典》第一千一百七十九条，赔偿丧葬费。", "", 0, "超出本库收录范围，不判"),
    ("依据《民法典》第一千二百一十三条，先由保险赔。", "", 0, "本库收录范围内"),
    ("依据《道路交通事故处理办法》第八条，处理。", "", 1, "已废止法规不在库"),
    ("依据《中华人民共和国道路交通安全法实施条例》第九十二条，认定责任。", "", 0, "全名可识别"),
    ("依据道路交通安全法第一百零一条，吊销驾驶证。", "第一百零一条", 0, "无书名号的弱匹配"),
    ("依据《机动车登记规定》第五十二条，办理转籍。", "", 0, "真实条号"),
    ("依据《GB 38900 机动车安全技术检验项目和方法》第5.2条。", "", 0, "国标体例，不校验条号"),
]


def _check_text(text: str, must, must_not):
    miss = [p for p in must if not re.search(p, text)]
    bad = [p for p in must_not if re.search(p, text, re.M)]
    return miss, bad


# ============================================================
# C. 引用闸门的三条处置路径（降级 / 摘除 / 原样放行）
#    不调模型，直接调 _citation_gate，防止以后改阈值时无声破坏
# ============================================================
GATE_CASES = [
    # (名称, 答案, 期望降级, 期望答案里必须出现 / 必须消失的片段)
    ("大面积编造→整段降级",
     "依据《道路交通安全法》第一百四十条处拘留，依据《道路交通事故处理办法》第八条罚款。",
     True, None, None),
    ("单条编造→就地摘除",
     "依据《道路交通安全法》第九十九条处警告或罚款，另按《道路交通安全法》第一百三十条处理。",
     False, "已摘除", "另按《道路交通安全法》第一百三十条"),
    ("全部真实→原样放行",
     "依据《道路交通安全法》第九十九条，处警告或者五元以上五十元以下罚款。",
     False, None, "系统校验"),
    ("真实但未检索到→只提示不改写",
     "依据《道路交通安全法》第九十一条，处暂扣六个月驾驶证。",
     False, "未出现在本次检索到的原文片段中", None),
]

_GATE_CTX = (
    "《道路交通安全法》第九十九条 行人、乘车人、非机动车驾驶人违反道路交通安全法律、法规"
    "关于道路通行规定的，处警告或者五元以上五十元以下罚款。"
)


def run_gate():
    from rag_engine import RAGEngine
    e = object.__new__(RAGEngine)   # 只用纯函数，不初始化向量库和模型
    fake = [{"content": _GATE_CTX,
             "metadata": {"title": "中华人民共和国道路交通安全法 第九十九条"}, "score": 0.9}]
    print("=" * 72)
    print("C. 引用闸门处置路径")
    ok = 0
    for name, ans, exp_deg, must, must_not in GATE_CASES:
        out, deg = e._citation_gate(ans, _GATE_CTX, fake)
        good = (deg == exp_deg)
        if must and must not in out:
            good = False
        if must_not and must_not in out:
            good = False
        ok += good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}（降级={deg}）")
        if not good:
            print(f"         输出: {out}")
    print(f"  → {ok}/{len(GATE_CASES)} 通过")
    return ok, len(GATE_CASES)


def run_cite():
    print("=" * 72)
    print("B. 引用对账单测")
    ok = 0
    for ans, ctx, exp, why in CITE_CASES:
        r = cg.check(ans, ctx)
        got = len(r["bad"])
        passed = got == exp
        ok += passed
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] 期望{exp} 实际{got}  {why}")
        if not passed:
            print(f"         答案: {ans}")
            print(f"         判定: {[(c['status'], c.get('label') or c['law_raw']) for c in r['citations']]}")
    print(f"  → {ok}/{len(CITE_CASES)} 通过")
    return ok, len(CITE_CASES)


def run_e2e():
    print("=" * 72)
    print("A. 端到端答案断言")
    engine = None
    if not NO_LLM:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from rag_engine import create_rag_engine
        engine = create_rag_engine(
            os.path.join(ROOT, "chroma_db"),
            os.getenv("API_BASE"), os.getenv("API_KEY"), os.getenv("MODEL"),
            os.getenv("EMBED_BACKEND", "cloud"),
            os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
        )

    ok = 0
    ran = 0
    for c in CASES:
        q = c["q"]
        r = fact_table.lookup(q)
        if r:
            ans = r["answer"]
            hit = r.get("fact")
            print(f"  [{q[:26]}] 事实层 {hit}")
            # 事实层答案必须自己过一遍引用对账：它绕过了 query() 的闸门
            ctx = ans + " " + " ".join(s.get("snippet", "") for s in r.get("sources", []))
            cite_bad = cg.check(ans, ctx)["bad"]
            if cite_bad:
                print(f"    !! 事实层答案含编造引用: {[x.get('label') or x['law_raw'] for x in cite_bad]}")
        elif engine is not None:
            res = engine.query(q, top_k=6, budget=30)
            ans = res["answer"]
            print(f"  [{q[:26]}] 生成层")
        else:
            print(f"  [SKIP] {q}（--no-llm 未跑生成层）")
            continue

        ran += 1
        if VERBOSE:
            print("-" * 60)
            print(ans)
            print("-" * 60)

        if c.get("fact") is not None and (r is None or r.get("fact") != c["fact"]):
            print(f"  [FAIL] 期望命中 {c['fact']}，实际 {r.get('fact') if r else '未命中（走了生成层）'}")
            continue

        miss, bad = _check_text(ans, c["must"], c["must_not"])
        if miss or bad:
            print(f"  [FAIL] {c['why']}")
            for p in miss:
                print(f"         缺要点: {p}")
            for p in bad:
                print(f"         踩错法: {p}")
        else:
            ok += 1
            print(f"  [PASS] {c['why']}")
    print(f"  → {ok}/{ran} 通过")
    return ok, ran


def main():
    # 索引自检：没有它，所有引用判定都会静默失效
    if not cg.index().load():
        print("!! 引用索引加载失败，先跑 python tools/build_citation_index.py")
        return 1

    ok_c, n_c = run_cite()
    ok_g, n_g = run_gate()
    ok_e, n_e = run_e2e()
    total_ok, total = ok_c + ok_g + ok_e, n_c + n_g + n_e
    print("=" * 72)
    print(f"合计 {total_ok}/{total} 通过")
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
