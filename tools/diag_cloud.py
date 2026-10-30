#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
云托管线上环境一键体检

用法：
    python tools/diag_cloud.py https://xxxx.sh.run.tcloudbase.com
    python tools/diag_cloud.py https://xxxx.sh.run.tcloudbase.com --full   # 含 RAG 端到端计时

检查项：
    1. /health        → 引擎是否初始化、嵌入后端/维度/是否熔断
    2. /api/status    → 向量条数（必须 735）
    3. /api/query     → 事实层（应 0.x 秒）与 RAG（应 <12s）各一发，验 blocks 结构

任何一项不符合预期都会给出具体原因和处置建议，不是只打一行 FAIL。
"""
import io
import json
import sys
import time
import urllib.request
import urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

FACT_Q = "C1 能开什么车"
# 必须选跨主题复合问句：单点问题会被确定性事实层 0.x 秒接走，测不到 RAG 生成链路。
RAG_Q = "驾驶证丢了同时车辆年检也过期了，分别要怎么办，会不会被处罚"

PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"


def req(base, path, body=None, timeout=40):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    t = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), time.time() - t, None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:400]
        return None, time.time() - t, f"HTTP {e.code}: {raw}"
    except Exception as e:
        return None, time.time() - t, f"{type(e).__name__}: {e}"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    base = sys.argv[1]
    full = "--full" in sys.argv

    print("=" * 68)
    print("云托管体检 →", base)
    print("=" * 68)

    problems = []

    # ---------- 1. /health ----------
    print("\n[1/3] GET /health")
    h, el, err = req(base, "/health", timeout=15)
    if err:
        print(f"  {FAIL} 请求失败（{el:.2f}s）：{err}")
        print("         → 实例没起来，或域名不对。看控制台「日志」最后 30 行。")
        problems.append("health 不可达")
    else:
        print(f"  {PASS} {el*1000:.0f}ms  {json.dumps(h, ensure_ascii=False)}")
        if not h.get("engine"):
            err_msg = h.get("engine_error") or "未知"
            print(f"  {FAIL} engine=false，原因：{err_msg}")
            if "API_KEY" in err_msg and "EMBED" not in err_msg:
                print("         → 缺 API_KEY（生成侧）：服务设置 → 环境变量补上后重启")
            elif "EMBED" in err_msg or "embed" in err_msg.lower():
                print("         → 嵌入侧异常：查 EMBED_API_KEY 是否填了、硅基流动是否欠费")
            else:
                print("         → 看这条原因本身，通常是 Key 有空格或维度不匹配")
            problems.append("引擎未初始化")
        if h.get("embed_dim") not in (1024, None):
            print(f"  {FAIL} embed_dim={h.get('embed_dim')}，应为 1024")
            problems.append("维度错误")
        if h.get("embed_vector") is False:
            print(f"  {WARN} 向量通道已熔断，当前退化为纯 BM25（能答但口语问句召回下降）")

    # ---------- 2. /api/status ----------
    print("\n[2/3] GET /api/status")
    s, el, err = req(base, "/api/status", timeout=20)
    if err:
        print(f"  {FAIL} 失败：{err}")
        problems.append("status 不可达")
    else:
        n = s.get("document_count", 0)
        if n == 735:
            print(f"  {PASS} document_count={n}（与本地库一致）")
        elif n == 0:
            print(f"  {FAIL} document_count=0，镜像里没读到 chroma_db")
            print("         → 确认 .dockerignore 没排除 chroma_db、构建拉的是最新 main")
            problems.append("向量库为空")
        else:
            print(f"  {WARN} document_count={n}，预期 735（可能库版本与代码不一致）")

    # ---------- 3. /api/query ----------
    print("\n[3/3] POST /api/query")
    d, el, err = req(base, "/api/query", {"question": FACT_Q, "format": "blocks"})
    if err:
        print(f"  {FAIL} 事实层题失败：{err}")
        problems.append("query 不可达")
    else:
        ok = d.get("success") and d.get("blocks")
        tag = PASS if ok else FAIL
        print(f"  {tag} 事实层「{FACT_Q}」{el*1000:.0f}ms  blocks={len(d.get('blocks') or [])} "
              f"fact_hit={bool(d.get('fact_hit'))} source={d.get('source')}")
        if el > 3:
            print(f"  {WARN} 事实层不该这么慢，可能是冷启动或无缓存")
        if not ok:
            problems.append("query 返回空")
            print("         →", json.dumps(d, ensure_ascii=False)[:300])

    if full:
        print(f"\n[+] RAG 端到端（复合长问句，最坏情况）")
        d2, el2, err2 = req(base, "/api/query", {"question": RAG_Q, "format": "blocks"}, timeout=45)
        if err2:
            print(f"  {FAIL} {err2}")
            problems.append("RAG 超时或报错")
        else:
            txt = " ".join(b.get("text", "") for b in (d2.get("blocks") or []))
            degraded = "AI 整理超时" in txt
            if d2.get("fact_hit"):
                print(f"  {WARN} {el2:.2f}s 该问句被事实层接走（fact_hit=true），未测到 RAG 生成链路")
                print("        → 换一条跨主题问句重测，否则这条的耗时没有参考价值")
            elif degraded:
                print(f"  {WARN} {el2:.2f}s 降级返回法规原文（生成超时，但用户仍能看到内容）")
            elif el2 > 12:
                print(f"  {FAIL} {el2:.2f}s 超过 12s 降级线，会撞 15s 硬上限")
                problems.append("RAG 过慢")
            else:
                print(f"  {PASS} {el2:.2f}s  blocks={len(d2.get('blocks') or [])}")
            first = [b.get("text", "") for b in (d2.get("blocks") or []) if b.get("type") == "p"]
            if first:
                print("       首段：", first[0][:110].replace("\n", " "))

    # ---------- 汇总 ----------
    print("\n" + "=" * 68)
    if problems:
        print(f"结论：{len(problems)} 项待处理 → {'、'.join(problems)}")
        sys.exit(1)
    print("结论：全部通过，链路可用")
    print("下一步：关掉「允许公网访问」，只留 callContainer 私有链路；开发者工具真机点一轮。")


if __name__ == "__main__":
    main()
