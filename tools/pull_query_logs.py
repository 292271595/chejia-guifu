# -*- coding: utf-8 -*-
"""把线上问句日志拉回本地归档。

为什么要有这一步
────────────────
云托管 stdout 日志只保留 7 天，容器可写层在重新发布时也会清空。
如果没人主动拉，真实问句永远是"当天能看、过几天就没"，攒不出频次，
也就无法用真实数据排修复优先级。

这个脚本只做一件事：调 /api/query/logs/export 按天取 JSONL，合并进本地
data/query_log/YYYY-MM-DD.jsonl，按 (时间, 原话, 层级) 去重，可以反复跑。

用法
────
    set QUERY_LOG_TOKEN=你的口令
    python tools/pull_query_logs.py --base-url https://你的服务域名
    python tools/pull_query_logs.py --base-url https://... --days 7
    python tools/pull_query_logs.py --base-url https://... --day 2026-11-18

注意：拉下来的文件含用户原始提问，已在 .gitignore 里排除，不要提交仓库。
"""
import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get(url: str, token: str, timeout: int = 30):
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}token={urllib.parse.quote(token)}"
    req = urllib.request.Request(full, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), r.headers.get("content-type", "")


def fetch_days(base: str, token: str):
    url = base.rstrip("/") + "/api/query/logs/export"
    body, ctype = _get(url, token)
    try:
        data = json.loads(body)
    except Exception:
        raise SystemExit(f"导出接口没有返回 JSON（content-type={ctype}）：{body[:200]}")
    if not data.get("success"):
        raise SystemExit("导出失败：" + str(data.get("message")))
    return data.get("days") or []


def fetch_day(base: str, token: str, day: str):
    url = base.rstrip("/") + "/api/query/logs/export"
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}day={urllib.parse.quote(day)}&token={urllib.parse.quote(token)}"
    req = urllib.request.Request(full, headers={"Accept": "application/x-ndjson"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def _key(rec: dict):
    return (rec.get("ts"), rec.get("q"), rec.get("layer"))


def merge_into(path: str, text: str) -> int:
    """按天合并，返回新增条数。重复跑不会翻倍。"""
    seen = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    seen.add(_key(json.loads(line)))
                except Exception:
                    continue
    added = 0
    with open(path, "a", encoding="utf-8") as out:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict) or not rec.get("q"):
                continue
            k = _key(rec)
            if k in seen:
                continue
            seen.add(k)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            added += 1
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="服务地址，如 https://xxx.sh.run.tcloudbase.com")
    ap.add_argument("--token", default=os.getenv("QUERY_LOG_TOKEN", ""),
                    help="导出令牌，默认读环境变量 QUERY_LOG_TOKEN")
    ap.add_argument("--days", type=int, default=0, help="拉最近 N 天（默认：全部可用日期）")
    ap.add_argument("--day", action="append", help="只拉指定日期，可多次")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "query_log"))
    args = ap.parse_args()

    if not args.token:
        raise SystemExit("缺少令牌：--token 或环境变量 QUERY_LOG_TOKEN")
    os.makedirs(args.out, exist_ok=True)

    try:
        days = args.day or fetch_days(args.base_url, args.token)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"导出接口返回 {e.code}：{e.read()[:300]!r}")
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {args.base_url}：{e.reason}")

    if not days and not args.day:
        print("服务端没有可导出的日志。检查是否配置了 QUERY_LOG_DIR。")
        return 2

    days = sorted(days)
    if args.days > 0:
        days = days[-args.days:]

    total = 0
    for day in days:
        try:
            text = fetch_day(args.base_url, args.token, day)
        except urllib.error.HTTPError as e:
            print(f"  {day} 拉取失败：{e.code}")
            continue
        path = os.path.join(args.out, f"{day}.jsonl")
        n = merge_into(path, text)
        total += n
        print(f"  {day}  新增 {n} 条 -> {path}")

    files = sorted(glob.glob(os.path.join(args.out, "*.jsonl")))
    all_lines = 0
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as f:
            all_lines += sum(1 for line in f if line.strip())
    print(f"\n本次新增 {total} 条｜本地累计 {all_lines} 条｜目录 {args.out}")
    print(f"看报告：python tools/query_log_report.py --dir {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
