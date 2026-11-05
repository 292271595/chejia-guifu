# -*- coding: utf-8 -*-
"""多轮追问改写验收

分四组，前两组不调模型（毫秒级，可反复跑），后两组要真实调用。

  A 组  need_resolve 判定：哪些该改写、哪些必须放过（纯正则）
  B 组  validate 防幻觉：构造模型可能给出的坏输出，必须全部拒收
  C 组  真实追问链：10 条链，看改写成什么 + 事实层命中率变化
  D 组  完整问句保护：带历史也不该动一句本来就完整的话

用法：
  python tools/test_context_rewrite.py           # A + B
  python tools/test_context_rewrite.py --llm     # 再跑 C + D
  python tools/test_context_rewrite.py --llm --show   # 打印改写全文
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def hist(*pairs):
    """把 ('问','答') 元组列表转成 history 结构。"""
    out = []
    for q, a in pairs:
        out.append({"role": "user", "content": q})
        out.append({"role": "assistant", "content": a})
    return out


H1 = hist(("C1 能开什么车", "C1 可以开小型、微型载客汽车以及轻型、微型载货汽车。"))

# ─── A 组 ───────────────────────────────────────────────
def group_a():
    import context_rewrite as C

    print("\n[A] need_resolve 判定（该补全 vs 必须放过）")

    should = [
        "那 B2 呢", "那要多久", "要多久", "可以吗", "还有呢", "那如果过期两年呢",
        "B2 呢", "这个要多少钱", "那扣分了怎么办", "行不行",
    ]
    should_not = [
        "C1 能开什么车", "醉驾几年能考", "驾驶证过期了怎么换证", "实习期能上高速吗",
        "年检多久一次", "换证要带什么材料", "车辆抵押了能卖吗", "超速怎么处罚",
        "报废车有补贴吗", "记满12分怎么处理",
    ]

    for q in should:
        check(f"该补全：{q}", C.need_resolve(q, H1) is True)

    for q in should_not:
        check(f"不该动：{q}", C.need_resolve(q, H1) is False)

    # 无历史时一律不改写
    for q in ["那 B2 呢", "要多久"]:
        check(f"无历史不动：{q}", C.need_resolve(q, []) is False)

    # 历史清洗：脏数据不该让流程炸
    check("历史为空列表", C.need_resolve("那 B2 呢", []) is False)
    check("历史含非法结构", C.need_resolve("那 B2 呢", [{"x": 1}, None, "abc"]) is False)
    check("历史只有 assistant", C.need_resolve("那 B2 呢", [{"role": "assistant", "content": "x"}]) is False)


# ─── B 组 ───────────────────────────────────────────────
def group_b():
    import context_rewrite as C

    print("\n[B] validate 防幻觉（模型给的坏输出必须拒收）")
    q = "那要多久"
    h = H1

    bad = [
        ("醉驾 5 年后能重新考吗", "新增数字 5"),
        ("A1 增驾要多久", "新增车型 A1"),
        ("抱歉，我无法判断你想问什么", "拒答措辞"),
        ("那要多久", "与原句相同"),
        ("", "空输出"),
        ("醉驾五年后能重新考吗", "新增中文期限"),
        ("那超过两年怎么办", "仍以指代词开头：主语没补进去"),
        ("C1 过期超过两年呢", "仍以「呢」结尾：没补完"),
        ("科目一", "不是问句：把残句补成了答案片段"),
        ("C1 驾驶证期满换证需要准备身份证明原件和复印件、县级以上医疗机构出具的体检证明、"
         "近期一寸白底免冠彩色照片三张、填写完整的机动车驾驶证申请表，并且要处理好所有违章记录", "过长"),
    ]
    for txt, why in bad:
        ok, reason = C.validate(q, txt, h)
        check(f"拒收（{why}）", ok is False, f"实际 ok={ok} reason={reason}")

    good = [
        ("C1 换证要多久", "补全主语"),
        ("换证大概要多久", "补全但不改口语"),
        ("驾驶证换证大概要多久", "补齐主语"),
        # 注意：这里不能写「驾驶证过期超过两年怎么办」——历史里没有「两年」，
        # 补出「两年」就是凭空新增期限，本就该被中文数量词校验拦下（见 bad 列表）

    ]
    for txt, why in good:
        ok, reason = C.validate(q, txt, h)
        check(f"接受（{why}）", ok is True, f"实际 reason={reason}")

    # 数字/车型来自历史时应当放行
    h2 = hist(("B2 增驾要多久", "一般需要 3 个月。"))
    ok, reason = C.validate("那要多久", "B2 增驾要多久", h2)
    check("历史里的车型可复用", ok is True, f"reason={reason}")

    # 多行输出取第一行而不是整条丢弃
    ok, reason = C.validate("那要多久", "C1 换证要多久\n（补充说明）", h)
    check("多行取首行", ok is True, f"reason={reason}")


# ─── C 组 ───────────────────────────────────────────────
CHAINS = [
    ("增驾换车型", [
        ("C1 能开什么车", "C1 可以开小型、微型载客汽车以及轻型、微型载货汽车。"),
    ], "那 B2 呢"),
    ("问期限", [
        ("C1 驾驶证过期了怎么换证", "超过有效期未满 1 年可以正常办理期满换证。"),
    ], "那要多久"),
    ("酒驾转醉驾", [
        ("饮酒驾驶怎么处罚", "处暂扣六个月机动车驾驶证，并处一千元以上二千元以下罚款。"),
    ], "那醉驾呢"),
    ("年检转新车", [
        ("年检多久一次", "小型非营运载客汽车 6 年内每 2 年检验 1 次。"),
    ], "那新车呢"),
    ("换证转外地", [
        ("换证要带什么材料", "需要身份证明、身体条件证明和驾驶证原件。"),
    ], "那外地怎么办"),
    ("实习期转扣分", [
        ("实习期能上高速吗", "实习期内驾驶机动车上高速公路必须由持相应或者更高准驾车型驾驶证三年以上的驾驶人陪同。"),
    ], "那扣满 12 分呢"),
    ("过户转夫妻", [
        ("车辆过户需要什么材料", "需要现机动车所有人身份证明、所有权转移证明、登记证书和行驶证。"),
    ], "那夫妻之间呢"),
    ("超速转幅度", [
        ("超速怎么处罚", "超速 20% 以下一般处警告或者二十元以上二百元以下罚款。"),
    ], "那超速 50% 以上呢"),
    ("记分转科目", [
        ("记满 12 分怎么办", "公安机关交通管理部门扣留驾驶证，驾驶人应当参加学习并考试科目一。"),
    ], "那要考科目一吗"),
    ("报废转补贴", [
        ("车辆报废怎么办理", "将机动车交售给报废机动车回收拆解企业，取得回收证明后办理注销登记。"),
    ], "那有补贴吗"),
]


def group_c(client, model):
    import context_rewrite as C
    import fact_table

    print("\n[C] 真实追问链（改写效果 + 事实层命中对比）")
    print(f"  {'链':<10} {'追问':<18} {'改写后':<26} {'命中(不改)':<12} {'命中(改写)'}")
    print("  " + "-" * 92)

    stat = {"resolved": 0, "hit_before": 0, "hit_after": 0, "rejected": 0, "sec": []}
    for name, pairs, follow in CHAINS:
        h = hist(*pairs)
        r = C.resolve(follow, h, client, model, timeout=2.0)
        stat["sec"].append(r["sec"])

        before = fact_table.lookup(follow)
        after = fact_table.lookup(r["question"])
        b = (before or {}).get("fact") or "-"
        a = (after or {}).get("fact") or "-"

        if r["resolved"]:
            stat["resolved"] += 1
        elif not r["skipped"]:
            stat["rejected"] += 1
        if before:
            stat["hit_before"] += 1
        if after:
            stat["hit_after"] += 1

        print(f"  {name:<10} {follow:<18} {r['question'][:24]:<26} {b:<12} {a}")
        if not r["resolved"]:
            print(f"       └ 未改写：{r['reason']}")

    n = len(CHAINS)
    print(f"\n  改写成功 {stat['resolved']}/{n}，拒收 {stat['rejected']}")
    print(f"  事实层命中：改写前 {stat['hit_before']}/{n} → 改写后 {stat['hit_after']}/{n}")
    secs = sorted(stat["sec"])
    if secs:
        print(f"  改写耗时：中位 {secs[len(secs)//2]:.2f}s，最大 {secs[-1]:.2f}s")
    return stat


# ─── D 组 ───────────────────────────────────────────────
def group_d(client, model):
    import context_rewrite as C

    print("\n[D] 完整问句保护（带历史也不该改写）")
    full = [
        "C1 驾驶证过期两年了还能恢复吗",
        "醉驾几年能重新考驾驶证",
        "实习期上高速没有陪同怎么处罚",
        "车辆抵押了还能过户吗",
    ]
    h = hist(*CHAINS[0][1])
    bad = 0
    for q in full:
        r = C.resolve(q, h, client, model, timeout=2.0)
        ok = not r["resolved"]
        if not ok:
            bad += 1
        check(f"未改写：{q}", ok, f"被改成「{r['question']}」")
    print(f"  误改 {bad}/{len(full)}")


def main():
    from dotenv import load_dotenv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))

    group_a()
    group_b()

    if "--llm" in sys.argv:
        import openai
        key = os.getenv("API_KEY", "")
        if not key:
            print("\n!! 没有 API_KEY，跳过 C/D 组")
            return
        gen_backend = os.getenv("GEN_BACKEND", "zhipu")
        base = os.getenv("API_BASE", "")
        if not base:
            base = ("https://api.siliconflow.cn/v1" if gen_backend == "sili"
                    else "https://open.bigmodel.cn/api/paas/v4")
        model = os.getenv("MODEL", "glm-4-flash")
        client = openai.OpenAI(api_key=key, base_url=base)
        print(f"\n模型 {model} @ {base}")

        group_c(client, model)
        group_d(client, model)

    print(f"\n{'='*60}\n断言通过 {PASS}，失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
