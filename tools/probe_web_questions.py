# -*- coding: utf-8 -*-
"""网上真实高频问题的端到端压测。

问题来源：各地交管公众号/政府门户的「车驾管高频问答」，均为用户实际提问原话。
用法：
    python tools/probe_web_questions.py           # 跑全部（含生成层，慢）
    python tools/probe_web_questions.py --fast    # 只跑事实层命中判定
"""
import io
import os
import re
import sys
import json
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
if "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

FAST = "--fast" in sys.argv

# (问题, 必须出现的关键点[], 绝不能出现[])
CASES = [
    ("夫妻两人名下各有一辆车，可以直接互换号牌吗",
     [r"(同一|同一个).{0,6}所有人", r"(不可以|不能)"], []),
    ("色弱可以申领机动车驾驶证吗",
     [r"(可以|能)", r"红绿色盲"], [r"色弱.{0,10}(不能|不得)申领"]),
    ("C5驾驶证需要多久体检一次",
     [r"(3|三)\s*年", r"(30|三十)\s*日"], []),
    ("驾驶证记满12分，满分教育可以在违法处理地参加吗",
     [r"可以"], []),
    ("C1驾驶证实习期内可以增驾摩托车吗",
     [r"(不能|不可以|办不了|不予受理)", r"(12|十二)\s*个月"], []),
    ("购车发票上的名字和身份证不一致，能上牌吗",
     [r"(不能|不予|不可以)"], []),
    ("用临时身份证上的牌，拿到正式身份证后要变更吗",
     [r"(需要|应当)", r"(30|三十)\s*日"], []),
    ("哪些驾驶人需要参加审验教育",
     [r"(AB|A、B|大型客车|牵引车|大型货车)", r"校车", r"70"], []),
    ("车子还有违章没处理，可以过户吗",
     [r"(不可以|不能|不予)"], []),
    ("收到正式号牌后，临时号牌还能用几天",
     [r"(3|三)\s*日"], []),
    ("C2考过科目一了，要去别的城市，剩下的科目怎么办",
     [r"变更考试地", r"(三|3)\s*次"], []),
    ("6年免检的车，第2年和第4年要上线检测吗",
     [r"(不需要|不用|免|不上)", r"第\s*6\s*年.{0,14}(上线|检验)"], []),
    ("实习期可以独自开车上高速吗",
     [r"(不能|不可以|不得)", r"(三|3)\s*年"], []),
    ("学法减分一个记分周期最多能减几分",
     [r"(6|六)\s*分"], []),
    ("驾驶证到期前多久可以换证",
     [r"(90|九十)\s*(日|天)"], []),
    ("70岁以上的驾驶人还要每年提交体检证明吗",
     [r"(每年|一年|每1年)", r"(身体条件证明|体检)"], []),
    ("驾驶证是外地的，能在本地换证吗，要回老家吗",
     [r"(可以|能|不需要回|不用回)"], [r"必须回.{0,8}核发地"]),
    ("驾驶证丢了怎么补办",
     [r"(补领|补办)"], []),
    ("车子报废了，注销证明在哪里下载",
     [r"(12123|电子)"], []),
    ("电子驾驶证申请不成功是什么原因",
     [r"(照片|相片|图像)"], []),
]


def _check(text, must, must_not):
    miss = [p for p in must if not re.search(p, text)]
    bad = [p for p in must_not if re.search(p, text, re.M)]
    return miss, bad


def main():
    import fact_table
    import citation_guard

    engine = None
    if not FAST:
        from rag_engine import create_rag_engine
        engine = create_rag_engine(
            os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"),
            os.getenv("API_KEY"), os.getenv("MODEL"),
            os.getenv("EMBED_BACKEND", "local"),
            os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""))

    rows = []
    for q, must, must_not in CASES:
        t = time.time()
        fr = fact_table.lookup(q)
        rec = {"q": q, "fact": fr.get("fact") if fr else None}
        if fr:
            ans, srcs, layer = fr["answer"], fr.get("sources", []), "fact"
        elif engine:
            r = engine.query(q, top_k=6, budget=30)
            ans, srcs = r["answer"], r.get("sources", [])
            layer = "rag" + ("+degraded" if r.get("degraded") else "")
        else:
            rows.append({**rec, "layer": "skip"})
            print(f"[SKIP] {q}")
            continue

        ctx = ans + " " + " ".join(
            (s.get("snippet") or "") + (s.get("metadata", {}).get("title") or "")
            for s in srcs)
        cg = citation_guard.check(ans, ctx)
        miss, bad = _check(ans, must, must_not)
        ok = not miss and not bad and not cg["bad"]
        rec.update({"layer": layer, "ok": ok, "miss": miss, "bad": bad,
                    "fake": [(x["status"], x.get("label") or x["law_raw"])
                             for x in cg["bad"]],
                    "ans": ans, "sec": round(time.time() - t, 1)})
        rows.append(rec)
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] ({rec['sec']}s) {layer}:{rec['fact']} | {q}")
        if not ok:
            if miss:
                print("   缺:", miss)
            if bad:
                print("   踩错:", bad)
            if cg["bad"]:
                print("   编造引用:", rec["fake"])
            print("   答案:", ans.replace("\n", " ")[:300])

    n_ok = sum(1 for r in rows if r.get("ok"))
    print("\n" + "=" * 72)
    print(f"合计 {n_ok}/{len(rows)} 通过")
    json.dump(rows, open(os.path.join(ROOT, "tools", "_web_probe.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
