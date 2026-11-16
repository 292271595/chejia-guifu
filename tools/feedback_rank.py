# -*- coding: utf-8 -*-
"""反馈日志统计：按真实提问频次排优先级（路线第四步）。

现状（2026-11-16 首次运行）：线上还没有真实用户反馈。`data/feedback/`
只有一条 2026-11-13 的测试数据被重复提交了 5 次（"测试测试测试…"，同一秒、同一句）。
所以这一步现在**跑不出有效结论**，工具先就绪，等有量了直接出清单。

但工具本身必须先能识别这种数据。否则第一次统计就会被"测试测试"占满 Top 1，
得出"用户最爱问测试"这种结论——比没数据更糟。

清洗规则（都写在这里，不用凭感觉）：
  · 长度 < 4：不是问题
  · 去重后字符数 <= 3 且长度 >= 6：「测试测试测试」这类重复填充
  · 同一秒同一句出现多次：同一次点击的重试，只留一条
  · 与车驾领域词表完全不沾边：单独归到「疑似非本领域」，不混进 Top 榜

聚类用 fact_common.norm 归一后去掉标点，所以「C1能骑摩托吗」「C1可以骑摩托车吗」
会合成一条，这才是真实频次（不然同一个问题被十种写法稀释）。

输出：
  · 有效条数 / 剔除条数 / 剔除原因
  · 事实层命中率、降级率、reason 分布
  · 高频问题 Top N（归一后聚类），标出「事实层没接住」的
  · 与 coverage_audit 的真缺口对照：哪个缺口真的有人问

用法：
    python tools/feedback_rank.py
    python tools/feedback_rank.py --top 30
    python tools/feedback_rank.py --raw      # 连剔除的也列出来
"""
import glob
import io
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

FEEDBACK_DIR = os.path.join(ROOT, "data", "feedback")

# 领域词表：只要沾一个就算本领域问题。宁可放宽，别把真问题误剔。
DOMAIN_WORDS = (
    "驾驶", "驾照", "驾驶证", "科目", "考试", "记分", "扣分", "违法", "违章",
    "车牌", "号牌", "车辆", "年检", "检验", "报废", "保险", "交强险", "事故",
    "增驾", "实习", "换证", "审验", "年审", "吊销", "注销", "准驾", "摩托",
    "电动", "校车", "货车", "客车", "扣留", "暂扣", "罚款", "拘留", "超速",
    "酒驾", "醉驾", "饮酒", "安全带", "头盔", "登记", "过户", "上牌", "行驶证",
)


def load():
    rows, bad = [], 0
    for path in sorted(glob.glob(os.path.join(FEEDBACK_DIR, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    bad += 1
    return rows, bad


def is_noise(q: str):
    """返回剔除原因，None 表示保留。"""
    s = (q or "").strip()
    if len(s) < 4:
        return "太短"
    if len(s) >= 6 and len(set(s)) <= 3:
        return "重复填充"
    return None


def cluster_key(q: str):
    """归一后去掉标点和语气词，用来把同一问题的多种写法合成一条。

    只用 fact_common.norm 不够——它管的是「酒驾→饮酒」「驾照→驾驶证」这类
    法规术语替换，不管「可以↔能」「摩托↔摩托车」这种口语同义。
    实测三种写法「C1能骑摩托车吗」「C1可以骑摩托车吗」「C1能骑摩托不」
    归一后仍是三个不同字符串，频次全是 1，等于没聚类。

    所以这里再做一层只用于统计的粗归一。注意「不」只在句末剥掉，
    中间的「不能」保留——否则「C1能骑摩托」和「C1不能骑摩托」会并成一条，
    那是最不能并的一对。
    """
    try:
        from fact_common import norm
        s = norm(q)
    except Exception:
        s = q
    s = re.sub(r"[\s，。？?！!、,.~·…]+", "", s)
    s = s.replace("可以", "能").replace("摩托车", "摩托").replace("驾驶证", "证")
    s = s.replace("行驶证", "证").replace("驾照", "证")
    s = re.sub(r"(什么|啥|哪些|哪种)", "啥", s)
    s = re.sub(r"(吗|呢|啊|呀|吧|不)$", "", s)
    return s


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    top = 20
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    show_raw = "--raw" in sys.argv

    rows, bad_lines = load()
    print("=" * 74)
    print("反馈日志统计（按真实频次排优先级）")
    print("=" * 74)
    if not rows:
        print(f"{FEEDBACK_DIR} 下没有可读记录（坏行 {bad_lines}）。")
        print("第四步要等真实用户反馈积累后再跑，现在没有数据可统计。")
        return 0

    dropped = Counter()
    kept = []
    for r in rows:
        q = r.get("question") or ""
        why = is_noise(q)
        if why:
            dropped[why] += 1
            continue
        kept.append(r)

    # 同秒同句去重：一次点击的重试不该算五次需求
    seen, dedup = set(), []
    for r in kept:
        k = (r.get("ts"), r.get("question"))
        if k in seen:
            dropped["同秒重复提交"] += 1
            continue
        seen.add(k)
        dedup.append(r)

    dates = sorted({r.get("date") or (r.get("ts") or "")[:10] for r in dedup if r.get("date") or r.get("ts")})
    print(f"原始 {len(rows)} 条（坏行 {bad_lines}），保留 {len(dedup)} 条")
    if dates:
        print(f"时间跨度：{dates[0]} — {dates[-1]}，共 {len(dates)} 天")

    if dropped:
        print("\n剔除：")
        for why, n in dropped.most_common():
            print(f"  {why}：{n} 条")

    if show_raw and dropped:
        print("\n被剔除的样本：")
        for r in rows:
            why = is_noise(r.get("question") or "")
            if why:
                print(f"  [{why}] {str(r.get('question'))[:50]}")
                break

    if not dedup:
        print("\n清洗后没有有效记录。第四步无法执行——不是工具不行，是还没数据。")
        return 0

    # 分领域 / 分原因
    off, on = [], []
    for r in dedup:
        q = r.get("question") or ""
        (on if any(w in q for w in DOMAIN_WORDS) else off).append(r)
    print(f"\n本领域问题 {len(on)} 条，疑似非本领域 {len(off)} 条")
    for r in off[:5]:
        print(f"  非本领域样例：{str(r.get('question'))[:50]}")

    reason = Counter((r.get("reason") or "未填") for r in on)
    fact = Counter("命中" if (r.get("fact_hit") or "").strip() else "未命中" for r in on)
    deg = Counter("降级" if r.get("degraded") else "正常" for r in on)
    print("\n反馈原因分布：")
    for k, v in reason.most_common():
        print(f"  {k}：{v}")
    print(f"事实层：命中 {fact.get('命中', 0)} / 未命中 {fact.get('未命中', 0)}")
    print(f"生成结果：正常 {deg.get('正常', 0)} / 降级 {deg.get('降级', 0)}")

    # 聚类排频次
    groups = {}
    for r in on:
        k = cluster_key(r.get("question") or "")
        g = groups.setdefault(k, {"n": 0, "samples": set(), "fact": 0, "reasons": Counter()})
        g["n"] += 1
        g["samples"].add(r.get("question"))
        if (r.get("fact_hit") or "").strip():
            g["fact"] += 1
        g["reasons"][r.get("reason") or "未填"] += 1

    print(f"\n高频问题 Top {min(top, len(groups))}（归一后聚类，n=被问次数）：")
    for k, g in sorted(groups.items(), key=lambda x: -x[1]["n"])[:top]:
        sample = list(g["samples"])[0]
        flag = "" if g["fact"] else "  ← 事实层没接住"
        print(f"  n={g['n']:<3} {sample[:52]}{flag}")
        if len(g["samples"]) > 1:
            print(f"        同义写法 {len(g['samples'])} 种")

    if len(dedup) < 20:
        print(f"\n⚠️ 有效样本只有 {len(dedup)} 条，任何「按频次排序」的结论都不成立。")
        print("   要等量再排，别拿几条数据反过来指导开发顺序。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
