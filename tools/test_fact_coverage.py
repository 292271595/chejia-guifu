# -*- coding: utf-8 -*-
"""事实层覆盖体检的口径自检。

为什么值得单独测：这套扫描最危险的失败不是报错，是**静默给出错数字**。
写这个脚本时真踩过一次——按「第一次出现」去重条号，结果文件开头的
「范围说明：仅收录第一百三十三条…」把正文整条挤掉，最后得出
「刑法没有条款、覆盖率 0%」的假结论，而报告看起来一切正常。

所以这里只锁三件事：条文切得出来、切得对、判定方向没反。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import scan_fact_coverage as S  # noqa: E402

KB = os.path.join(ROOT, "data", "knowledge_base")


def main() -> int:
    errors = []

    # 1) 刑法这种「范围说明 + 正文」结构的文件，必须切出正文而不是被说明挤掉
    arts = S.parse_articles(os.path.join(KB, "criminal_law_traffic.md"))
    nos = [a["no"] for a in arts]
    if len(arts) < 3:
        errors.append(f"刑法只切出 {len(arts)} 条，范围说明又把正文挤掉了")
    for want in ("第一百三十三条", "第一百三十三条之一"):
        if want not in nos:
            errors.append(f"没切出 {want}")
    body = " ".join(a["text"] for a in arts)
    if "交通肇事罪" not in body:
        errors.append("切出的条文里没有正文（只有范围说明）")

    # 2) 驾驶证这种大法规，条款数应上百；明显偏少说明切分规则退化
    arts2 = S.parse_articles(os.path.join(KB, "driving_license.md"))
    if len(arts2) < 80:
        errors.append(f"驾驶证只切出 {len(arts2)} 条，明显偏少")

    # 3) 判定方向：事实层逐字引用的句子应判为已覆盖
    src = S.load_fact_source()
    if "机动车发生交通事故造成损害" in src:
        quoted = "机动车发生交通事故造成损害，属于该机动车一方责任的"
        if not S.covered(quoted, src):
            errors.append("逐字引用的条文被判成缺口（判定方向反了）")
    if S.covered("这句原文肯定不在事实层代码里出现过哦", src):
        errors.append("不存在的句子被判成已覆盖（判定过宽）")

    # 4) 要素识别：没有明确要素的原则性表述不该算进分母
    if S.elements_of("机动车驾驶人应当遵守道路交通安全法律法规，安全驾驶"):
        errors.append("原则性表述被算成有明确要素，分母会虚高")
    if not S.elements_of("申请小型汽车准驾车型的，年龄应当在 18 周岁以上"):
        errors.append("年龄要素没识别出来")

    # 5) 全量跑一遍，确认不抛异常且总量不为零
    data = S.scan(5)
    solid = sum(f["solid"] for f in data["files"])
    if solid < 100:
        errors.append(f"全量只有 {solid} 条含明确要素，明显不对")

    if errors:
        print("事实层覆盖体检自检失败：")
        for e in errors:
            print("  - " + e)
        return 1
    print(f"覆盖体检自检：{len(data['files'])} 部法规切分正常，判定与要素识别方向正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
