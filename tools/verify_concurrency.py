# -*- coding: utf-8 -*-
"""并发验收：复核加进去之后，多人同时问会不会崩。

此前测过 8 并发零降级，但那是在没有复核的版本上。现在每道事实层题多一次模型调用，
而 glm-4-flash 免费档有并发限制。如果并发下复核集体超时，这套机制在线上等于
「只加延迟、不产生保护」——所以必须实测，不能靠推理。

指标：
  成功率     有多少题正常返回（不是异常）
  降级率     有多少题退成检索原文
  复核有效   有多少题真正完成了复核（SKIP 表示超时/异常放行，等于没保护）
  耗时分布   墙钟时间，对照云托管 callContainer 的 15 秒硬上限
"""
import os
import sys
import io
import time
import random
import threading

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

from rag_engine import create_rag_engine  # noqa: E402

CONCURRENCY = int(os.environ.get("CONC", "8"))
ROUNDS = int(os.environ.get("ROUNDS", "2"))

QS = [
    "闯红灯怎么处罚",
    "我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗",
    "遮挡号牌扣几分",
    "70岁C1还能开吗",
    "驾驶证因超期未换证被注销怎么恢复",
    "交强险保额是多少",
    "科目一考试不合格多久可以重新预约",
    "实习期内可以学法减分吗",
    "占用应急车道行驶记几分",
    "6年免检的车第2年要上线检测吗",
    "醉驾吊销几年",
    "驾驶证被暂扣期间开车怎么处理",
    "满分教育可以异地参加吗",
    "车辆改色需要办理变更登记吗",
    "高速公路上违法停车记几分",
    "不礼让行人怎么处罚",
]

eng = create_rag_engine(
    os.path.join(ROOT, "chroma_db"), os.getenv("API_BASE"), os.getenv("API_KEY"),
    os.getenv("MODEL"), os.getenv("EMBED_BACKEND", "cloud"),
    os.getenv("EMBED_API_KEY", ""), os.getenv("EMBED_API_BASE", ""),
)
# 预热，避免首个请求吃加载时间
eng.query("闯红灯怎么处罚", budget=12.0)

lock = threading.Lock()
results = []


def worker(q, idx):
    t0 = time.time()
    try:
        r = eng.query(q, budget=12.0)
        row = {
            "q": q, "sec": time.time() - t0, "ok": True,
            "degraded": bool(r.get("degraded")),
            "review": (r.get("fact_review") or {}).get("verdict") or "-",
            "reason": (r.get("fact_review") or {}).get("reason") or "",
            "fact": r.get("fact_hit"),
        }
    except Exception as e:
        row = {"q": q, "sec": time.time() - t0, "ok": False,
               "degraded": False, "review": "ERR", "fact": f"{type(e).__name__}: {e}"}
    with lock:
        results.append(row)
        flag = "ERR" if not row["ok"] else ("DEG" if row["degraded"] else "OK ")
        extra = f" ({row['reason'][:60]})" if row["review"] not in ("OK", "WRONG") else ""
        print(f"  [{len(results):>2}] {flag} {row['sec']:>5.1f}s 复核 {row['review']:<5} {q[:30]}{extra}")


for rnd in range(ROUNDS):
    print(f"\n──── 第 {rnd + 1} 轮：{CONCURRENCY} 并发 ────")
    results.clear()
    t0 = time.time()
    threads = []
    batch = QS[:CONCURRENCY]
    random.shuffle(batch)
    for q in batch:
        th = threading.Thread(target=worker, args=(q, len(threads)))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    wall = time.time() - t0

    err = sum(1 for r in results if not r["ok"])
    deg = sum(1 for r in results if r["degraded"])
    reviewed = sum(1 for r in results if r["review"] in ("OK", "WRONG"))
    skipped = sum(1 for r in results if r["review"] == "-")
    secs = sorted(r["sec"] for r in results)
    print(f"\n  墙钟 {wall:.1f}s（云托管上限 15s）｜最慢单题 {secs[-1]:.1f}s"
          f"｜中位 {secs[len(secs) // 2]:.1f}s")
    print(f"  异常 {err}｜降级 {deg}｜复核生效 {reviewed}/{len(results)}"
          f"｜复核跳过 {skipped}（跳过=超时放行，等于没保护）")
    if wall >= 15:
        print("  ⚠ 墙钟已超云托管上限")
    if skipped:
        print("  ⚠ 有请求没被复核保护到")
