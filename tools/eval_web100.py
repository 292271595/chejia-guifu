"""对联网搜集的 100 个车驾管高频问法跑真实问答链路并分层判分。

跑法：
    python tools/eval_web100.py --limit 5      # 冒烟
    python tools/eval_web100.py                # 全量（逐题落盘，可中断续跑）
    python tools/eval_web100.py --report       # 只看已有结果的汇总，不重跑

为什么要复制向量库：`create_rag_engine` 会用读写句柄打开 chroma，
评测虽然不增删文档，但保险起见把原库整个复制一份，跑完可以删掉副本，
原 `chroma_db/` 一个字节都不动。

结果与判分：
- 每题走 `engine.query(q, top_k=6, budget=12)`，这是线上真实路径（含事实层
  命中、大模型复核、数字核对、护栏、引用对账），不是单跑事实表。
- layer 由返回里的 fact_hit 判定，fact_hit 非空 = 事实层直答，否则 = 检索层。
- 判分沿用 eval_100.judge 的二值口径：must 正则全中 + must_not 全不中。
- 自动判分只是初判。断言过严、答案换了说法、正确弃答都会误判为 FAIL，
  所以 FAIL 必须逐条人工复核（见 --report 输出的失败清单）。
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

RESULT = os.path.join(ROOT, "tools", "_web100_result.json")
CHROMA_SRC = os.path.join(ROOT, "chroma_db")
CHROMA_COPY = os.path.join(ROOT, "data", "_eval_chroma_tmp")


def load_env():
    """把 .env 里的键值灌进 os.environ（已有环境变量优先）。不打印任何值。"""
    p = os.path.join(ROOT, ".env")
    if not os.path.isfile(p):
        return
    for ln in open(p, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def default_config():
    load_env()
    api_base = os.getenv("API_BASE")
    if not api_base:
        api_base = ("https://api.siliconflow.cn/v1"
                    if os.getenv("LLM_PROVIDER", "zhipu") == "siliconflow"
                    else "https://open.bigmodel.cn/api/paas/v4")
    model = os.getenv("MODEL") or (
        "deepseek-ai/DeepSeek-V3" if os.getenv("LLM_PROVIDER", "zhipu") == "siliconflow"
        else "glm-4-flash")
    return dict(
        api_base=api_base,
        api_key=os.getenv("API_KEY", ""),
        model=model,
        embed_backend=os.getenv("EMBED_BACKEND", "cloud"),
        embed_api_key=os.getenv("EMBED_API_KEY", ""),
        embed_api_base=os.getenv("EMBED_API_BASE", "https://api.siliconflow.cn/v1"),
    )


def ensure_chroma_copy():
    if os.path.isdir(CHROMA_COPY) and os.path.isfile(os.path.join(CHROMA_COPY, "chroma.sqlite3")):
        return CHROMA_COPY
    os.makedirs(os.path.dirname(CHROMA_COPY), exist_ok=True)
    shutil.copytree(CHROMA_SRC, CHROMA_COPY, dirs_exist_ok=True)
    return CHROMA_COPY


# 事实层答案常主动引用错误说法来做辨析，例如「科目一没有上限……第四十七条的
# 『不得超过 5 次』原文限定为科目二、科目三」。裸 must_not 会把这种正确对比
# 判成答错——项目里已经踩过好几次。所以命中处前后若有否定/辨析词就豁免。
EXEMPT = re.compile(r"(不是|不在其列|只管|限定为|而非|错误|不得|不能|并非|"
                    r"误|旧规|对比|除外|不适用于|不适用于此|不适用)")


def judge(answer, must, must_not):
    answer = answer or ""
    miss = [p for p in must if not re.search(p, answer)]
    bad = []
    for p in must_not:
        for m in re.finditer(p, answer):
            ctx = answer[max(0, m.start() - 26): min(len(answer), m.end() + 18)]
            if not EXEMPT.search(ctx):
                bad.append(p)
                break
    return (not miss and not bad), miss, bad


ABSTAIN = re.compile(r"(暂无|未收录|无法确定|不在.{0,6}范围|没有相关|建议咨询|无法回答)")

# 人工逐条复核结论（2026-11-14）。自动判分只是初判，FAIL 里混着
# 「答案主动引用错误说法做辨析」这类误伤，必须读原文才能定性。
# 复核依据一律是项目知识库里的现行法规原文，不是网页答案。
VERDICTS = {
    # —— 断言过严，答案其实正确 ——
    "W008": ("PASS", "答案明确写「没有 10 日间隔」，踩词来自引用第四十七条原文做辨析"),
    "W009": ("PASS", "10 日/20 日分档正确，踩词来自答案主动提示「科目三是 30 日、别套用」"),
    "W010": ("PASS", "20/30/40 日三档正确，踩词来自「补考间隔 10 日别混」的提示"),
    "W027": ("PASS", "窄路掉头 5 分钟正确，踩词来自附带表里「曲线/直角/单边桥没有时间限制」"),
    "W028": ("PASS", "限宽门 30 秒正确，同上，踩词属于其他项目"),
    "W031": ("PASS", "闯红灯 6 分正确，踩词来自「记分周期满分 12 分」的背景说明"),
    "W033": ("PASS", "醉驾 5 年正确，踩词来自「注销未超过 2 年恢复资格」的消歧说明"),
    "W041": ("PASS", "遮挡号牌 9 分正确，踩词来自「满分 12 分」的背景说明"),
    # —— 真错 ——
    "W047": ("真错", "24 分应答「科目一合格后再考科目三道路驾驶技能」；答案把 12 分的情形说成也要考道路驾驶技能，且没提科目一（163号令第二十二条）"),
    "W056": ("真错", "问的是损毁换证期限（第六十六条：30 日内），答成了期满换证 90 日内，答非所问"),
    "W083": ("真错", "问的是年检可提前多久（第五十四条：有效期满前三个月内），答成了年检周期表，答非所问"),
    "W088": ("真错", "知识库第四十四条明明写了可以保留原号及三个条件，检索层却弃答「暂无准确信息」"),
    "W098": ("真错", "只列了 3 条且混入「未撤离现场」这种不属「不能自行协商」的情形，没答到「应当报警/保护现场」"),
    # —— 法规无统一口径，人工判 ——
    "W030": ("未答到点", "标准确实没有统一出分时限，但答案列了科目一考试内容原文，没明说「法规无统一规定」；胜在没编数字"),
    "W099": ("真错", "把逃逸套进第二十二条（醉酒/无证/盗抢/故意）说成「只垫付抢救费、财产损失不赔」。逃逸实为第二十四条救助基金垫付情形，不是交强险免责"),
    "W100": ("PASS", "正确区分非机动车电动自行车与电动摩托车需 D/E/F 证"),
}


def run(args):
    from web100_bank import as_dicts
    import rag_engine

    questions = as_dicts()
    if args.limit:
        questions = questions[:args.limit]
    if args.cat:
        questions = [q for q in questions if q["cat"] == args.cat]

    done = {}
    if os.path.isfile(RESULT) and not args.fresh:
        try:
            done = {r["id"]: r for r in json.load(open(RESULT, encoding="utf-8"))}
        except Exception:
            done = {}

    cfg = default_config()
    print(f"题库 {len(questions)} 题 | 已完成 {len(done)} 题 | "
          f"生成模型 {cfg['model']} | 嵌入后端 {cfg['embed_backend']}")
    print("向量库使用副本，原 chroma_db 不动\n")

    engine = None
    rows = dict(done)
    t_all = time.time()
    for i, q in enumerate(questions, 1):
        if q["id"] in rows and not args.fresh:
            print(f"[{i:3d}/{len(questions)}] SKIP {q['id']} {q['q']}")
            continue
        if engine is None:
            engine = rag_engine.create_rag_engine(
                ensure_chroma_copy(), cfg["api_base"], cfg["api_key"], cfg["model"],
                cfg["embed_backend"], cfg["embed_api_key"], cfg["embed_api_base"])
        t0 = time.time()
        try:
            r = engine.query(q["q"], top_k=6, budget=12)
            answer = r.get("answer") or ""
            layer = "fact" if r.get("fact_hit") else "rag"
            titles = [((s.get("metadata") or {}).get("title") or s.get("title") or "")
                      for s in r.get("sources", [])]
            degraded = bool(r.get("degraded"))
            err = ""
        except Exception as e:
            answer, layer, titles, degraded = "", "error", [], False
            err = f"{type(e).__name__}: {e}"[:200]
        ok, miss, bad = judge(answer, q["must"], q["must_not"])
        row = {
            "id": q["id"], "cat": q["cat"], "q": q["q"], "layer": layer,
            "ok": ok, "miss": miss, "bad": bad, "sec": round(time.time() - t0, 2),
            "answer": answer, "titles": titles, "degraded": degraded,
            "abstain": bool(ABSTAIN.search(answer)), "manual": q["manual"],
            "basis": q["basis"], "err": err,
            "verdict": "",           # 人工复核结论：PASS / 真错 / 断言过严 / 合理弃答
        }
        rows[q["id"]] = row
        # 逐题落盘：中途断网/超时也不丢前面跑过的
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump([rows[k] for k in sorted(rows)], f, ensure_ascii=False, indent=1)
        flag = "OK " if ok else "FAIL"
        print(f"[{i:3d}/{len(questions)}] {flag} {q['id']} {layer:4s} "
              f"{row['sec']:5.1f}s  {q['q']}" + (f"  ERR={err}" if err else ""))
    print(f"\n总耗时 {time.time() - t_all:.1f}s，结果 {RESULT}")


def report(args):
    if not os.path.isfile(RESULT):
        print("没有结果文件，先跑一次。")
        return
    rows = json.load(open(RESULT, encoding="utf-8"))
    if args.cat:
        rows = [r for r in rows if r["cat"] == args.cat]
    total = len(rows)
    graded = [r for r in rows if not r.get("manual")]
    auto_ok = sum(1 for r in graded if r["ok"])

    def verdict(r):
        return VERDICTS.get(r["id"], ("", ""))[0]

    def is_pass(r):
        v = verdict(r)
        if v:
            return v == "PASS"
        return bool(r["ok"]) and not r.get("manual")

    confirmed = sum(1 for r in rows if is_pass(r))
    confirmed_graded = sum(1 for r in graded if is_pass(r))
    real_bad = [r for r in rows if verdict(r) in ("真错", "未答到点")]
    fixed_by_review = sum(1 for r in graded if not r["ok"] and is_pass(r))

    print("=" * 72)
    print(f"联网 100 问评测 | 已跑 {total} 题（其中 {total - len(graded)} 题法规无统一口径，需人工判）")
    print("=" * 72)
    print(f"\n自动判分：      {auto_ok}/{len(graded)} = {auto_ok / max(1, len(graded)) * 100:.1f}%  （初判，含误伤）")
    print(f"人工复核后：    {confirmed_graded}/{len(graded)} = {confirmed_graded / max(1, len(graded)) * 100:.1f}%  （可自动判分的 {len(graded)} 题）")
    print(f"全 100 题口径： {confirmed}/{total} = {confirmed / total * 100:.1f}%  （含 3 题人工判）")
    print(f"其中 {fixed_by_review} 条 FAIL 复核后判对（答案主动引用错误说法做辨析，被断言误伤）")
    print(f"确认有问题：{len(real_bad)} 条\n")

    print("-- 分层（复核后）--")
    for layer in ("fact", "rag", "error"):
        g = [r for r in rows if r["layer"] == layer]
        if g:
            n = sum(1 for r in g if is_pass(r))
            print(f"  {layer:5s} {n:3d}/{len(g):3d} = {n / len(g) * 100:5.1f}%   "
                  f"（平均 {sum(r['sec'] for r in g) / len(g):.2f}s）")

    print("\n-- 分类 --")
    bycat = defaultdict(list)
    for r in graded:
        bycat[r["cat"]].append(r)
    for cat, g in sorted(bycat.items()):
        n = sum(1 for r in g if is_pass(r))
        print(f"  {cat:8s} {n:3d}/{len(g):3d} = {n / len(g) * 100:5.1f}%")

    ab = [r for r in rows if r.get("abstain")]
    deg = [r for r in rows if r.get("degraded")]
    err = [r for r in rows if r["layer"] == "error"]
    print(f"\n-- 弃答 {len(ab)} 条 / 降级 {len(deg)} 条 / 报错 {len(err)} 条 --")
    for r in ab:
        print(f"    {r['id']} {r['q']}")

    print("\n-- 确认有问题的题（复核后仍判错）--")
    for r in real_bad:
        print(f"\n  {r['id']} [{r['cat']}/{r['layer']}] {r['q']}")
        print(f"    {VERDICTS.get(r['id'], ('', ''))[1]}")

    fails = [r for r in graded if not r["ok"]]
    print(f"\n-- 自动判分 FAIL {len(fails)} 条（含复核结论）--")
    for r in fails:
        v = verdict(r)
        print(f"  {r['id']} {r['q'][:24]:24s} → {v or '未复核'}")
    # 回写复核结论，结果文件自带判词，事后不用重跑也能看
    for r in rows:
        r["verdict"] = verdict(r)
        r["verdict_note"] = VERDICTS.get(r["id"], ("", ""))[1]
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    manual = [r for r in rows if r.get("manual")]
    if manual:
        print(f"\n-- 法规无统一口径 {len(manual)} 条（只看它有没有乱编）--")
        for r in manual:
            print(f"\n  {r['id']} {r['q']}")
            print(f"    答：{(r['answer'] or '(空)')[:220].replace(chr(10), ' | ')}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--cat", default="")
    p.add_argument("--fresh", action="store_true", help="忽略已有结果重跑")
    p.add_argument("--report", action="store_true", help="只出报告")
    a = p.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if a.report:
        report(a)
    else:
        run(a)
