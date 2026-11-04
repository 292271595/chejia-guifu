# -*- coding: utf-8 -*-
"""交通事故致人伤亡的刑事/行政责任事实层
================================================
为什么单独建表（2026-11-04 线上错答案例）：
  用户问「造成交通事故致人死亡，负同等以上责任如何处理」，模型连答三版全错：
    第1版：负同等以上责任致人死亡 → 处三年以下（把不构罪的同等责任算成了犯罪）
    第2版：可能构成交通肇事罪，第二条和第四条有详细规定（含糊，不给分界）
    第3版：死亡三人以上负同等责任 → 三年以上七年以下（分项对了，刑罚档又串到第四条）
  检索没问题（司法解释第二条原文完整进了上下文），条号也没编。
  错在**模型不会做「责任等级 × 死亡人数」的二维判决**：法条把入罪门槛拆成
  (一)(二)(三) 三项、各带不同的责任等级和人数条件，8B 模型反复把 A 项的条件
  配上 B 项的刑罚。这不是 prompt 能压住的——它需要同时持有两个维度再查表。
  所以按项目既有惯例（fact_table 记分/年龄同款做法），把这张二维表固化下来，
  命中即直答，完全不经过生成模型。

全部内容逐字取自：
- 《最高人民法院关于审理交通肇事刑事案件具体应用法律若干问题的解释》（法释〔2000〕33号）
  第二条（入罪三年以下）、第四条（"其他特别恶劣情节"三年以上七年以下）
- 《中华人民共和国刑法》第一百三十三条（交通肇事罪三档刑罚）
- 《中华人民共和国道路交通安全法》第一百零一条（吊销/终生禁驾）
- 《中华人民共和国民法典》第一千二百一十三条（赔偿顺序）、第一千一百七十九条（赔偿项目）
"""

import re
from typing import Dict, List, Optional

from fact_common import source, result, cn2int

LAW_SPC = "《最高人民法院关于审理交通肇事刑事案件具体应用法律若干问题的解释》"
LAW_CRIM = "《中华人民共和国刑法》"
LAW_ROAD = "《中华人民共和国道路交通安全法》"
LAW_CIVIL = "《中华人民共和国民法典》"

# 责任等级：法条把"全部/主要"归为一档，"同等"单列一档，"次要"不构罪
RESP_MAIN = ("全部", "主要")
RESP_EQUAL = ("同等",)

_NUM = r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})"

# 主题闸门：必须同时出现"死亡类后果"和"责任/处理"意图，宁窄不宽。
# 放宽会把"死亡赔偿金怎么算""交强险能赔多少"这类纯民事问题劫持过来。
_DEATH_RE = re.compile(r"(?:致?人死亡|死亡|撞死|身亡|致死|死了)")
_INTENT_RE = re.compile(
    r"(?:责任|处理|怎么办|怎么判|如何判|判几|几年|量刑|坐牢|刑事|构成|犯罪|"
    r"罪|起诉|判刑|定罪|吊销|后果|赔付|赔偿)"
)
# 纯民事/保险问句不接：这些问题的答案是赔偿标准和理赔流程，不是刑事责任分档
_CIVIL_ONLY_RE = re.compile(r"(赔偿标准|死亡赔偿金|丧葬费|精神损害|交强险.{0,6}(赔|多少)|理赔流程|起诉状)")
# 只问"会不会吊销驾照"的窄问题：不必摊开整张判决表
_ADMIN_ONLY_RE = re.compile(r"(吊销|终生禁驾|终身禁驾|驾照.{0,4}(没|废|保|还))")
_CRIM_WORD_RE = re.compile(r"(判|罪|刑事|坐牢|量刑|构成|起诉|几年)")


def _parse_resp(q: str):
    """识别用户说的责任等级。返回 (档位, 用户原词)。

    档位：main（全部/主要，法条同一档）/ equal（同等）/ minor（次要，一律不构罪）/ None（未指明）
    原词用于回写答案，避免用户问"主要责任"却答成"全部责任"——这是答非所问的典型。
    """
    if "同等以上" in q or "同等及以上" in q:
        return "both", "同等以上"
    if "次要" in q:
        return "minor", "次要"
    has_equal = "同等" in q
    has_main = ("全部" in q) or ("主要" in q)
    if has_equal and has_main:
        return "both", ""
    if has_equal:
        return "equal", "同等"
    if has_main:
        return "main", ("主要" if "主要" in q else "全部")
    return None, ""


def _parse_deaths(q: str) -> Optional[int]:
    """从问题里读死亡人数。"重伤三人"不算死亡人数。"""
    m = re.search(rf"死(?:亡)?\s*({_NUM})\s*人", q)
    if not m:
        m = re.search(rf"({_NUM})\s*人\s*(?:死亡|身亡)", q)
    if not m:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else cn2int(raw)
    return n if n and n > 0 else None


def _src(title: str, snippet: str, f: str) -> Dict:
    return source(title, snippet, f)


def _sources() -> List[Dict]:
    return [
        _src(f"{LAW_SPC} 第二条",
             "交通肇事具有下列情形之一的，处三年以下有期徒刑或者拘役：(一)死亡一人或者重伤三人以上，"
             "负事故全部或者主要责任的;(二)死亡三人以上，负事故同等责任的;(三)造成公共财产或者他人财产"
             "直接损失，负事故全部或者主要责任，无能力赔偿数额在三十万元以上的。",
             "spc_traffic_accident_interpretation.md"),
        _src(f"{LAW_SPC} 第四条",
             "交通肇事具有下列情形之一的，属于“有其他特别恶劣情节”，处三年以上七年以下有期徒刑："
             "(一)死亡二人以上或者重伤五人以上，负事故全部或者主要责任的;"
             "(二)死亡六人以上，负事故同等责任的。",
             "spc_traffic_accident_interpretation.md"),
        _src(f"{LAW_CRIM} 第一百三十三条",
             "违反交通运输管理法规，因而发生重大事故，致人重伤、死亡或者使公私财产遭受重大损失的，"
             "处三年以下有期徒刑或者拘役；交通运输肇事后逃逸或者有其他特别恶劣情节的，处三年以上七年"
             "以下有期徒刑；因逃逸致人死亡的，处七年以上有期徒刑。",
             "criminal_law_traffic.md"),
        _src(f"{LAW_ROAD} 第一百零一条",
             "违反道路交通安全法律、法规的规定，发生重大交通事故，构成犯罪的，依法追究刑事责任，"
             "并由公安机关交通管理部门吊销机动车驾驶证。造成交通事故后逃逸的，由公安机关交通管理部门"
             "吊销机动车驾驶证，且终生不得重新取得机动车驾驶证。",
             "road_traffic_safety_law.md"),
    ]


def _penalty_block(n: int, resp: str) -> str:
    """按 (责任等级, 死亡人数) 给出刑事结论。resp 用用户原话（全部/主要/同等）。"""
    if resp in RESP_MAIN:
        if n == 1:
            return (f"负{resp}责任、死亡 1 人 → 构成交通肇事罪，处三年以下有期徒刑或者拘役"
                    f"（{LAW_SPC}第二条第一项）")
        return (f"负{resp}责任、死亡 2 人以上 → 属“其他特别恶劣情节”，处三年以上七年以下有期徒刑"
                f"（第四条第一项）")
    # 同等责任
    if n <= 2:
        return (f"负同等责任、死亡 {n} 人 → **不构成交通肇事罪**（第二条第二项要求“死亡三人以上”，"
                f"人数不够），只承担民事赔偿，不追究刑事责任")
    if n <= 5:
        return (f"负同等责任、死亡 {n} 人 → 构成交通肇事罪，处三年以下有期徒刑或者拘役"
                f"（第二条第二项）")
    return (f"负同等责任、死亡 {n} 人（六人以上）→ 属“其他特别恶劣情节”，处三年以上七年以下有期徒刑"
            f"（第四条第二项）")


def q_accident_death(q: str) -> Optional[Dict]:
    """交通事故致人死亡的刑事责任分档（确定性直答）"""
    if not _DEATH_RE.search(q):
        return None
    if not _INTENT_RE.search(q):
        return None
    if _CIVIL_ONLY_RE.search(q):
        return None
    # 排除"醉驾/逃逸"为主诉的问题：那类归吊销规则管，硬套本表会把刑期和禁驾混在一起
    if re.search(r"(醉驾|醉酒|酒驾|饮酒|毒驾|吸毒)", q):
        return None

    scope, word = _parse_resp(q)
    deaths = _parse_deaths(q)

    # 只问"驾驶证会不会被吊销"：给准这一句就够，摊开整张判决表等于答非所问
    if _ADMIN_ONLY_RE.search(q) and not _CRIM_WORD_RE.search(q):
        lines = [
            "**分三种情形**：",
            f"· 发生重大交通事故**构成犯罪**的 → 吊销机动车驾驶证，并依法追究刑事责任"
            f"（{LAW_ROAD}第一百零一条第一款）。",
            f"· 事故后**逃逸**的 → 吊销驾驶证，且**终生不得重新取得**（第一百零一条第二款），"
            f"不以构成犯罪为前提。",
            "· 只是致人死亡、未被认定构成犯罪（如负次要责任、或负同等责任但死亡不足三人）的，"
            "不因该事故吊销驾驶证，按民事赔偿处理。",
        ]
        return result("\n".join(lines), _sources(), "ACCIDENT_DEATH_ADMIN",
                      aspects={"PENALTY", "QUALIFY"})

    lines: List[str] = []
    # 只有"责任等级明确 + 人数明确 + 结论唯一为不构罪"时，才敢说"本案不构成犯罪"。
    # 分档表里虽然也含"不构成交通肇事罪"这一行，但那是在讲各种情形，不是本案结论。
    no_crime = False
    if scope == "minor":
        lines.append("负次要责任致人死亡 → **不构成交通肇事罪**，不追究刑事责任。"
                     "交通肇事罪的入罪门槛是「负全部或者主要责任」或「负同等责任且死亡三人以上」"
                     f"（{LAW_SPC}第二条），次要责任不在此列。")
        lines.append("")
        lines.append(f"仍需承担民事赔偿：先由交强险赔付，不足部分按责任比例分担"
                     f"（{LAW_CIVIL}第一千二百一十三条）；死亡赔偿含丧葬费、死亡赔偿金"
                     f"（第一千一百七十九条）。")
        return result("\n".join(lines), _sources(), "ACCIDENT_DEATH_MINOR",
                      aspects={"PENALTY", "QUALIFY"})

    if deaths is not None:
        # 人数已明确：只给对应结论，不再罗列其他档
        if scope == "both":
            lines.append(f"你的情形是死亡 {deaths} 人，按责任等级分别是：")
            lines.append("· " + _penalty_block(deaths, "全部"))
            lines.append("· " + _penalty_block(deaths, "同等"))
        elif scope == "main":
            lines.append(_penalty_block(deaths, word or "全部"))
        elif scope == "equal":
            lines.append(_penalty_block(deaths, "同等"))
            no_crime = deaths <= 2   # 同等责任 + 死亡不足 3 人 = 唯一确定的"不构罪"结论
        else:
            lines.append(f"死亡 {deaths} 人，是否构罪取决于责任等级：")
            lines.append("· " + _penalty_block(1 if deaths == 1 else 2, "全部"))
            lines.append("· " + _penalty_block(deaths, "同等"))
    else:
        lines.append("致人死亡的刑事责任，按“责任等级 + 死亡人数”两档把关，你属于哪一档就按哪一档：")
        if scope in ("main", "both", None):
            lines.append("")
            lines.append("**负全部或者主要责任**")
            lines.append("· 死亡 1 人（或重伤 3 人以上）→ 交通肇事罪，三年以下有期徒刑或者拘役"
                         f"（{LAW_SPC}第二条第一项）")
            lines.append("· 死亡 2 人以上 → “其他特别恶劣情节”，三年以上七年以下有期徒刑（第四条第一项）")
        if scope in ("equal", "both", None):
            lines.append("")
            lines.append("**负同等责任**")
            lines.append("· 死亡 3 人以上不满 6 人 → 交通肇事罪，三年以下有期徒刑或者拘役（第二条第二项）")
            lines.append("· 死亡 6 人以上 → 三年以上七年以下有期徒刑（第四条第二项）")
            lines.append("· **死亡不足 3 人 → 不构成交通肇事罪**，不追究刑事责任")

    lines.append("")
    if no_crime:
        # 已判定不构罪时，再挂一条"构成犯罪的吊销驾驶证"只会自相矛盾，改成正面结论
        lines.append("**本案后果**")
        lines.append("· 不构成犯罪，不追究刑事责任，也不因本次事故吊销机动车驾驶证")
        lines.append("· 事故后逃逸的另论：吊销驾驶证且终生不得重新取得"
                     f"（{LAW_ROAD}第一百零一条第二款）")
    else:
        lines.append("**附带后果（不论是否构罪都适用）**")
        lines.append(f"· 构成犯罪的，吊销机动车驾驶证（{LAW_ROAD}第一百零一条第一款）")
        lines.append("· 事故后逃逸的，吊销驾驶证且终生不得重新取得（第一百零一条第二款）；"
                     f"因逃逸致人死亡的，处七年以上有期徒刑（{LAW_CRIM}第一百三十三条）")
    lines.append(f"· 民事赔偿：先由交强险赔付，不足部分按责任比例由商业险或侵权人赔偿"
                 f"（{LAW_CIVIL}第一千二百一十三条）；死亡赔偿含丧葬费、死亡赔偿金"
                 f"（第一千一百七十九条）")

    return result("\n".join(lines), _sources(), "ACCIDENT_DEATH",
                  aspects={"PENALTY", "QUALIFY"})
