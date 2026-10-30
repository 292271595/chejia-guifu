"""
嵌入后端体检脚本 —— 拿到新 Key 或换模型时先跑这个，30 秒内判断能不能用。

检查项：
  1. 连通性：能否调通嵌入接口（区分 401 失效 / 402 欠费 / 429 额度用尽 / 网络不通）
  2. 维度：实际输出维度是否与预期一致（决定向量库要不要重建）
  3. 延迟：单条与批量（32 条）耗时，判断查询期是否吃得下 15s 预算
  4. 语义：近义问句的相似度是否合理（低于阈值说明模型不适配中文法条场景）

用法：
    python tools/check_embed.py            # 读 .env / 环境变量
    set EMBED_API_KEY=sk-xxx && python tools/check_embed.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)


def cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(y * y for y in b) ** 0.5
    return num / (da * db + 1e-9)


def main():
    load_env()
    key = os.getenv("EMBED_API_KEY", "") or os.getenv("API_KEY", "")
    base = os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1")
    model = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
    want_dim = int(os.getenv("EMBED_DIM", "1024"))

    print(f"后端: {base}\n模型: {model}\n预期维度: {want_dim}")
    if not key:
        print("✗ 未配置 EMBED_API_KEY / API_KEY，无法检测")
        return 1
    print(f"Key: {key[:6]}…{key[-4:]}（长度 {len(key)}）\n")

    from openai import OpenAI

    client = OpenAI(base_url=base, api_key=key, timeout=20, max_retries=0)

    # ── 1. 连通性 + 维度 ──
    try:
        t0 = time.time()
        r = client.embeddings.create(model=model, input=["醉酒驾驶机动车怎么处罚"])
        lat = time.time() - t0
        dim = len(r.data[0].embedding)
        print(f"✓ 调通：{lat * 1000:.0f}ms，维度 {dim}")
    except Exception as e:
        msg = str(e)
        print(f"✗ 调用失败：{type(e).__name__}: {msg[:200]}")
        if "401" in msg or "invalid" in msg.lower():
            print("  → Key 失效，需要重新生成")
        elif "402" in msg or "insufficient" in msg.lower():
            print("  → 账户余额不足（部分平台要求余额 > 0 才允许调免费模型）")
        elif "429" in msg:
            print("  → 额度用尽或限流")
        return 1

    if dim != want_dim:
        print(f"! 维度与预期不符：实际 {dim}，配置 {want_dim}")
        print("  → 若换维度，必须用新后端重建知识库，否则检索直接报维度错误")

    # ── 2. 批量延迟（建库时的真实开销）──
    batch = [f"第{i}条：机动车驾驶证申领和使用规定测试文本" for i in range(32)]
    try:
        t0 = time.time()
        client.embeddings.create(model=model, input=batch)
        print(f"✓ 批量 32 条：{(time.time() - t0) * 1000:.0f}ms（建库 735 条约 "
              f"{(time.time() - t0) * 735 / 32:.1f}s）")
    except Exception as e:
        print(f"! 批量失败：{type(e).__name__}: {str(e)[:150]}")
        print("  → 降低 BATCH_SIZE 或拉长超时后重建")

    # ── 3. 语义合理性（同一问题的两种问法应当高度相似）──
    pairs = [
        ("驾驶证丢了怎么补办", "驾驶证遗失如何申请补发"),
        ("喝了酒开车怎么处理", "饮酒后驾驶机动车的处罚"),
        ("驾照到期了要换证吗", "机动车驾驶证有效期满换证"),
        ("外地车牌能迁到本地吗", "机动车转籍登记的办理条件"),
    ]
    neg = ("驾驶证丢了怎么补办", "机动车安全技术检验机构管理规定")
    try:
        texts = [t for p in pairs for t in p] + list(neg)
        embs = [d.embedding for d in
                sorted(client.embeddings.create(model=model, input=texts).data,
                       key=lambda x: x.index)]
        print("\n语义相似度（近义问句应明显高于无关问句）：")
        lows = 0
        for i, (a, b) in enumerate(pairs):
            s = cosine(embs[i * 2], embs[i * 2 + 1])
            flag = "✓" if s >= 0.85 else "!"
            if s < 0.85:
                lows += 1
            print(f"  {flag} {s:.3f}  {a} ↔ {b}")
        s_neg = cosine(embs[-2], embs[-1])
        print(f"  参考（无关问句，应明显更低）：{s_neg:.3f}")
        if lows:
            print(f"! {lows} 组近义问句相似度偏低，该模型在本场景的语义召回可能不如预期")
    except Exception as e:
        print(f"! 语义检查跳过：{type(e).__name__}: {str(e)[:120]}")

    print("\n结论：能调通即可建库 → python build_kb.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
