"""
一键切换嵌入后端并重建知识库 —— 换 key / 换模型后只跑这一个脚本。

做什么（顺序执行，任一步失败立即停）：
  1. 把 key 写进项目根 .env（只改 EMBED_API_KEY 这一行，其他配置原样保留）
  2. 体检：连通性 → 实际维度 → 延迟 → 近义问句相似度
  3. 用实测维度重建向量库（旧库直接覆盖）
  4. 抽样检索自检：6 个问题看 top-3 是否对得上（含 2 条口语问句）
  5. 打印下一步（重启后端 / 云端同步库）

用法：
    python tools/rebuild_kb.py --key sk-xxxxxxxx     # 写 key 并重建
    python tools/rebuild_kb.py                       # 用 .env 里已有的 key 重建
    python tools/rebuild_kb.py --check-only          # 只体检，不动向量库
    python tools/rebuild_kb.py --backend none        # 切纯 BM25（不重建，直接复用现有库）

为什么要有这个脚本：换嵌入 = 换向量空间，必须「先验 key 再建库」。
直接跑 build_kb.py 而 key 不可用时，会建出一个空库或半截库，排查成本远高于先体检。
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
ENV_PATH = os.path.join(ROOT, ".env")


# ── .env 读写 ──
def read_env(path=ENV_PATH):
    data = {}
    if not os.path.isfile(path):
        return data
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def upsert_env(updates, path=ENV_PATH):
    """只改指定键，保留注释与其他行；文件不存在则从 .env.example 复制一份再改"""
    if not os.path.isfile(path):
        example = os.path.join(ROOT, ".env.example")
        if os.path.isfile(example):
            with open(example, encoding="utf-8") as f:
                content = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    done = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                done.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.environ.update({k: str(v) for k, v in updates.items()})


def cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    return num / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5) + 1e-9)


# ── 1. 体检 ──
def health_check(key, base, model):
    from openai import OpenAI

    print(f"\n[1/4] 体检  base={base}  model={model}")
    client = OpenAI(base_url=base, api_key=key, timeout=20, max_retries=0)
    try:
        t0 = time.time()
        r = client.embeddings.create(model=model, input=["醉酒驾驶机动车怎么处罚"])
        lat = (time.time() - t0) * 1000
        dim = len(r.data[0].embedding)
        print(f"  ✓ 调通：{lat:.0f}ms，维度 {dim}")
    except Exception as e:
        msg = str(e)
        print(f"  ✗ 调用失败：{type(e).__name__}: {msg[:200]}")
        if "401" in msg or "invalid" in msg.lower():
            print("    → Key 失效，去控制台重新生成")
        elif "402" in msg or "insufficient" in msg.lower():
            print("    → 账户欠费：免费模型也要求账户无欠费，去费用中心处理后再试")
        elif "429" in msg:
            print("    → 额度用尽或限流")
        return None, None

    # 批量延迟（建库真实开销）
    batch = [f"第{i}条测试文本：机动车驾驶证申领和使用规定" for i in range(32)]
    try:
        t0 = time.time()
        client.embeddings.create(model=model, input=batch)
        bt = time.time() - t0
        print(f"  ✓ 批量 32 条：{bt * 1000:.0f}ms（建 735 条约 {bt * 735 / 32:.0f}s）")
    except Exception as e:
        print(f"  ! 批量失败（可降 BATCH_SIZE）：{type(e).__name__}: {str(e)[:120]}")

    # 语义合理性
    pairs = [
        ("驾驶证丢了怎么补办", "驾驶证遗失如何申请补发"),
        ("喝了酒开车怎么处理", "饮酒后驾驶机动车的处罚"),
        ("外地车牌能迁到本地吗", "机动车转籍登记的办理条件"),
    ]
    try:
        texts = [t for p in pairs for t in p] + ["机动车安全技术检验机构管理规定"]
        embs = [d.embedding for d in
                sorted(client.embeddings.create(model=model, input=texts).data, key=lambda x: x.index)]
        print("  语义相似度（近义应 ≥0.85，无关应明显更低）：")
        low = 0
        for i, (a, b) in enumerate(pairs):
            s = cosine(embs[i * 2], embs[i * 2 + 1])
            low += s < 0.85
            print(f"    {'✓' if s >= 0.85 else '!'} {s:.3f}  {a} ↔ {b}")
        print(f"    参考无关问句：{cosine(embs[-2], embs[-1]):.3f}")
        if low:
            print(f"  ! {low} 组近义相似度偏低，口语问句召回可能不如预期")
    except Exception as e:
        print(f"  ! 语义检查跳过：{type(e).__name__}: {str(e)[:100]}")
    return dim, lat


# ── 3. 重建 ──
def rebuild(key, base, model, dim):
    from vector_store import build_knowledge_base

    print(f"\n[3/4] 重建知识库（{dim} 维）...")
    t0 = time.time()
    store = build_knowledge_base(
        os.path.join(ROOT, "data", "knowledge_base"),
        os.path.join(ROOT, "chroma_db"),
        api_key=key, api_base=base, embed_backend="cloud",
    )
    if not store:
        print("  ✗ 建库失败")
        return None
    print(f"  ✓ 完成：{store.count()} 条，{store._stored_dim()} 维，耗时 {time.time() - t0:.1f}s")
    return store


# ── 4. 检索自检 ──
PROBES = [
    ("醉酒驾驶被吊销几年能重考", "不得重新取得"),
    ("红绿色盲能考驾照吗", "色盲"),
    ("63岁能开大客车吗", "63周岁"),
    ("喝了酒开车被抓会怎么处理", "饮酒"),
    ("外地车牌能迁到本地吗", "转籍"),
    ("C6驾驶证怎么考", "轻型牵引挂车"),
]


def retrieval_check(store):
    print("\n[4/4] 检索自检（top-3 首行，看是否答在点上）")
    ok = 0
    for q, anchor in PROBES:
        t0 = time.time()
        hits = store.search(q, top_k=3)
        ms = (time.time() - t0) * 1000
        hit = any(anchor in h["content"] for h in hits)
        ok += hit
        print(f"\n  {'✓' if hit else '✗'} {q}  ({ms:.0f}ms)")
        for h in hits:
            first = h["content"].strip().splitlines()[0][:52]
            print(f"      {h.get('hybrid_score', 0):.3f}  {first}")
    print(f"\n  命中 {ok}/{len(PROBES)}（≥5 可上线；<5 说明该模型与本地语料不匹配）")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", help="嵌入 API Key（写入 .env 的 EMBED_API_KEY）")
    ap.add_argument("--backend", default="cloud", choices=["cloud", "none"])
    ap.add_argument("--check-only", action="store_true", help="只体检，不重建")
    args = ap.parse_args()

    env = read_env()
    base = env.get("EMBED_API_BASE", "https://api.siliconflow.cn/v1")
    model = env.get("EMBED_MODEL", "BAAI/bge-m3")
    key = args.key or env.get("EMBED_API_KEY", "") or os.getenv("EMBED_API_KEY", "")

    if args.backend == "none":
        env_update = {"EMBED_BACKEND": "none"}
        upsert_env(env_update)
        print("已切到纯 BM25 模式（复用现有向量库当文档仓库，不重建）。")
        print("注意：口语问句召回会明显变差，仅建议做临时验证。")
        print("改回云端嵌入：把 .env 里 EMBED_BACKEND 改回 cloud 后执行本脚本。")
        return 0

    if key:
        upsert_env({"EMBED_BACKEND": "cloud", "EMBED_API_KEY": key,
                    "EMBED_API_BASE": base, "EMBED_MODEL": model})
        print(f"已写入 .env：EMBED_API_KEY={key[:6]}…{key[-4:]}")
    if not key:
        print("✗ 没有可用 Key。用 --key 传入，或先在 .env 里填 EMBED_API_KEY")
        return 1

    dim, _ = health_check(key, base, model)
    if dim is None:
        print("\n体检未通过，已中止（未改动向量库）")
        return 1

    # 维度以实测为准：写回 EMBED_DIM，保证建库与查询用同一维度
    upsert_env({"EMBED_DIM": dim, "EMBED_BACKEND": "cloud",
                "EMBED_API_KEY": key, "EMBED_API_BASE": base, "EMBED_MODEL": model})

    if args.check_only:
        print("\n--check-only：跳重建。去掉该参数即可建库。")
        return 0

    print("\n[2/4] 维度确认：%d（写回 .env 的 EMBED_DIM）" % dim)
    store = rebuild(key, base, model, dim)
    if store is None:
        return 1
    ok = retrieval_check(store)
    print("\n下一步：")
    print("  1) 重启本地后端：Stop-ScheduledTask chejia-zhushou-backend; Start-ScheduledTask chejia-zhushou-backend")
    print("  2) 云端：提交 chroma_db 并推送，云托管重新构建（镜像不再需要 torch）")
    print("  3) 全量回归：python tools/run200.py")
    return 0 if ok >= 5 else 2


if __name__ == "__main__":
    sys.exit(main())
