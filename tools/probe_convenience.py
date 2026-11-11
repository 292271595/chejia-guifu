# -*- coding: utf-8 -*-
"""便民措施知识库的检索探针。

用法：python tools/probe_convenience.py
逐条打印 top3，并对照期望命中的小节号，输出命中率。
建库后必跑——rebuild_kb.py 的 6 条自检覆盖不到本篇主题。
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "backend")

from dotenv import load_dotenv

load_dotenv()
from vector_store import VectorStore

# (问句, 期望命中的关键词——出现在 top3 块的正文或标题里即算命中)
CASES = [
    ("异地考驾照要居住证吗", ["一证通考"]),
    ("驾驶证考试能换几次考试地", ["变更考试地", "三次"]),
    ("人在国外驾照快过期了怎么办", ["延期", "3 年"]),
    ("在国外回不来 驾照过期被注销了还能恢复吗", ["6 个月", "救济"]),
    ("70岁还能考驾照吗", ["70 周岁", "三力"]),
    ("自动挡科目二考几项", ["坡道定点", "4 项"]),
    ("注销满两年重新申领要考几科", ["科目三道路驾驶技能"]),
    ("老年人考驾照能让子女代办吗", ["大字版", "亲友"]),
    ("军人换地方驾照驾龄怎么算", ["连续计算", "大型货车"]),
    ("新车能网上上牌吗", ["国产小客车", "交管12123"]),
    ("进口车能网上上牌吗", ["2026", "进口小客车"]),
    ("二手车过户还要回原地提档吗", ["转籍", "迁入地"]),
    ("电子行驶证有用吗", ["电子行驶证"]),
    ("免检标志还要贴纸吗", ["检验标志电子化", "电子凭证"]),
    ("异地检车要开委托书吗", ["通检", "委托"]),
    ("轻微事故能视频快处吗", ["36 个城市", "视频快处"]),
    ("满分学习能在外地变更吗", ["满分审验教育地", "满分学习"]),
    ("解除抵押能网上办吗", ["解除抵押"]),
    ("摩托车6年内免检吗", ["10 年内", "摩托车"]),
    ("考驾照体检去哪家医院", ["体检医疗机构", "乡镇卫生院"]),
]


def main() -> int:
    vs = VectorStore("chroma_db")
    ok = 0
    for q, expects in CASES:
        hits = vs.search(q, top_k=3)
        blob = " ".join(
            (h.get("metadata", {}).get("title") or "") + h.get("content", "") for h in hits
        )
        hit = any(e in blob for e in expects)
        ok += hit
        print("=" * 74)
        print(("√ " if hit else "× ") + q + ("" if hit else f"   期望关键词={expects}"))
        for h in hits:
            src = h.get("metadata", {}).get("source", "?")
            first = h.get("content", "").replace("\n", " ")[:56]
            print(f"   {h.get('score', 0):.3f} [{src}] {first}")
    print("\n" + "=" * 74)
    print(f"top3 命中 {ok}/{len(CASES)}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
