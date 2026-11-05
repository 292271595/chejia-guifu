# -*- coding: utf-8 -*-
"""发布前体检：确认打进镜像的东西是齐的。

为什么需要这个
--------------
有三类问题本地全绿也发现不了，只有在镜像里才暴露：

1. **文件没进镜像**。`data/glossary_colloquial.json` 就这么丢过一次——
   `.dockerignore` 排除了 `data/`，Dockerfile 也不 COPY 它。术语表载入失败只打一行
   日志就关闭查询改写，**不崩不报错**，线上表现为"口语问法答得差"，查几天都未必发现。

2. **索引加载失败会静默跳过整层校验**。引用索引没了就关掉引用对账、数字索引没了就
   关掉数字核对，都是打一行日志了事。所以要把"加载成功且条目数正常"作为硬性检查。

3. **`.env` 在本地永远存在，在镜像里永远不存在**。本地跑得通不代表容器里跑得通，
   缺 key 时是启动失败还是降级运行，必须在无 .env 的条件下验证。

用法
----
    python tools/check_release.py              # 静态体检（秒级）
    python tools/check_release.py --boot       # 额外做无 .env 冷启动验证（约 10s）

退出码 0 = 全部通过；1 = 有阻塞项。
"""
import io
import json
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

FAIL = []
WARN = []


def ok(msg):
    print(f"  [OK]   {msg}")


def fail(msg):
    FAIL.append(msg)
    print(f"  [FAIL] {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  [WARN] {msg}")


# ── A. 镜像文件完整性 ────────────────────────────────────
# 运行时真正会读的文件。每一条都必须落在某条 COPY 的源目录下，
# 且不能被 .dockerignore 排除——两者缺一，镜像里就没有它。
RUNTIME_FILES = [
    ("backend/main.py", "服务入口"),
    ("backend/rag_engine.py", "问答引擎"),
    ("backend/fact_table.py", "事实层调度"),
    ("backend/kb_index/citation_index.json", "引用对账白名单"),
    ("backend/kb_index/number_index.json", "数字核对索引"),
    ("backend/kb_index/glossary_colloquial.json", "口语术语表"),
    ("chroma_db/chroma.sqlite3", "向量库"),
    ("frontend/index.html", "网页版"),
    ("requirements.txt", "依赖清单"),
]


def parse_dockerfile():
    path = os.path.join(ROOT, "Dockerfile")
    copies = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*COPY\s+(.+?)\s+(\S+)\s*$", line)
            if not m:
                continue
            for src in m.group(1).split():
                copies.append(src)
    return copies


def covered(rel, copies):
    """rel 是否落在某条 COPY 的源目录/文件之下。"""
    for c in copies:
        cs = c.rstrip("/")
        if rel == cs or rel.startswith(cs + "/"):
            return True
    return False


def parse_dockerignore():
    path = os.path.join(ROOT, ".dockerignore")
    rules, negations = [], []
    if not os.path.isfile(path):
        return rules, negations
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!"):
                negations.append(line[1:].lstrip("./"))
            else:
                rules.append(line.lstrip("./"))
    return rules, negations


def ignored(rel, rules, negations):
    """粗粒度判断：命中 ignore 规则且未被 ! 规则放行。"""
    if any(rel == n or rel.startswith(n.rstrip("/") + "/") for n in negations):
        return False
    for r in rules:
        if r.endswith("/"):
            if rel.startswith(r):
                return True
        elif r.startswith("*."):
            if rel.endswith(r[1:]):
                return True
        elif rel == r or rel.startswith(r + "/"):
            return True
    return False


def check_image_files():
    print("\n[A] 镜像文件完整性")
    copies = parse_dockerfile()
    rules, negations = parse_dockerignore()
    for rel, desc in RUNTIME_FILES:
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            fail(f"{rel} 本地就不存在（{desc}）")
            continue
        if not covered(rel, copies):
            fail(f"{rel} 不在任何 COPY 源目录下，进不了镜像（{desc}）")
            continue
        if ignored(rel, rules, negations):
            fail(f"{rel} 被 .dockerignore 排除，COPY 会直接失败（{desc}）")
            continue
        ok(f"{rel} —— {desc}")


# ── B. 索引可加载 ────────────────────────────────────────
def check_indexes():
    print("\n[B] 三个静默降级源能否正常加载")
    sys.path.insert(0, os.path.join(ROOT, "backend"))

    try:
        import glossary
        n = glossary.stats()["entries"]
        if n >= 50:
            ok(f"术语表：{n} 条目")
        else:
            fail(f"术语表只有 {n} 条目（预期 ≥50），查询改写会明显变弱")
    except Exception as e:
        fail(f"术语表加载失败：{type(e).__name__}: {e}")

    try:
        import citation_guard
        ci = citation_guard.index()
        ci.load()
        n = len(ci.docs)
        if n >= 20:
            ok(f"引用索引：{n} 部法规")
        else:
            fail(f"引用索引只有 {n} 部（预期 ≥20），引用对账基本失效")
    except Exception as e:
        fail(f"引用索引加载失败：{type(e).__name__}: {e}")

    try:
        import number_guard
        ni = number_guard.index()
        ni.load()
        n = len(ni.docs) if getattr(ni, "loaded", False) else 0
        if n >= 20:
            ok(f"数字索引：{n} 部法规")
        else:
            fail(f"数字索引只有 {n} 部（预期 ≥20），数字核对基本失效")
    except Exception as e:
        fail(f"数字索引加载失败：{type(e).__name__}: {e}")


# ── C. 依赖版本 ─────────────────────────────────────────
PINNED = ("chromadb", "fastapi", "openai")


def check_requirements():
    print("\n[C] 依赖版本锁定")
    req = {}
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            k, v = line.split("==", 1)
            req[k.strip().lower()] = v.strip()
    try:
        import importlib.metadata as md
        for pkg in PINNED:
            want = req.get(pkg)
            if not want:
                warn(f"{pkg} 未在 requirements.txt 里锁定版本")
                continue
            try:
                got = md.version(pkg)
            except Exception:
                fail(f"{pkg} 本地未安装，无法核对（镜像里会装，但本地测不了）")
                continue
            if got == want:
                ok(f"{pkg}=={got}")
            else:
                warn(f"{pkg} 本地 {got}，requirements 锁的是 {want}（chromadb 跨大版本读库会崩）")
    except Exception as e:
        warn(f"版本核对跳过：{e}")


# ── D. 敏感信息 ─────────────────────────────────────────
SK_PAT = re.compile(r"sk-[A-Za-z0-9]{20,}")


def check_secrets():
    print("\n[D] 敏感信息不在仓库里")
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        warn(f"git 不可用，跳过：{e}")
        return
    hits = []
    for rel in out.splitlines():
        if not rel.endswith((".py", ".md", ".json", ".js", ".html", ".env.example", ".txt")):
            continue
        p = os.path.join(ROOT, rel)
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if SK_PAT.search(line):
                        hits.append(f"{rel}:{i}")
        except Exception:
            continue
    if hits:
        fail(f"仓库里有明文 API Key：{', '.join(hits[:5])}")
    else:
        ok("已被 git 跟踪的文件里没有明文 key")

    if os.path.isfile(os.path.join(ROOT, ".env")):
        gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
        if ".env" in gi:
            ok(".env 已被 .gitignore 排除")
        else:
            fail(".env 存在但没被 .gitignore 排除")


# ── E. 接口契约 ─────────────────────────────────────────
CONTRACT = ("success", "blocks", "sources", "answer")


def check_contract():
    print("\n[E] 接口契约（前端/小程序依赖的字段）")
    src = open(os.path.join(ROOT, "backend", "main.py"), encoding="utf-8").read()
    for k in CONTRACT:
        if f'"{k}"' in src:
            ok(f"响应字段 {k}")
        else:
            fail(f"响应字段 {k} 不见了（前端/小程序会拿不到数据）")
    if "fact_hit" in src:
        ok("响应字段 fact_hit")
    else:
        warn("响应字段 fact_hit 缺失（前端绿色徽章会失效）")


# ── F. 无 .env 冷启动 ───────────────────────────────────
def check_boot():
    print("\n[F] 无 .env 冷启动（模拟容器环境）")
    env = {k: v for k, v in os.environ.items()
           if k not in ("API_KEY", "EMBED_API_KEY", "GEN_BACKEND", "MODEL",
                        "EMBED_BACKEND", "EMBED_MODEL", "EMBED_API_BASE")}
    env["PATH"] = os.environ.get("PATH", "")
    port = "8199"

    # 光清环境变量不够：main.py 会自己 _load_dotenv 读磁盘上的 .env，
    # 本地永远有这个文件，测出来"能起"是假象。必须把它挪开再测。
    dotenv = os.path.join(ROOT, ".env")
    hidden = dotenv + ".__bootcheck"
    moved = False
    try:
        if os.path.isfile(dotenv):
            os.rename(dotenv, hidden)
            moved = True
    except Exception as e:
        warn(f".env 无法临时移开（{type(e).__name__}），本项结果不可信——容器里没有 .env")

    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", port,
         "--log-level", "error"],
        cwd=os.path.join(ROOT, "backend"), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    import time
    import urllib.request
    try:
        deadline = time.time() + 45
        body = None
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                    body = json.loads(r.read())
                break
            except Exception:
                time.sleep(0.8)
        if body is None:
            out = ""
            try:
                proc.kill()
                out = (proc.stdout.read() or b"").decode("utf-8", "replace")[-600:]
            except Exception:
                pass
            fail(f"无 key 时服务起不来（容器会反复崩溃重启）\n{out}")
            return
        if body.get("status") == "ok":
            ok(f"进程存活，engine={body.get('engine')}，gen_ready={body.get('gen_ready')}"
               f"（没 key 时 gen_ready 应为 false，但进程不能崩）")
            if moved and body.get("gen_ready"):
                warn("挪开 .env 后 gen_ready 仍为 true，说明 key 来自环境变量而非 .env，属正常")
        else:
            fail(f"/health 返回异常：{body}")
        if body.get("resources"):
            r = body["resources"]
            if r.get("glossary", 0) >= 50 and r.get("citation_docs", 0) >= 20:
                ok(f"镜像内资源可见：术语 {r.get('glossary')} 条、"
                   f"引用索引 {r.get('citation_docs')} 部、数字索引 {r.get('number_docs')} 部")
            else:
                fail(f"镜内资源不完整：{r}")
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        if moved:
            try:
                os.rename(hidden, dotenv)
            except Exception as e:
                fail(f".env 没还原成功，请手动把 {hidden} 改回 .env（{e}）")


def main():
    boot = "--boot" in sys.argv
    print("=" * 62)
    print("发布前体检")
    print("=" * 62)
    check_image_files()
    check_indexes()
    check_requirements()
    check_secrets()
    check_contract()
    if boot:
        check_boot()
    else:
        print("\n[F] 无 .env 冷启动 —— 跳过（加 --boot 启用）")

    print("\n" + "=" * 62)
    if FAIL:
        print(f"阻塞项 {len(FAIL)} 个：")
        for f in FAIL:
            print(f"  · {f}")
    if WARN:
        print(f"提醒 {len(WARN)} 个：")
        for w in WARN:
            print(f"  · {w}")
    if not FAIL:
        print("全部通过，可以构建镜像。")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
