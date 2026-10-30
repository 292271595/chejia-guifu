# -*- coding: utf-8 -*-
"""
处理器守卫一致性审计（机制性检查，非逐题打补丁）

问题背景：调度表 _HANDLERS 给每个处理器配了"主题正则"，意思是"这类问题归你"。
但处理器内部往往还有自己的守卫（return None）。两者不一致时会出现两类静默错配：

  A. 主题命中 → 处理器弃权 → 问题被后面的处理器抢答（答非所问，用户看不出错）
  B. 主题命中 → 处理器弃权 → 落进 RAG（漏答，回答质量下降）

本脚本对一批问题（默认 200 题压测题库 + 额外探针）逐题调用 fact_table.audit()，
把"命中主题但弃权"的处理器全部列出来，供人工判断是主题正则该收窄，还是守卫该放宽。

用法：python tools/audit_guards.py [-v]
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import fact_table as FT  # noqa: E402


def load_bank():
    """
    从 200 题压测题库取问题。

    不直接 import run200：它在 import 时会把 sys.stdout 换成自己的 TextIOWrapper，
    本进程后续 print 会撞上 "I/O operation on closed file"。改为子进程导出到缓存文件。
    """
    cache = os.path.join(ROOT, "tools", "bank_questions.json")
    if not os.path.exists(cache):
        import subprocess
        code = (
            "import sys, json, io;"
            f"sys.path.insert(0, r'{os.path.join(ROOT, 'tools')}');"
            "import run200;"
            f"open(r'{cache}', 'w', encoding='utf-8').write("
            "json.dumps([r[1] for r in run200.BANK], ensure_ascii=False))"
        )
        subprocess.run([sys.executable, "-c", code], cwd=os.path.join(ROOT, "tools"), check=False)
    try:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 无法读取题库缓存: {e}")
        return []


PROBES = [
    # 边界探针：同一主题、不同诉求，用来暴露"主题宽/守卫窄"的错配
    "考试有效期是几年", "驾驶证有效期是几年", "实习期多久",
    "年检标志还要贴吗", "检验标志电子凭证有效吗", "非营运小客车几年检一次",
    "考试不合格多久可以重新预约", "科目二考几次", "科目一多少分及格",
    "增驾A1需要什么条件", "60岁能考A1吗", "70岁以上还要每年体检吗",
    "注销后还能恢复吗", "吊销后多久能重考", "记满12分怎么处理",
    "超速20%记几分", "货车超载50%记几分", "C1能开9座车吗",
    "色弱能考驾照吗", "一只手掌缺失能考C1吗", "有癫痫病史能考驾照吗",
    "异地能换证吗", "驾驶证过期3年以上怎么办", "校车驾驶人有什么条件",
    "境外驾照怎么换国内驾照", "车辆过户要多久办完", "车牌丢了怎么补",
    "学法减分最多减几分", "醉驾几年不能重新申请", "驾驶证被锁定是什么意思",
]


def main():
    verbose = "-v" in sys.argv
    questions = load_bank() + PROBES
    seen, uniq = set(), []
    for q in questions:
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)

    mismatches = []
    for q in uniq:
        info = FT.audit(q)
        rows = info["rows"]
        winner = info["winner_match"]
        for row in rows:
            if row["answered"]:
                continue
            # 诉求门控已经把它挡在门外（订阅的诉求不匹配）→ 属于设计行为，不算错配
            if not row["aspect_ok"]:
                continue
            stolen = winner and winner != row["handler"]
            mismatches.append({
                "q": q,
                "handler": row["handler"],
                "kind": "被抢答" if stolen else ("落RAG" if not winner else "被其它处理器接手"),
                "winner": winner,
                "winner_fact": info["winner"],
                "error": row.get("error"),
            })

    print(f"共审计 {len(uniq)} 题，发现主题命中但处理器弃权 {len(mismatches)} 处\n")
    by_handler = {}
    for m in mismatches:
        by_handler.setdefault(m["handler"], []).append(m)
    for handler, items in sorted(by_handler.items(), key=lambda kv: -len(kv[1])):
        print(f"── {handler}  弃权 {len(items)} 次")
        for m in items[:6]:
            print(f"   [{m['kind']}] {m['q']}  → {m['winner_fact']}")
        if len(items) > 6:
            print(f"   ... 另 {len(items) - 6} 次")
        print()

    out = os.path.join(ROOT, "tools", "audit_guards.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(mismatches, f, ensure_ascii=False, indent=1)
    print(f"明细已写入 {out}")
    if verbose:
        for m in mismatches:
            print(m)


if __name__ == "__main__":
    main()
