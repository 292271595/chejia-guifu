# -*- coding: utf-8 -*-
"""200 题压力测试执行器。

- 逐题调用 /api/query，记录答案、是否走确定性通道、来源数、耗时
- 按题库锚点自动判分（锚点全部取自知识库法规原文）
- 每完成一题立即落盘，支持断点续跑
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bank_200 import BANK  # noqa: E402

API = "http://127.0.0.1:8000/api/query"
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "run200_result.json")


def squash(s):
    """去空白与 markdown 强调符，避免"9分"/"9 分"类误判。"""
    return re.sub(r"[\s*`_]+", "", s or "")


# 否定语境词：答案常把错误说法引述出来再否定（"认为色盲可以申请C1——这是错的"），
# 此时 forbid 命中属误伤，必须识别。
_NEG = ("错", "不对", "不能", "不得", "并非", "不是", "禁止", "无", "误", "否则", "反之", "≠")


def _only_negated(text, phrase):
    """phrase 的所有出现是否都处在否定语境中。"""
    idx = text.find(phrase)
    if idx == -1:
        return False
    while idx != -1:
        win = text[max(0, idx - 60): idx + len(phrase) + 40]
        if not any(n in win for n in _NEG):
            return False
        idx = text.find(phrase, idx + 1)
    return True


def judge(answer, must, forbid):
    """三态判定：
    FAIL    锚点缺失（硬失败，答案未给出应有事实）
    SUSPECT forbid 命中且不在否定语境（需人工复核，不直接判错）
    PASS    锚点齐备且无错误表述
    """
    a = squash(answer)
    miss = ["|".join(g) for g in must if not any(squash(x) in a for x in g)]
    bad = [f for f in forbid if squash(f) in a and not _only_negated(a, squash(f))]
    if miss:
        return "FAIL", miss, bad
    if bad:
        return "SUSPECT", miss, bad
    return "PASS", miss, bad


def ask(q, timeout=240):
    body = json.dumps({"question": q}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {
            "ok": bool(d.get("success")),
            "answer": d.get("answer", "") or d.get("message", "") or "",
            "fact_hit": d.get("fact_hit"),
            "n_src": len(d.get("sources") or []),
            "elapsed": round(time.time() - t0, 2),
            "err": "",
        }
    except Exception as e:
        return {
            "ok": False, "answer": "", "fact_hit": None, "n_src": 0,
            "elapsed": round(time.time() - t0, 2), "err": f"{type(e).__name__}: {e}",
        }


def load():
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                return {x["idx"]: x for x in json.load(f)}
        except Exception:
            return {}
    return {}


def save(res):
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump([res[k] for k in sorted(res)], f, ensure_ascii=False, indent=1)


def main():
    res = load()
    todo = [i for i in range(1, len(BANK) + 1) if i not in res]
    print(f"共 {len(BANK)} 题，已完成 {len(res)}，待跑 {len(todo)}", flush=True)

    for n, i in enumerate(todo, 1):
        dom, q, must, forbid, grade, note = BANK[i - 1]
        r = ask(q)
        status, miss, bad = judge(r["answer"], must, forbid)
        item = {
            "idx": i, "domain": dom, "question": q, "grade": grade, "note": note,
            "must": must, "forbid": forbid,
            "status": status, "pass": status == "PASS", "miss": miss, "forbid_hit": bad,
            "fact_hit": r["fact_hit"], "n_src": r["n_src"],
            "elapsed": r["elapsed"], "ok": r["ok"], "err": r["err"],
            "answer": r["answer"],
        }
        res[i] = item
        save(res)
        ft = r["fact_hit"] or "-"
        print(f"[{i:03d}/{len(BANK)}] {r['elapsed']:5.1f}s {status:<7} fact={ft:<14} {q}", flush=True)

    st = [x["status"] for x in res.values()]
    print(f"\n完成：PASS {st.count('PASS')} / SUSPECT {st.count('SUSPECT')} / FAIL {st.count('FAIL')}  共 {len(res)}", flush=True)


if __name__ == "__main__":
    main()
