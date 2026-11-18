# -*- coding: utf-8 -*-
"""每天自动把线上问句日志拉回本地（供 Windows 任务计划调用）。

和 pull_query_logs.py 的分工
────────────────────────────
pull_query_logs.py 是手动工具，参数都在命令行；
这里负责"无人值守"：自己找配置、自己写运行日志、自己决定退出码，
出问题能在 _pull.log 里看到原因，而不是窗口一闪什么都没留下。

配置从哪里读（优先级从高到低）
──────────────────────────────
1. 环境变量 QUERY_LOG_BASE_URL / QUERY_LOG_TOKEN
2. 配置文件，默认 C:\\RAG\\.secrets\\query_log.env，格式：
       QUERY_LOG_BASE_URL=https://xxx.sh.run.tcloudbase.com
       QUERY_LOG_TOKEN=你的口令
3. 服务地址都没有时回落到文档里记录的线上域名

令牌刻意不写进仓库：data/query_log 和 .secrets 都在 .gitignore 外面。

用法
────
    python tools/pull_logs_daily.py              # 正常拉取
    python tools/pull_logs_daily.py --check      # 只检查配置和连通性，不拉
    python tools/pull_logs_daily.py --days 14    # 一次补更长的窗口
"""
import argparse
import glob
import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import pull_query_logs as P  # noqa: E402

TZ = timezone(timedelta(hours=8))
DEFAULT_BASE = "https://rag-backend-338218-6-1521022515.sh.run.tcloudbase.com"
CONFIG_CANDIDATES = [
    os.getenv("QUERY_LOG_CONFIG", "").strip(),
    r"C:\RAG\.secrets\query_log.env",
    os.path.join(ROOT, "data", "query_log", ".pull.env"),
]
OUT_DIR = os.getenv("QUERY_LOG_LOCAL_DIR", "").strip() \
    or os.path.join(ROOT, "data", "query_log")
RUN_LOG = os.path.join(OUT_DIR, "_pull.log")
MAX_RUN_LOG = 512 * 1024


def now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def read_config() -> dict:
    cfg = {}
    for path in CONFIG_CANDIDATES:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            continue
        cfg["_source"] = path
        break
    # 环境变量优先于文件
    for k in ("QUERY_LOG_BASE_URL", "QUERY_LOG_TOKEN", "QUERY_LOG_XLSX"):
        v = os.getenv(k, "").strip()
        if v:
            cfg[k] = v
    cfg.setdefault("QUERY_LOG_BASE_URL", DEFAULT_BASE)
    return cfg


def rotate_log():
    try:
        if os.path.isfile(RUN_LOG) and os.path.getsize(RUN_LOG) > MAX_RUN_LOG:
            os.replace(RUN_LOG, RUN_LOG + ".1")
    except Exception:
        pass


def write_run_log(text: str):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        rotate_log()
        with open(RUN_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{now()}] {text}\n")
    except Exception as e:
        print(f"写运行日志失败：{e}", file=sys.stderr)


def local_total() -> int:
    n = 0
    for p in glob.glob(os.path.join(OUT_DIR, "*.jsonl")):
        if os.path.basename(p).startswith("_"):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            n += sum(1 for line in f if line.strip())
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="回溯天数，默认 7（覆盖云端保留窗口）")
    ap.add_argument("--check", action="store_true", help="只检查配置与连通性")
    ap.add_argument("--no-excel", action="store_true", help="只写 JSONL，不刷新 Excel")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = read_config()
    base = cfg.get("QUERY_LOG_BASE_URL", DEFAULT_BASE).rstrip("/")
    token = cfg.get("QUERY_LOG_TOKEN", "").strip()

    if not token:
        msg = (f"未配置 QUERY_LOG_TOKEN（配置文件：{cfg.get('_source') or '未找到'}）。"
               "把口令写进 C:\\RAG\\.secrets\\query_log.env 后即可自动拉取。")
        write_run_log("跳过：" + msg)
        print(msg, file=sys.stderr)
        return 2

    try:
        days = P.fetch_days(base, token)
    except urllib.error.HTTPError as e:
        msg = f"导出接口返回 {e.code}（线上可能还是旧版本，或令牌不对）"
        write_run_log(msg)
        print(msg, file=sys.stderr)
        return 3
    except Exception as e:
        msg = f"连不上 {base}：{type(e).__name__}: {e}"
        write_run_log(msg)
        print(msg, file=sys.stderr)
        return 3

    if args.check:
        print(f"配置可用｜服务 {base}｜可导出日期 {len(days)} 天 {days[-3:] if days else ''}")
        return 0

    if not days:
        write_run_log(f"服务端暂无可导出日志（检查 QUERY_LOG_DIR 是否已配置）｜{base}")
        print("服务端暂无可导出日志，检查 QUERY_LOG_DIR 是否已配置。")
        return 4

    days = sorted(days)[-max(1, args.days):]
    added = failed = 0
    for day in days:
        try:
            text = P.fetch_day(base, token, day)
        except Exception:
            failed += 1
            continue
        added += P.merge_into(os.path.join(OUT_DIR, f"{day}.jsonl"), text)

    total = local_total()
    msg = f"拉取 {len(days)} 天｜新增 {added} 条｜失败 {failed} 天｜本地累计 {total} 条"

    # 表格跟着刷新：JSONL 是底稿，Excel 是给人看的那一份，两者不能各说各话
    if not args.no_excel:
        xlsx = cfg.get("QUERY_LOG_XLSX", "").strip() or os.path.join(OUT_DIR, "问句日志.xlsx")
        try:
            import query_log_excel
            res = query_log_excel.sync(OUT_DIR, xlsx)
            msg += f"｜表格已刷新 {res['rows']} 行"
        except Exception as e:
            msg += f"｜表格刷新失败（{type(e).__name__}）"

    write_run_log(msg)
    print(msg)
    return 0 if failed == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
