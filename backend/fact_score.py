# -*- coding: utf-8 -*-
"""
记分事实层（163号令 第八~十二条）
==================================
压力测试暴露的核心缺陷：记分规则用"关键词 + 维度组"描述，
`need=[["超员","超载"], ["7座","七座","百分之五十","50%"]]` 这种写法
让"货车超载50%"命中了"7座以上载客汽车超员50%"的规则，答成 9 分（应为 6 分）。

根因不是某条规则写错，而是**规则维度没有显式化**：
超员 / 超载 / 超速 三件事的判分维度是
    （违法类型）×（车辆/人员主体）×（量级百分比）×（道路类型）
四元组，而旧写法把"主体"和"量级"混在关键词里，必然互相污染。

本模块的做法：
1. 超员 / 超载 / 超速 改为**矩阵查表**，四个维度分别解析，逐格对照法条；
2. 其余定性违法（信号灯、应急车道、遮挡号牌等）仍走关键词规则，但每条
   规则显式声明 subject，主体不符直接跳过；
3. 任一维度缺失（比如没说是否高速）→ 不猜，输出分档对照表。
"""

import re
from typing import Dict, List, Optional

from fact_common import (parse_percent, subject_of, road_of, norm, has,
                         cn2int, source, result)

LAW = "《道路交通安全违法行为记分管理办法》（公安部令第163号）"
SRC_FILE = "traffic_violations.md"

# ============================================================
# 一、超员矩阵（载人超过核定人数）
# ============================================================
# 主体分类：
#   ops_bus   校车、公路客运汽车、旅游客运汽车
#   seat7     7座以上载客汽车（非上述营运客车）
#   other     其他载客汽车（5座及以下小客车等）
# 分档：(下限含, 上限不含, 分值, 条款)
OVERLOAD_PAX = {
    "ops_bus": [(0, 20, 6, "第十条第一项"), (20, 10 ** 9, 12, "第八条第四项")],
    "seat7":   [(20, 50, 6, "第十条第一项"), (50, 100, 9, "第九条第一项"),
                (100, 10 ** 9, 12, "第八条第四项")],
    "other":   [(20, 50, 3, "第十一条第一项"), (50, 100, 6, "第十条第一项"),
                (100, 10 ** 9, 12, "第八条第四项")],
}
OVERLOAD_PAX_DESC = {
    "ops_bus": "驾驶校车、公路客运汽车、旅游客运汽车载人超过核定人数",
    "seat7": "驾驶7座以上载客汽车载人超过核定人数",
    "other": "驾驶其他载客汽车载人超过核定人数",
}

# ============================================================
# 二、超载矩阵（载货超过最大允许总质量）
# ============================================================
OVERLOAD_CARGO = [(0, 30, 1, "第十二条第六项"), (30, 50, 3, "第十一条第九项"),
                  (50, 10 ** 9, 6, "第十条第四项")]

# ============================================================
# 三、超速矩阵（道路类型 × 车辆类别 × 百分比）
# ============================================================
# 车辆类别：ops 校车、中型以上载客载货汽车、危险物品运输车辆；other 其他机动车
OVERSPEED = {
    ("highway", "ops"):   [(0, 20, 6, "第十条第二项"), (20, 10 ** 9, 12, "第八条第五项")],
    ("highway", "other"): [(0, 20, 0, ""), (20, 50, 6, "第十条第三项"),
                           (50, 10 ** 9, 12, "第八条第五项")],
    ("other", "ops"):     [(0, 10, 0, ""), (10, 20, 1, "第十二条第一项"),
                           (20, 50, 6, "第十条第二项"), (50, 10 ** 9, 9, "第九条第二项")],
    ("other", "other"):   [(0, 20, 0, ""), (20, 50, 3, "第十一条第二项"),
                           (50, 10 ** 9, 6, "第十条第三项")],
}

# ============================================================
# 四、定性违法关键词规则（显式声明主体，避免跨主体抢答）
# ============================================================
SCORE_RULES: List[Dict] = [
    # ── 12 分 ──
    dict(score=12, law="第八条第七项", subject="any",
         desc="代替实际机动车驾驶人接受交通违法行为处罚和记分牟取经济利益的",
         need=[["代替"]], any_=["记分", "处罚", "牟利", "经济利益", "卖分", "买分"], none=[]),
    dict(score=12, law="第八条第一项", desc="饮酒后驾驶机动车的", subject="any",
         need=[["饮酒"]], any_=["驾驶", "开车", "上路"], none=["醉酒", "营运"]),
    dict(score=12, law="第八条第三项", subject="any",
         desc="使用伪造、变造的机动车号牌、行驶证、驾驶证、校车标牌或者使用其他机动车号牌、行驶证的",
         need=[["伪造", "变造", "假牌", "假证", "套牌", "假号牌"]], any_=[], none=[]),
    dict(score=12, law="第八条第六项", subject="any",
         desc="驾驶机动车在高速公路、城市快速路上倒车、逆行、穿越中央分隔带掉头的",
         need=[["高速", "快速路"], ["倒车", "逆行", "掉头", "穿越中央分隔带"]], any_=[], none=[]),
    dict(score=12, law="第八条第二项", subject="any",
         desc="造成致人轻伤以上或者死亡的交通事故后逃逸，尚不构成犯罪的",
         need=[["逃逸"], ["轻伤", "重伤", "死亡"]], any_=[], none=["犯罪", "轻微伤"]),

    # ── 9 分 ──
    dict(score=9, law="第九条第三项", subject="any",
         desc="驾驶机动车在高速公路或者城市快速路上违法停车的",
         need=[["停车", "违停"], ["高速", "快速路", "应急车道"]],
         any_=[], none=["行驶", "非机动车道", "人行道"]),
    dict(score=9, law="第九条第四项", subject="any",
         desc="驾驶未悬挂机动车号牌或者故意遮挡、污损机动车号牌的机动车上道路行驶的",
         need=[["遮挡", "污损", "未悬挂", "不挂号牌", "没挂牌"]], any_=["号牌"], none=[]),
    dict(score=9, law="第九条第五项", subject="any",
         desc="驾驶与准驾车型不符的机动车的",
         need=[["准驾车型不符", "不符"]], any_=[], none=[]),
    dict(score=9, law="第九条第七项", subject="passenger",
         desc="连续驾驶中型以上载客汽车、危险物品运输车辆超过4小时未停车休息或者停车休息时间少于20分钟的",
         need=[["4小时", "四小时", "四个小时"], ["休息", "疲劳"]], any_=[], none=["货车", "载货"]),
    dict(score=9, law="第九条第六项", subject="any",
         desc="未取得校车驾驶资格驾驶校车的", need=[["校车"], ["资格"]], any_=[], none=[]),

    # ── 6 分 ──
    dict(score=6, law="第十条第十一项", subject="any",
         desc="驾驶机动车在高速公路或者城市快速路上违法占用应急车道行驶的",
         need=[["应急车道"]], any_=["行驶", "占用", "走", "通行"], none=["停车", "停放"]),
    dict(score=6, law="第十条第八项", subject="any",
         desc="驾驶机动车不按交通信号灯指示通行的",
         need=[["信号灯", "闯红灯", "红灯", "红绿灯"]], any_=[], none=["黄灯"]),
    dict(score=6, law="第十条第九项", subject="any",
         desc="机动车驾驶证被暂扣或者扣留期间驾驶机动车的",
         need=[["暂扣", "扣留"]], any_=["驾驶", "开车", "期间"], none=[]),
    dict(score=6, law="第十条第十项", subject="any",
         desc="造成致人轻微伤或者财产损失的交通事故后逃逸，尚不构成犯罪的",
         need=[["逃逸"]], any_=["轻微伤", "财产损失", "刮蹭", "剐蹭"],
         none=["犯罪", "轻伤", "重伤", "死亡"]),
    dict(score=6, law="第十条第五项", subject="danger",
         desc="驾驶机动车载运爆炸物品、易燃易爆化学物品以及剧毒、放射性等危险物品，未按指定的时间、路线、速度行驶或者未悬挂警示标志并采取必要的安全措施的",
         need=[["危险物品", "危化品", "爆炸物品", "剧毒", "放射性"]], any_=[], none=[]),
    dict(score=6, law="第十条第六项", subject="cargo",
         desc="驾驶机动车运载超限的不可解体的物品，未按指定的时间、路线、速度行驶或者未悬挂警示标志的",
         need=[["不可解体", "超限"]], any_=["未按", "悬挂", "标志"], none=[]),
    dict(score=6, law="第十条第七项", subject="danger",
         desc="驾驶机动车运输危险化学品，未经批准进入危险化学品运输车辆限制通行的区域的",
         need=[["危险化学品", "危化品"], ["限制通行", "未经批准"]], any_=[], none=[]),

    # ── 3 分 ──
    dict(score=3, law="第十一条第六项", subject="any",
         desc="驾驶机动车有拨打、接听手持电话等妨碍安全驾驶的行为的",
         need=[["电话", "手机"]], any_=["拨打", "接听", "打", "玩", "手持"], none=[]),
    dict(score=3, law="第十一条第七项", subject="any",
         desc="驾驶机动车行经人行横道不按规定减速、停车、避让行人的",
         need=[["人行横道", "斑马线", "礼让行人", "让行人"]],
         any_=["不让", "不避让", "抢行", "不减速", "不礼让", "未避让", "未让"], none=[]),
    dict(score=3, law="第十一条第三项", subject="any",
         desc="驾驶机动车在高速公路或者城市快速路上不按规定车道行驶的",
         need=[["高速", "快速路"], ["车道"]], any_=[], none=["应急车道", "停车"]),
    dict(score=3, law="第十一条第四项", subject="any",
         desc="驾驶机动车不按规定超车、让行，或者在高速公路、城市快速路以外的道路上逆行的",
         need=[["逆行"]], any_=[], none=["高速", "快速路"]),
    dict(score=3, law="第十一条第八项", subject="any",
         desc="驾驶机动车不按规定避让校车的", need=[["校车"], ["避让"]], any_=[], none=[]),
    dict(score=3, law="第十一条第十五项", subject="any",
         desc="驾驶机动车在高速公路上行驶低于规定最低时速的",
         need=[["高速", "快速路"], ["最低时速", "低速", "低于规定", "龟速"]], any_=[], none=[]),
    dict(score=3, law="第十一条第十四项", subject="cargo",
         desc="连续驾驶载货汽车超过4小时未停车休息或者停车休息时间少于20分钟的",
         need=[["4小时", "四小时", "四个小时"], ["休息", "疲劳"]], any_=["货车", "载货"], none=[]),
    dict(score=3, law="第十一条第五项", subject="any",
         desc="驾驶机动车遇前方机动车停车排队或者缓慢行驶时，借道超车或者占用对面车道、穿插等候车辆的",
         need=[["加塞", "穿插", "借道超车"]], any_=[], none=[]),
    dict(score=3, law="第十一条第十项", subject="any",
         desc="驾驶不按规定安装机动车号牌的机动车上道路行驶的",
         need=[["不按规定安装", "号牌安装"]], any_=["号牌"], none=[]),
    dict(score=3, law="第十一条第十一项", subject="any",
         desc="在道路上车辆发生故障、事故停车后，不按规定使用灯光或者设置警告标志的",
         need=[["故障", "事故"]], any_=["警告标志", "三角牌", "灯光"], none=[]),
    dict(score=3, law="第十一条第十二项", subject="ops_bus",
         desc="驾驶未按规定定期进行安全技术检验的公路客运汽车、旅游客运汽车、危险物品运输车辆上道路行驶的",
         need=[["检验", "年检"], ["未按规定", "逾期", "超期"]], any_=["公路客运", "旅游客运", "危险物品"],
         none=["处罚", "罚款"]),

    # ── 1 分 ──
    dict(score=1, law="第十二条第九项", subject="any",
         desc="驾驶机动车在道路上行驶时，机动车驾驶人未按规定系安全带的",
         need=[["安全带"]], any_=[], none=[]),
    dict(score=1, law="第十二条第十项", subject="any",
         desc="驾驶摩托车，不戴安全头盔的", need=[["头盔"]], any_=["摩托"], none=[]),
    dict(score=1, law="第十二条第三项", subject="any",
         desc="驾驶机动车不按规定使用灯光的",
         need=[["灯光"]], any_=["不按规定", "滥用", "远光"], none=["故障", "事故"]),
    dict(score=1, law="第十二条第四项", subject="any",
         desc="驾驶机动车违反禁令标志、禁止标线指示的",
         need=[["禁令标志", "禁止标线", "压实线", "压线"]], any_=[], none=[]),
    dict(score=1, law="第十二条第二项", subject="any",
         desc="驾驶机动车不按规定会车，或者在高速公路、城市快速路以外的道路上不按规定倒车、掉头的",
         need=[["会车", "倒车", "掉头"]], any_=[], none=["高速", "快速路"]),
    dict(score=1, law="第十二条第五项", subject="cargo",
         desc="驾驶机动车载货长度、宽度、高度超过规定的",
         need=[["载货", "货物"], ["超长", "超宽", "超高", "超过规定"]], any_=[], none=[]),
    dict(score=1, law="第十二条第七项", subject="any",
         desc="驾驶未按规定定期进行安全技术检验的（公路客运、旅游客运、危险物品运输车辆以外的）机动车上道路行驶的",
         need=[["检验", "年检"], ["未按规定", "逾期", "超期"]], any_=[], none=["处罚", "罚款"]),
]


def _in_band(pct: int, lo: int, hi: int) -> bool:
    return lo <= pct < hi


def _band(rule_list, pct):
    for lo, hi, score, law in rule_list:
        if _in_band(pct, lo, hi):
            return score, law
    return None, None


def _fmt_bands(rule_list, desc, unit="%"):
    lines = []
    for lo, hi, score, law in rule_list:
        if hi >= 10 ** 8:
            span = f"{lo}{unit}以上"
        elif lo == 0:
            span = f"未达到{hi}{unit}"
        else:
            span = f"{lo}{unit}以上未达到{hi}{unit}"
        if score:
            lines.append(f"- {span}：**记 {score} 分**（{law}）")
        else:
            lines.append(f"- {span}：**不记分**（法规未设记分，仍可按道交法罚款）")
    return f"{desc}，记分按超出的比例分档：\n\n" + "\n".join(lines)


def _score_result(score: int, law: str, desc: str, extra: str = "") -> Dict:
    answer = (f"**一次记 {score} 分。**\n\n"
              f"对应情形：{desc}。\n\n"
              f"依据：{LAW}{law}。\n\n"
              f"记分周期为 12 个月，满分为 12 分。")
    if extra:
        answer += "\n\n" + extra
    return result(answer, [source(f"{LAW}{law}", desc, SRC_FILE)],
                  f"SCORE:{score}")


# ============================================================
# 五、三类定量违法
# ============================================================
def q_overload_pax(q: str) -> Optional[Dict]:
    """载人超过核定人数（超员）。"""
    if not re.search(r"(超员|超载|载人|核定人数|乘客|拉人)", q):
        return None
    # 主体是载货（货车超载）就绝不能用"载人超员"的档位判分
    if subject_of(q) == "cargo":
        return None
    if re.search(r"载货|货物|超重|超限|货车", q) and not re.search(r"载人|超员|乘客", q):
        return None
    pct = parse_percent(q)
    if re.search(r"7座|七座", q):
        cls = "seat7"
    elif re.search(r"校车|公路客运|旅游客运|营运客", q):
        cls = "ops_bus"
    elif re.search(r"客车|载客", q):
        cls = "other"
    else:
        cls = "other"

    if pct is None:
        body = "\n\n".join(_fmt_bands(v, OVERLOAD_PAX_DESC[k]) for k, v in OVERLOAD_PAX.items())
        return result(
            "超员记分要看**车型 + 超员比例**两个维度，不能一概而论：\n\n" + body +
            "\n\n请补充车型（校车/公路客运/旅游客运、7座以上客车、其他小客车）和超员比例。",
            [source(f"{LAW}第八条第四项、第九条第一项、第十条第一项、第十一条第一项",
                    "驾驶校车、公路客运汽车、旅游客运汽车载人超过核定人数百分之二十以上，或者驾驶其他载客汽车载人超过核定人数百分之百以上的，一次记12分")],
            "SCORE:OVERLOAD_PAX_TABLE")

    score, law = _band(OVERLOAD_PAX[cls], pct)
    if score is None:
        return result(
            f"**超员 {pct}% 在现行记分办法中没有对应记分条款，不记分。**\n\n"
            f"{OVERLOAD_PAX_DESC[cls]}{pct}%，低于最低记分档（{OVERLOAD_PAX[cls][0][0]}%）。\n\n"
            "注意：不记分不等于不处罚，仍可按《道路交通安全法》处以罚款。",
            [source(f"{LAW}", "超员记分最低档为百分之二十")], "SCORE:0")
    desc = f"{OVERLOAD_PAX_DESC[cls]}{pct}%" if pct else OVERLOAD_PAX_DESC[cls]
    return _score_result(score, law, desc)


def q_overload_cargo(q: str) -> Optional[Dict]:
    """载货超过最大允许总质量（超载）。"""
    if not re.search(r"(超载|超重|超限|超过最大允许总质量|超过核定载质量)", q):
        return None
    if subject_of(q) == "passenger":
        return None
    if re.search(r"超员|载客|载人|乘客|核定人数", q):
        return None
    pct = parse_percent(q)
    if pct is None:
        return result(
            "载货超载（超过最大允许总质量）按比例分三档：\n\n" +
            _fmt_bands(OVERLOAD_CARGO, "驾驶载货汽车载物超过最大允许总质量") +
            "\n\n请补充超载比例。",
            [source(f"{LAW}第十条第四项、第十一条第九项、第十二条第六项",
                    "驾驶载货汽车载物超过最大允许总质量百分之五十以上的，一次记6分")],
            "SCORE:OVERLOAD_CARGO_TABLE")
    score, law = _band(OVERLOAD_CARGO, pct)
    if score is None:
        return None
    return _score_result(score, law, f"驾驶载货汽车载物超过最大允许总质量{pct}%")


def q_overspeed(q: str) -> Optional[Dict]:
    """超速。"""
    if not re.search(r"超速|超过规定时速|开快|飙车", q):
        return None
    pct = parse_percent(q)
    road = road_of(q)
    cls = "ops" if re.search(r"校车|中型以上|大客车|大货车|货车|客运|旅游|危险物品|危化品", q) else "other"

    if pct is None or road == "any":
        lines = ["超速记分要看**道路类型 + 车辆类别 + 超速比例**三个维度：", ""]
        for r, rname in (("highway", "高速公路/城市快速路"), ("other", "高速公路、城市快速路以外的道路")):
            for c, cname in (("ops", "校车、中型以上载客载货汽车、危险物品运输车辆"), ("other", "其他机动车")):
                lines.append(f"**{rname} · {cname}**")
                lines.append(_fmt_bands(OVERSPEED[(r, c)], "超速"))
                lines.append("")
        lines.append("请补充是否在高速公路/城市快速路上行驶、以及超速比例。")
        return result("\n".join(lines),
                      [source(f"{LAW}第八条第五项、第九条第二项、第十条第二项第三项、第十一条第二项",
                              "驾驶校车、中型以上载客载货汽车、危险物品运输车辆在高速公路、城市快速路上行驶超过规定时速百分之二十以上……一次记12分")],
                      "SCORE:OVERSPEED_TABLE")

    score, law = _band(OVERSPEED[(road, cls)], pct)
    if score is None:
        return None
    if score == 0:
        return result(
            f"**超速 {pct}% 在该道路类型与车辆类别下没有对应记分条款，不记分。**\n\n"
            "现行记分办法对超速按「道路类型 × 车辆类别 × 比例」分档，未达到最低分档的不记分，"
            "但仍可依法处以罚款（《道路交通安全法》第九十九条）。",
            [source(LAW, "超速记分最低档为百分之十或百分之二十")], "SCORE:0")
    road_name = "高速公路/城市快速路" if road == "highway" else "高速公路、城市快速路以外的道路"
    cls_name = "校车、中型以上载客载货汽车、危险物品运输车辆" if cls == "ops" else "其他机动车"
    return _score_result(score, law, f"驾驶{cls_name}在{road_name}上行驶超过规定时速{pct}%")


# ============================================================
# 六、定性违法（关键词规则 + 主体校验）
# ============================================================
def _match_rule(rule: Dict, q: str, subj: str) -> bool:
    want = rule.get("subject", "any")
    if want != "any" and subj != "any" and want != subj:
        return False
    for group in rule.get("need", []):
        if not has(q, group):
            return False
    if rule.get("any_") and not has(q, rule["any_"]):
        return False
    if rule.get("none") and has(q, rule["none"]):
        return False
    return True


def q_drunk(q: str) -> Optional[Dict]:
    """饮酒 / 醉酒的分值口径（两者性质不同，不能混答）。"""
    if "醉酒" in q:
        return result(
            "**醉酒驾驶不是记分问题，而是吊销驾驶证 + 追究刑事责任。**\n\n"
            "醉酒驾驶机动车（血液酒精含量 ≥ 80mg/100ml）的：\n"
            "- 由公安机关交通管理部门**约束至酒醒，吊销机动车驾驶证**，依法追究刑事责任；\n"
            "- **5 年内不得重新取得**机动车驾驶证（醉酒驾驶营运机动车的为 **10 年**，"
            "且重新取得后不得驾驶营运机动车）；\n"
            "- 发生重大交通事故构成犯罪的，终生不得重新取得。\n\n"
            "依据：《道路交通安全法》第九十一条；《机动车驾驶证申领和使用规定》"
            "第十五条第五项、第六项。\n\n"
            "**对比记忆**：饮酒后驾驶（20mg/100ml ≤ 血液酒精 < 80mg/100ml，未达醉酒）的，"
            "**一次记 12 分**（163号令第八条第一项），并暂扣驾驶证 6 个月、罚款 1000～2000 元。"
            "很多人把\"醉驾\"和\"酒驾\"的分值混为一谈——醉驾根本走不到记分这一步，是直接吊销。",
            [source("《道路交通安全法》第九十一条",
                    "醉酒驾驶机动车的，由公安机关交通管理部门约束至酒醒，吊销机动车驾驶证，"
                    "依法追究刑事责任；五年内不得重新取得机动车驾驶证", SRC_FILE),
             source(f"{LAW}第八条第一项", "饮酒后驾驶机动车的，一次记12分", SRC_FILE)],
            "SCORE:DRUNK")
    return None


def q_study_reduce(q: str) -> Optional[Dict]:
    """学法减分（记分减免）。"""
    if not re.search(r"(学法减分|减分|扣减记分|参加.*教育.*减)", q):
        return None
    return result(
        "**学法减分：一个记分周期内累计最高扣减 6 分。**\n\n"
        "依据第二十五条：机动车驾驶人处理完交通违法行为记录后累积记分未满 12 分，"
        "参加公安机关交通管理部门组织的交通安全教育并达到规定要求的，"
        "可以申请在现有累积记分分值中扣减记分，"
        "**在一个记分周期内累计最高扣减 6 分**。\n\n"
        "不予受理的情形（第二十六条）：\n"
        "1. 本记分周期或上一记分周期内有 2 次以上满分教育记录；\n"
        "2. 最近 3 个记分周期内因逃逸、饮酒驾驶、使用伪造变造牌证、买分卖分受过处罚；\n"
        "3. 驾驶证在实习期内，或逾期未审验，或被扣留、暂扣期间；\n"
        "4. 名下有安全技术检验超过有效期或未按规定注销登记的机动车；\n"
        "5. 最近 3 个记分周期内参加学法减分、满分教育、审验教育时有弄虚作假、冒名顶替记录。",
        [source("《道路交通安全违法行为记分管理办法》第二十五条",
                "参加公安机关交通管理部门组织的交通安全教育并达到规定要求的，可以申请在机动车驾驶人"
                "现有累积记分分值中扣减记分。在一个记分周期内累计最高扣减6分", SRC_FILE)],
        "SCORE:STUDY_REDUCE")


def q_score(question: str) -> Optional[Dict]:
    """记分总入口：先特殊口径（醉驾/学法减分），再定量（超员/超载/超速），最后定性。"""
    q = norm(question)
    for special in (q_drunk, q_study_reduce):
        r = special(q)
        if r:
            return r

    for fn in (q_overload_pax, q_overload_cargo, q_overspeed):
        try:
            r = fn(q)
        except Exception as e:
            print(f"[fact_score] {fn.__name__} 异常: {e}")
            r = None
        if r:
            r["covers"] = frozenset({"SCORE", "VALUE"})
            return r

    subj = subject_of(q)
    for rule in SCORE_RULES:
        if not _match_rule(rule, q, subj):
            continue
        return _score_result(rule["score"], rule["law"], rule["desc"])
    return None
