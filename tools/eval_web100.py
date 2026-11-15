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
# 『不得超过 5 次』原文限定为科目二、科目三」。旧版用「命中处 ±26/18 字窗口里
# 有没有否定词」来豁免，实测漏了 8 条：辨析句常常离命中处更远（「别拿科目三的
# 数套到科目二上」在后面 30 字），或者踩词根本不在结论里，而在附表、依据、
# 「记分周期为 12 个月，满分为 12 分」这种背景句里。
#
# 2026-11-15 改成**结论区判分**：must 仍全文找（正确结论必须给出），
# must_not 只在答案的主结论区里判。依据 / 原文引用 / 消歧对比 / 其他项目附表
# 里出现的忌含词不再算答错——它们本来就是答案主动拿来辨析的。
# 关键：非结论区命中**要记进结果里并在报告打印**，不静默吞掉，避免"把尺子
# 放松了却看不出来"。`tools/verify_web100_judge.py` 用明知错的答案反向验证。
EXEMPT = re.compile(r"(不是|不在其列|只管|只限|仅限|只适用于|限定为|而非|"
                    r"错误|不得|不能|并非|没有|不必|不用|别|不要|混淆|差在|"
                    r"误|旧规|对比|除外|不适用于|不适用于此|不适用|另一|其他项目|"
                    r"两条不同|记分周期为|满分)")

# 结论区最少凑到 30 字：只写「**一次记 6 分。**」的答案首段太短，
# 要把紧接着的「对应情形」段并进来才有实质内容；凑够 30 字就停。
# 但**辨析段一律不并**：W003 首段「科目一没有预约次数上限」是对的，
# 下一段「第四十七条的『不得超过 5 次』原文限定为科目二和科目三」是引用
# 错误说法做辨析，并进来就会把正确答案判成答错（2026-11-15 实测踩过）。
_MIN_ZONE = 30
_CONTRAST_PARA = re.compile(
    r"(原文限定为|限定为|不在其列|只管|只限|只适用于|依据第|依据：|"
    r"⚠️|别拿|不要与|混淆|两条不同|记分周期为|满分线|不适用于)")


def _conclusion_zone(answer: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", answer.strip()) if p.strip()]
    if not paras:
        return answer
    zone = paras[0]
    for p in paras[1:]:
        if len(zone) >= _MIN_ZONE or _CONTRAST_PARA.search(p):
            break
        zone += "\n" + p
    return zone


def judge(answer, must, must_not):
    """返回 (是否通过, 缺失要点, 结论区踩中的错法, 结论区外豁免明细)。"""
    answer = answer or ""
    miss = [p for p in must if not re.search(p, answer)]
    zone = _conclusion_zone(answer)
    bad = [p for p in must_not if re.search(p, zone)]
    exempted = []
    for p in must_not:
        if p in bad:
            continue
        for m in re.finditer(p, answer):
            ctx = answer[max(0, m.start() - 40): min(len(answer), m.end() + 30)]
            exempted.append({"pattern": p, "hit": m.group(0),
                             "contrast": bool(EXEMPT.search(ctx)),
                             "ctx": ctx.replace("\n", " ")[:100]})
            break
    return (not miss and not bad), miss, bad, exempted


ABSTAIN = re.compile(r"(暂无|未收录|无法确定|不在.{0,6}范围|没有相关|建议咨询|无法回答)")

# 人工逐条复核结论（2026-11-14）。自动判分只是初判，FAIL 里混着
# 「答案主动引用错误说法做辨析」这类误伤，必须读原文才能定性。
# 复核依据一律是项目知识库里的现行法规原文，不是网页答案。
#
# 2026-11-15：judge 改成结论区判分后，下面这 8 条自动判分也转 PASS，
# 复核结论保留不删——一是留着「这题原来为什么被误伤」的回溯线索，
# 二是自动判分口径变了，人工复核这一层仍然要有，不能只信机器。
VERDICTS = {
    # —— 断言过严，答案其实正确（2026-11-15 起自动判分也已转 PASS）——
    "W008": ("PASS", "答案明确写「没有 10 日间隔」，踩词来自引用第四十七条原文做辨析"),
    "W009": ("PASS", "10 日/20 日分档正确，踩词来自答案主动提示「科目三是 30 日、别套用」"),
    "W010": ("PASS", "20/30/40 日三档正确，踩词来自「补考间隔 10 日别混」的提示"),
    "W027": ("PASS", "窄路掉头 5 分钟正确，踩词来自附带表里「曲线/直角/单边桥没有时间限制」"),
    "W028": ("PASS", "限宽门 30 秒正确，同上，踩词属于其他项目"),
    "W031": ("PASS", "闯红灯 6 分正确，踩词来自「记分周期满分 12 分」的背景说明"),
    "W033": ("PASS", "醉驾 5 年正确，踩词来自「注销未超过 2 年恢复资格」的消歧说明"),
    "W041": ("PASS", "遮挡号牌 9 分正确，踩词来自「满分 12 分」的背景说明"),
    # —— 2026-11-14 判真错 / 未答到点，2026-11-15 修复后复跑改判 PASS ——
    # 原判词保留在括号里，便于回溯「这题原来错在哪」，不是把旧结论抹掉。
    # 改判依据是复跑出来的新答案原文：7 条都落进专属事实层，判分要点全中、
    # 忌含词全不中；两条人工题也逐字读过，确认没编数字、没串法条。
    "W047": ("PASS", "修复后 FULL_SCORE 的 road 档写明「科目一 → 科目三（道路驾驶技能）」（原真错：把 12 分说成也要考道路驾驶技能）"),
    "W056": ("PASS", "修复后 RENEW:DAMAGED_30 答 30 日内 / 第六十六条（原真错：答成期满换证 90 日内）"),
    "W083": ("PASS", "修复后 VEHICLE:INSPECT_EARLY 答期满前 3 个月 / 第五十四条（原真错：答成年检周期表）"),
    "W088": ("PASS", "修复后 REG:KEEP_PLATE 答可以保留 + 三个条件（原真错：检索层弃答「暂无准确信息」）"),
    "W098": ("PASS", "修复后 ACCIDENT:NO_SETTLE 照抄第十三条死亡伤人 + 七种情形（原真错：只列 3 条且混入「未撤离现场」）"),
    # —— 法规无统一口径，人工判 ——
    "W030": ("PASS", "修复后 EXAM:SCORE_RELEASE 明说「法规和考试标准都没有规定统一的出分时限」，没编天数（原：贴科目一考试内容原文）"),
    "W099": ("PASS", "修复后 INS:ESCAPE 答交强险照赔（第二十一条）、逃逸不属第二十二条、第二十四条救助基金先行垫付（原真错：把逃逸套进第二十二条）"),
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
        ok, miss, bad, exempted = judge(answer, q["must"], q["must_not"])
        row = {
            "id": q["id"], "cat": q["cat"], "q": q["q"], "layer": layer,
            "ok": ok, "miss": miss, "bad": bad, "exempted": exempted,
            "sec": round(time.time() - t0, 2),
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
    print("判分口径：must 全文找；must_not 只在答案的主结论区判（依据/消歧/附表里的引用不算答错）")
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

    # 结论区外的忌含词命中必须显式列出，不能静默吞掉——否则「改判分口径」
    # 和「把尺子放松」在报告上长得一模一样。
    ex_rows = [r for r in rows if r.get("exempted")]
    print(f"\n-- 结论区外命中忌含词、已豁免 {len(ex_rows)} 条（逐条可查）--")
    for r in ex_rows:
        e = r["exempted"][0]
        print(f"  {r['id']} {r['q'][:20]:20s} 命中「{e['hit']}」"
              f"{'（辨析句）' if e['contrast'] else '（无辨析词，需人工看）'}")
        print(f"      {e['ctx']}")

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
