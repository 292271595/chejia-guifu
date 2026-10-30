# -*- coding: utf-8 -*-
"""
确定性事实层（Deterministic Fact Layer）—— 统一调度
======================================================
LLM 输出是概率采样，对"记几分""几岁""几年""能开哪些车型"这类**有限枚举事实**
会偶发走样。本层把这些事实从生成环节剥离：命中即查表直答，模型不参与。

v2 重构要点（针对 200 题压力测试暴露的系统性缺陷）
------------------------------------------------
1. **不再"命中意图就套模板"**。旧实现先识别意图（ADD/INTERNSHIP/RECOVER…），
   再调对应模板；于是"增驾C6考什么科目"被增驾条件模板抢答、"哪些情形驾驶证
   会被注销"被恢复驾驶资格模板抢答。新实现改为：
   先识别**子诉求**（科目/罚则/清单/流程/期限/数值），每个处理器声明自己
   覆盖哪些诉求（covers），覆盖不到就不许作答，交回 RAG。
2. **处理器自证相关性**。每个处理器自己 re.search 主题词，不相关返回 None，
   避免"意图猜错 → 全盘答错"。
3. **包含关系推理**。持 A2 准驾范围已含 C6，问"A2增驾C6"应先指出"无需增驾"。
4. **分域拆分**。记分（fact_score）、考试（fact_exam）、车辆（fact_vehicle）、
   驾驶证扩展（fact_license_ext）独立成模块，主调度只负责排序与门控。
5. **输出表达统一**。中文数字量词统一为阿拉伯数字（十二个月→12个月），
   避免与用户/判分口径对不上。

数据来源（全部取自 data/knowledge_base 原文）
--------------------------------------------
- 《机动车驾驶证申领和使用规定》（公安部令第172号）
- 《道路交通安全违法行为记分管理办法》（公安部令第163号）
- 《机动车登记规定》（公安部令第164号）
- 《机动车强制报废标准规定》、公交管〔2022〕295号（检验周期现行口径）
"""

import re
from typing import Dict, List, Optional

from fact_common import (
    LICENSE_TABLE, ALIAS, MOTO_CODES, CAR_CODES, ONLY_BY_ADD,
    AGE_RULES, AGE_SPECIAL, FORBIDDEN_AT_63, FORBIDDEN_AT_70, DOWNGRADE_RULES,
    ADD_RULES, NO_YEAR_ADD, extract_codes, covers, covering_codes,
    norm, has, fmt_codes, num_style, detect_aspects, source, result,
)

import fact_admin
import fact_score
import fact_exam
import fact_vehicle
import fact_license_ext as ext

# ============================================================
# 固定事实文本
# ============================================================
PHYSICAL_LAW = "第十四条第二项"

_REVOKE_TABLE_TEXT = """| 情形 | 禁止申请期限 | 依据 |
|---|---|---|
| 造成交通事故后逃逸构成犯罪 | 终生禁驾 | 第十五条第三项 |
| 饮酒后或醉酒驾驶发生重大交通事故构成犯罪 | 终生禁驾 | 第十五条第四项 |
| 醉酒驾驶营运机动车被吊销 | 10年 | 第十五条第六项 |
| 醉酒驾驶机动车被吊销 | 5年 | 第十五条第五项 |
| 饮酒后驾驶营运机动车被吊销 | 5年 | 第十五条第五项 |
| 追逐竞驶、超员、超速、违规运输危化品构成犯罪被吊销 | 5年 | 第十五条第七项 |
| 其他重大交通事故构成犯罪被吊销 | 10年 | 第十五条第八项 |
| 驾驶许可被撤销 | 3年 | 第十五条第十项 |
| 其他情形被吊销 | 2年 | 第十五条第九项 |
| 无证驾驶发生同等以上责任事故致重伤或死亡 | 10年 | 第十五条第十一项 |
| 3年内有代替他人参加考试行为 | 3年 | 第十五条第十二项 |
| 3年内有吸食注射毒品，或解除强制隔离戒毒未满3年 | 3年 | 第十五条第二项 |"""

REVOKE_RULES: List[Dict] = [
    dict(need=["逃逸"], any_=["犯罪"], forever=True,
         desc="造成交通事故后逃逸构成犯罪的", law="第十五条第三项"),
    # 酒驾/醉驾 + 死亡：口语说"撞死人"时不会出现"重大事故"字样，
    # 若只按法条措辞匹配，会被下面的"醉酒→5年"规则抢答成 5 年（少答成终生禁驾）。
    dict(need=["重大交通事故", "重大事故", "死亡", "撞死", "致死"],
         any_=["饮酒", "醉酒"], forever=True,
         desc="饮酒后或者醉酒驾驶机动车发生重大交通事故构成犯罪的", law="第十五条第四项"),
    dict(need=["醉酒"], any_=["营运"], years=10,
         desc="醉酒驾驶营运机动车依法被吊销机动车驾驶证的", law="第十五条第六项"),
    dict(need=["饮酒"], any_=["营运"], years=5,
         desc="饮酒后驾驶营运机动车依法被吊销机动车驾驶证的", law="第十五条第五项"),
    dict(need=["醉酒"], any_=[], years=5,
         desc="醉酒驾驶机动车依法被吊销机动车驾驶证的", law="第十五条第五项"),
    dict(need=["追逐竞驶", "超员", "超速", "危险化学品", "危化品"], any_=["犯罪"], years=5,
         desc="驾驶机动车追逐竞驶、超员、超速、违反危险化学品安全管理规定运输危险化学品构成犯罪依法被吊销机动车驾驶证的",
         law="第十五条第七项"),
    dict(need=["重大交通事故", "重大事故"], any_=["犯罪"], years=10,
         desc="因其他违反交通管理法律法规的行为发生重大交通事故构成犯罪依法被吊销机动车驾驶证的",
         law="第十五条第八项"),
    dict(need=["撤销"], any_=[], years=3, desc="驾驶许可依法被撤销的", law="第十五条第十项"),
    dict(need=["代替"], any_=["考试"], years=3,
         desc="3年内有代替他人参加机动车驾驶人考试行为的", law="第十五条第十二项"),
    dict(need=["毒品", "吸毒"], any_=[], years=3,
         desc="3年内有吸食、注射毒品行为或者解除强制隔离戒毒措施未满3年的", law="第十五条第二项"),
]

INTERNSHIP = {
    "text": ("机动车驾驶人**初次取得汽车类准驾车型或者初次取得摩托车类准驾车型**后的12个月为实习期。"
             "同大类内增驾（如 C1→B2、B2→A1）不产生新的实习期；"
             "跨大类增驾（汽车类↔摩托车类）因属于初次取得该类准驾车型，仍有12个月实习期。"),
    "law": "第七十六条",
    "highway": ("驾驶人在实习期内驾驶机动车**上高速公路行驶**，应当由持相应或者包含其准驾车型驾驶证"
                "**3 年以上**的驾驶人陪同。其中，驾驶残疾人专用小型自动挡载客汽车（C5）的，"
                "可以由持有小型自动挡载客汽车以上准驾车型驾驶证的驾驶人陪同。"),
    "highway_law": "第七十七条第二款",
    "original_not_restricted": "在增加准驾车型后的实习期内，驾驶**原准驾车型**的机动车时，不受上述限制。",
    "restrictions": ("实习期内不得驾驶公共汽车、营运客车或者执行任务的警车、消防车、救护车、工程救险车"
                     "以及载有爆炸物品、易燃易爆化学物品、剧毒或者放射性等危险物品的机动车；"
                     "驾驶的机动车不得牵引挂车。"),
    "restrictions_law": "第七十七条第一款",
    "cancel": "实习期内发生的道路交通安全违法行为被记满12分的，注销其实习的准驾车型驾驶资格。",
    "cancel_law": "第八十条",
}

RECOVER = {
    "text": ("超过机动车驾驶证有效期1年以上未换证被注销，**未超过2年**的，"
             "参加道路交通安全法律、法规和相关知识考试（科目一）合格后，可以恢复驾驶资格。"),
    "law": "第七十九条第一款第八项、第四款",
    "note": "注意：这是**第七十九条**，不是第二十七条（第二十七条是重新申领新证的程序）。",
    # 「吊销」与「注销」是两套完全不同的制度，混答会把 5 年禁驾答成 2 年恢复。
    # 吊销类答案里必须显式排除这条，否则用户会把两者当成同一件事。
    "disambig": ("⚠️ **不要与「注销后恢复驾驶资格」混淆**："
                 "「注销未超过2年、考科目一恢复驾驶资格」只适用于**因超过有效期1年以上未换证被注销**"
                 "（第七十九条第八项）这类行政管理性注销；因违法被**吊销**的，禁驾期内没有任何"
                 "「恢复」通道，期满后只能按初次申领重新报考全部科目。"),
}

# 「吊销/禁驾后多久能再考」的口语说法：用户往往一个字都不提"吊销"，
# 只说"继续考""还能考""什么时候能考"，仅靠法条措辞设正则会把整类问句漏到 RAG。
_REVOKE_TOPIC_PENALTY = (
    r"(吊销|撤销|终生禁驾|终身禁驾|禁驾|重新取得|重新申请|重考|重领|"
    r"继续考|还能考|再考|恢复驾驶资格)"
)
_REVOKE_TOPIC_VIOLATION = (
    r"(醉驾|醉酒|酒驾|饮酒|逃逸|肇事|超速|超员|超载|吸毒|毒驾|无证|"
    r"代替考试|代考|死亡事故|重大事故|犯罪)"
)
# 违法词 + 报考动作（"醉驾后多久可以继续考驾驶证"）：中间允许 16 字间隔
_REVOKE_TOPIC_VIOLATION_EXAM = _REVOKE_TOPIC_VIOLATION + r".{0,16}(?:考|申领|拿证|领证|取得|申请)"
REVOKE_TOPIC = (_REVOKE_TOPIC_PENALTY + "|" + _REVOKE_TOPIC_VIOLATION_EXAM)

# 「考」类动作里与处罚无关的场景：无违法情形时一律不接，否则
# "科目二考完多久考科目三""学习驾驶证明过期怎么重考"会被吊销规则抢答。
_REVOKE_TOPIC_NEG = (
    r"(科目[一二三四]|学习驾驶证明|实习期|换证|审验|"
    r"预约|补考|考试费|考场|教练|学时)"
)


def revoke_topic_ok(text: str) -> bool:
    """吊销类问题的话题判定（供调度表与处理器共用，保证两者口径一致）。

    分两支：
    · 含违法情形（醉驾/逃逸/吸毒…）→ 必须同时出现报考动作（考/申领/取证）；
    · 不含违法情形 → 靠"吊销/禁驾/重考/继续考"等处罚措辞接住，
      但要排除科目考试、学习驾驶证明等纯业务流程问句。
    """
    if re.search(_REVOKE_TOPIC_VIOLATION, text):
        return bool(re.search(_REVOKE_TOPIC_VIOLATION_EXAM, text))
    if re.search(_REVOKE_TOPIC_NEG, text):
        return False
    return bool(re.search(_REVOKE_TOPIC_PENALTY, text))

# ============================================================
# 各处理器
# ============================================================
def q_contain(question: str) -> Optional[Dict]:
    """X 准驾车型能否驾驶 Y 车型（附件1 准予驾驶范围判断）。"""
    if has(question, ["什么", "哪些", "啥"]):
        return None
    codes = extract_codes(question)

    # "持C1能骑摩托车吗"——目标只有泛称"摩托车"，没有具体代号
    if len(codes) < 2 and "摩托" in question and not re.search(r"(电动自行车|电瓶车)", question):
        holder = codes[0] if codes else None
        if holder and holder in MOTO_CODES:
            return None
        if holder:
            return result(
                f"**不能。** 持 {holder}（{LICENSE_TABLE[holder]['name']}）**不能驾驶任何摩托车**。\n\n"
                "附件1：普通三轮摩托车（D）、普通二轮摩托车（E）、轻便摩托车（F）是独立的准驾车型，"
                f"{holder} 的「准予驾驶的其他准驾车型」为 "
                f"{'、'.join(LICENSE_TABLE[holder]['also']) or '无'}，不包含 D/E/F。\n\n"
                "如需骑行，应按第十七条申请增驾 D/E/F 之一：摩托车属可初次申领车型，"
                "增驾**没有持证年限要求**，只需本记分周期和申请前最近 1 个记分周期内没有记满 12 分记录。",
                [source("《机动车驾驶证申领和使用规定》附件1 准驾车型及代号",
                        f"{holder}：准予驾驶的其他准驾车型 {'、'.join(LICENSE_TABLE[holder]['also']) or '无'}")],
                "CONTAIN:MOTO_NO")

    if len(codes) < 2:
        return None
    holder, target = codes[0], codes[1]
    if holder == target:
        return None
    h_info = LICENSE_TABLE[holder]
    t_info = LICENSE_TABLE[target]
    also = h_info["also"]

    if covers(holder, target):
        text = (f"**能。** 持 {holder}（{h_info['name']}）可以驾驶 {target}（{t_info['name']}），**不需要另行增驾**。\n\n"
                f"依据附件1「准驾车型及代号」：{holder} 的「准予驾驶的其他准驾车型」包含 {target}"
                + ("（含通过其他准驾车型间接包含）" if target not in also else "") + "。\n\n"
                f"⚠️ 注意方向：包含关系通常是单向的。{holder} 可以驾驶 {target}，"
                f"但只持 {target} 不能反过来驾驶 {holder} 的车辆。"
                f"另外 {holder} 本身不等于 {target}——如果你要的是单独一本 {target} 证，仍需按第十七条办理增驾。")
    else:
        # 目标车型是否需要增驾、能否直接增驾，一并给出（避免用户追问）
        extra = ""
        if target in ADD_RULES:
            paths = "；".join(
                f"已持{'/'.join(pres)}满{years}年（最近{cyc}个记分周期无满分）"
                for pres, years, cyc, _ in ADD_RULES[target])
            extra = f"\n\n如需驾驶 {target}，应按第十七条申请增驾：{paths}。"
        elif target in NO_YEAR_ADD:
            extra = (f"\n\n如需驾驶 {target}，可直接申请增驾：{target} 属可初次申领车型，"
                     "增驾无持证年限要求，只需本记分周期和申请前最近 1 个记分周期内没有记满 12 分记录。")
        text = (f"**不能。** 持 {holder}（{h_info['name']}）不能驾驶 {target}（{t_info['name']}）。\n\n"
                f"依据附件1：{holder} 的「准予驾驶的其他准驾车型」为 "
                f"{'、'.join(also) if also else '无（该证没有准予驾驶的其他准驾车型）'}，不包含 {target}。"
                + extra)
    return result(text,
                  [source("《机动车驾驶证申领和使用规定》附件1 准驾车型及代号",
                          f"{h_info['name']} {holder}：准予驾驶的其他准驾车型 {'、'.join(also) or '无'}")],
                  f"CONTAIN:{holder}->{target}")


def q_scope(question: str) -> Optional[Dict]:
    codes = extract_codes(question)
    if not codes:
        return None
    code = codes[0]
    info = LICENSE_TABLE[code]
    lines = [f"**{code}（{info['name']}）**可驾驶：{info['vehicle']}。"]
    if info["also"]:
        lines.append(f"同时准予驾驶：{_fmt_codes(info['also'])}。")
    else:
        lines.append("该准驾车型没有其他准予驾驶的准驾车型。")
    if info["first_apply"]:
        lines.append(f"{code} 属于第十六条规定的可初次申领车型。")
    else:
        lines.append(f"⚠️ {code} **不能初次申领**，只能通过增驾取得（第十六条第一款初次申领清单不含 {code}）。")
    if code == "C1":
        lines.append("C1 不包含 C6（轻型牵引挂车）、不包含 D/E/F 摩托车、不包含 B1（中型客车）、不包含 B2（大型货车）。")
    if code == "A2":
        lines.append("A2 的准予驾驶范围包含 C6，但 A2 本身不等于 C6。")
    if code == "B2":
        lines.append("B2 准予驾驶 C1、C2、C3、C4、M，但**不包含 A1/A2/A3/B1**，也不包含摩托车 D/E/F。")
    return result("\n".join(lines),
                  [source("《机动车驾驶证申领和使用规定》附件1 准驾车型及代号",
                          f"{info['name']} {code}：准驾的车辆 {info['vehicle']}；"
                          f"准予驾驶的其他准驾车型 {'、'.join(info['also']) or '无'}")],
                  f"SCOPE:{code}")


def _fmt_codes(codes: List[str]) -> str:
    return "、".join(f"{c}（{LICENSE_TABLE[c]['name']}）" for c in codes if c in LICENSE_TABLE)


def q_age_apply(question: str) -> Optional[Dict]:
    codes = extract_codes(question)
    if not codes:
        return None
    code = codes[0]
    if code not in AGE_RULES:
        return None
    lo, hi, law = AGE_RULES[code]
    name = LICENSE_TABLE[code]["name"]
    cond = f"{lo}周岁以上，**没有年龄上限**" if hi is None else f"{lo}周岁以上、{hi}周岁以下"
    # 针对"有年龄上限吗"这类直接提问，先给一句结论
    ask_limit = bool(re.search(r"(上限|下限|有没有|是否有)", question))
    text = (f"**{'有' if hi else '没有'}年龄上限。** " if ask_limit else "") + \
           f"申请 **{code}（{name}）** 准驾车型的年龄条件是：{cond}。"
    if code in AGE_SPECIAL:
        text += "\n\n" + AGE_SPECIAL[code]
    if hi == 63:
        text += ("\n\n⚠️ 63 周岁是**申请**上限。已持该证的人可以驾驶到 63 周岁；"
                 "63 周岁以上仍需驾驶的，可在年满 63 周岁前 1 年内经三力测试"
                 "（记忆力、判断力、反应力）申请延长，延长期限最长不超过 3 年（第六十五条第一款）。")
    if hi == 70:
        text += ("\n\n⚠️ 70 周岁是**申请**上限。年满 70 周岁后不得继续驾驶该车型，"
                 "应按规定换领其他准驾车型驾驶证（第六十五条第二款）。")
    return result(text,
                  [source(f"《机动车驾驶证申领和使用规定》{law}",
                          f"申请{name}（{code}）准驾车型的，在{lo}周岁以上" + (f"、{hi}周岁以下" if hi else ""))],
                  f"AGE_APPLY:{code}")


def q_age70_apply(question: str) -> Optional[Dict]:
    if not re.search(r"70\s*(?:周?岁)", question):
        return None
    if not re.search(r"(申请|考|学|能开|可以开|哪些|什么)", question):
        return None
    return result(
        "**70 周岁以上可以申请以下 4 种准驾车型**（须通过记忆力、判断力、反应力等能力测试）：\n\n"
        "**C1（小型汽车）、C2（小型自动挡汽车）、C5（残疾人专用小型自动挡载客汽车）、F（轻便摩托车）**\n\n"
        "依据：第十四条第二项第9目。\n\n"
        "⚠️ 同时注意第六十五条第二款：70 周岁以上不得驾驶低速载货汽车（C3）、三轮汽车（C4）、"
        "轻型牵引挂车（C6）、普通三轮摩托车（D）、普通二轮摩托车（E）。\n"
        "持 D/E 驾驶证的应换领轻便摩托车（F）驾驶证；持 C6 驾驶证的应换领 C1 或 C2 驾驶证。",
        [source("《机动车驾驶证申领和使用规定》第十四条第二项第9目",
                "年龄在70周岁以上能够通过记忆力、判断力、反应力等能力测试的，可以申请小型汽车、小型自动挡汽车、"
                "残疾人专用小型自动挡载客汽车、轻便摩托车准驾车型的机动车驾驶证")],
        "AGE70_APPLY")


def q_study_cert(question: str) -> Optional[Dict]:
    # 守卫必须与调度表里的主题正则一致（原来只认"学习驾驶证明/学车证明"，
    # 导致"考试有效期是几年"主题命中却弃权，被后面的换证处理器抢答成驾驶证有效期）
    if not re.search(r"(学习驾驶证明|学车证明|考试有效期|考试.{0,2}有效期)", question):
        return None
    return result(
        "**学习驾驶证明的有效期为 3 年。**\n\n"
        "但有效期截止日期**不得超过申请年龄条件上限**。申请人应当在有效期内完成科目二和科目三考试；"
        "未在有效期内完成考试的，已考试合格的科目成绩作废。\n\n"
        "依据：第四十一条。\n\n"
        "补充：初次申请机动车驾驶证或者申请增加准驾车型的，科目一考试合格后，"
        "车辆管理所应当在 1 日内核发学习驾驶证明（第四十条）。",
        [source("《机动车驾驶证申领和使用规定》第四十一条",
                "学习驾驶证明的有效期为3年，但有效期截止日期不得超过申请年龄条件上限。"
                "申请人应当在有效期内完成科目二和科目三考试")],
        "STUDY_CERT")


def q_age_drive(question: str) -> Optional[Dict]:
    m = re.search(r"(\d{2})\s*(?:周?岁)", question)
    if not m:
        return None
    # 必须是在问"能不能开 / 怎么办"，否则"70岁以上需要每年体检吗"会被抢答成驾驶限制
    if not re.search(r"(能开|可以开|还能开|不能开|驾驶|开车|骑|上路|怎么办|如何处理|怎么处理|继续开|开)", question):
        return None
    age = int(m.group(1))
    codes = extract_codes(question)

    if not codes and "摩托" in question:
        if age >= 70:
            return result(
                "**普通三轮摩托车（D）、普通二轮摩托车（E）：70 周岁以上不得驾驶**，"
                "应换领轻便摩托车（F）驾驶证。\n\n"
                "**轻便摩托车（F）：70 周岁以上仍可驾驶**，但申请时须通过三力测试"
                "（第十四条第二项第9目）；持有 F 证的按规定提交身体条件证明即可。\n\n"
                "依据：第六十五条第二款。",
                [source("《机动车驾驶证申领和使用规定》第六十五条第二款",
                        "年龄在70周岁以上的，不得驾驶低速载货汽车、三轮汽车、轻型牵引挂车、"
                        "普通三轮摩托车、普通二轮摩托车")],
                f"AGE_DRIVE:{age}:MOTO")
        return result(
            f"**能。** {age} 周岁未达到 70 周岁，可以驾驶普通三轮摩托车（D）、"
            "普通二轮摩托车（E）、轻便摩托车（F）。\n\n"
            "注意：年满 70 周岁后不得驾驶 D/E，应换领轻便摩托车（F）驾驶证（第六十五条第二款）。",
            [source("《机动车驾驶证申领和使用规定》第六十五条第二款",
                    "年龄在70周岁以上的，不得驾驶……普通三轮摩托车、普通二轮摩托车")],
            f"AGE_DRIVE:{age}:MOTO")

    if not codes:
        if age >= 63:
            return result(
                "这要看你持的是哪个准驾车型，规则分两档：\n\n"
                "**63 周岁以上不得驾驶**：大型客车（A1）、重型牵引挂车（A2）、城市公交车（A3）、"
                "中型客车（B1）、大型货车（B2）、轮式专用机械车（M）、无轨电车（N）、有轨电车（P）。\n\n"
                "**70 周岁以上不得驾驶**：低速载货汽车（C3）、三轮汽车（C4）、轻型牵引挂车（C6）、"
                "普通三轮摩托车（D）、普通二轮摩托车（E）。\n\n"
                "**不受上述限制**：小型汽车（C1）、小型自动挡汽车（C2）、残疾人专用小型自动挡载客汽车（C5）、"
                "轻便摩托车（F）。\n\n"
                "请补充具体准驾车型，以便给出准确结论。",
                [source("《机动车驾驶证申领和使用规定》第六十五条",
                        "年龄在63周岁以上的，不得驾驶大型客车、重型牵引挂车、城市公交车、中型客车、大型货车、"
                        "轮式专用机械车、无轨电车和有轨电车")],
                f"AGE_DRIVE:{age}")
        return None

    code = codes[0]
    name = LICENSE_TABLE[code]["name"]
    lines: List[str] = []

    if code in FORBIDDEN_AT_63:
        if age >= 63:
            lines.append(f"**不能。** 年龄在 63 周岁以上的，不得驾驶 {code}（{name}）。")
            lines.append(f"应当：{DOWNGRADE_RULES.get(code, '换领小型汽车（C1）或小型自动挡汽车（C2）驾驶证')}。")
            lines.append("")
            lines.append("**唯一的延长途径**（第六十五条第一款）：")
            lines.append("1. 必须在**年满 63 周岁前 1 年内**提出申请；")
            lines.append("2. 须通过记忆力、判断力、反应力等能力测试（三力测试）；")
            lines.append("3. 向驾驶证核发地或核发地以外的车辆管理所申请；")
            lines.append("4. **延长期限最长不超过 3 年**（即最多驾驶到 66 周岁）。")
            lines.append("")
            lines.append("⚠️ 满 63 周岁后就不能再申请延长，必须卡在 62～63 岁这一年之内办理。")
        else:
            lines.append(f"**能。** {age} 周岁尚未达到 63 周岁，可以驾驶 {code}（{name}）。")
            lines.append("但需注意：年满 63 周岁后不得继续驾驶，须在 63 周岁前 1 年内经三力测试申请延长，最长 3 年。")
        law = "第六十五条第一款"
    elif code in FORBIDDEN_AT_70:
        if age >= 70:
            lines.append(f"**不能。** 年龄在 70 周岁以上的，不得驾驶 {code}（{name}）。")
            lines.append(f"应当：{DOWNGRADE_RULES.get(code, '换领轻便摩托车（F）驾驶证')}。")
            if code in ("D", "E"):
                lines.append("即：持 D/E 驾驶证的，70 周岁以上应换领轻便摩托车（F）驾驶证。")
            elif code == "C6":
                lines.append("即：持 C6 驾驶证的，70 周岁以上应换领 C1 或 C2 驾驶证。")
        else:
            lines.append(f"**能。** {age} 周岁尚未达到 70 周岁，可以驾驶 {code}（{name}）。")
            if code in ("D", "E"):
                lines.append("但需注意：年满 70 周岁后不得继续驾驶，应换领轻便摩托车（F）驾驶证。")
            elif code == "C6":
                lines.append("但需注意：年满 70 周岁后不得继续驾驶，应换领 C1 或 C2 驾驶证。")
        law = "第六十五条第二款"
    else:
        if code in ("C1", "C2", "C5", "F"):
            lines.append(f"**能。** {code}（{name}）不属于第六十五条的禁驾车型，{age} 周岁仍可驾驶。")
            if age >= 70:
                lines.append("70 周岁以上仍可驾驶，但须按规定提交身体条件证明；"
                             "申请时需通过三力测试（第十四条第二项第9目）。")
        else:
            lines.append(f"{code}（{name}）不在第六十五条的禁驾车型清单内，{age} 周岁可以驾驶。")
        law = "第六十五条"

    return result("\n".join(lines),
                  [source(f"《机动车驾驶证申领和使用规定》{law}", lines[0].replace("**", ""))],
                  f"AGE_DRIVE:{age}:{code}")


def q_add(question: str) -> Optional[Dict]:
    # 自证相关性：没有"增驾/增加"语义就不是增驾问题
    # （旧实现因未加此守卫，"F证有年龄上限吗"会被当成"增驾摩托车"作答）
    if not re.search(r"(增驾|增加)", question):
        return None
    codes = extract_codes(question)

    # 目标车型：优先取"增驾X"中的 X
    target = None
    m = re.search(r"(?:增驾|增加)(?:准驾车型)?\s*(?:为)?\s*([ABCDEFMNP]\s*[1-6]?)", question, re.I)
    if m:
        t = m.group(1).replace(" ", "").upper()
        if t in LICENSE_TABLE:
            target = t

    # 摩托泛称：未指明 D/E/F
    if "摩托" in question and not any(c in MOTO_CODES for c in codes) and not target:
        current = codes[0] if codes else None
        head = (f"持 **{current}（{LICENSE_TABLE[current]['name']}）** 可以增驾摩托车。" if current
                else "可以增驾摩托车。")
        return result(
            f"**{head}**\n\n"
            "普通三轮摩托车（D）、普通二轮摩托车（E）、轻便摩托车（F）都属于第十六条规定的"
            "**可初次申领车型**，所以持任何准驾车型驾驶证的人都可以直接申请增驾，"
            "**没有持证年限要求**（第十七条）。\n\n"
            "唯一条件：本记分周期和申请前最近 1 个记分周期内**没有记满 12 分记录**。\n\n"
            "⚠️ 常见错误：不存在\"B2/C1 不能增驾摩托车\"的规定，任何准驾车型都可以增驾 D/E/F。",
            [source("《机动车驾驶证申领和使用规定》第十六条、第十七条",
                    "普通三轮摩托车（D）、普通二轮摩托车（E）、轻便摩托车（F）属于可初次申领车型，"
                    "增驾无持证年限要求")],
            "ADD:MOTO")

    if not codes and not target:
        return None
    if not target:
        target = codes[-1]

    name = LICENSE_TABLE[target]["name"]
    current = codes[0] if len(codes) >= 2 else None

    # ── 包含关系优先：已有的证本来就准驾目标车型 → 不需要增驾 ──
    if current and current != target and covers(current, target):
        return result(
            f"**不需要增驾。** 持 {current}（{LICENSE_TABLE[current]['name']}）**本来就准予驾驶 "
            f"{target}（{name}）**。\n\n"
            f"依据附件1：{current} 的「准予驾驶的其他准驾车型」包含 {target}"
            + ("（通过中间准驾车型间接包含）" if target not in LICENSE_TABLE[current]["also"] else "") + "。\n\n"
            f"⚠️ 但要注意：{current} 准驾范围包含 {target}，不等于你持有一本 {target} 驾驶证。"
            f"如果你要的是单独签注 {target} 的驾驶证（例如为了从业、租车或异地使用），"
            f"仍需按第十七条申请增驾。",
            [source("《机动车驾驶证申领和使用规定》附件1 准驾车型及代号",
                    f"{LICENSE_TABLE[current]['name']} {current}：准予驾驶的其他准驾车型 "
                    f"{'、'.join(LICENSE_TABLE[current]['also'])}")],
            f"ADD:COVERED:{current}->{target}")

    if target in ADD_RULES:
        lines = [f"增驾 **{target}（{name}）** 的条件（第十七条）：", ""]
        for pres, years, cycles, law in ADD_RULES[target]:
            lines.append(f"- 已持 {_fmt_codes(pres)} 满 **{years} 年**以上 → 可增驾 {target}"
                         f"（且申请前最近连续 {cycles} 个记分周期内无记满12分记录）·{law}")
        lines.append("")
        lines.append("此外还需满足第十七条**一般条件**：本记分周期和申请前最近 1 个记分周期内没有记满 12 分记录。")
        if target == "A1":
            lines.append("")
            lines.append("⚠️ **C1 不能直接增驾 A1**。C1 不在 A1 的前置车型内，需先增驾至 B1 或 B2，再增驾 A1。"
                         "另外 A2 满 1 年也可增驾 A1。")
        if target == "A2":
            lines.append("")
            lines.append("⚠️ **C1 不能直接增驾 A2**。A2 的前置车型只有 B1、B2（满2年）或 A1（满1年），C1 不在其中。")
        if current:
            ok = any(current in pres for pres, _, _, _ in ADD_RULES[target])
            if not ok:
                lines.append("")
                lines.append(f"⚠️ 按你所述持有 **{current}**，**不能直接增驾 {target}**，需先增驾到上述前置车型之一。")
        if target == "C6":
            lines.append("")
            lines.append("补充：增驾 C6 只考**科目二 + 科目三安全文明驾驶常识**，不考科目一"
                         "（第三十条第三款）。")
        return result("\n".join(lines),
                      [source("《机动车驾驶证申领和使用规定》第十七条",
                              f"申请增加{name}（{target}）准驾车型的，应当在本记分周期和申请前最近1个记分周期内"
                              "没有记满12分记录")],
                      f"ADD:{target}")

    if target in NO_YEAR_ADD:
        text = (f"增驾 **{target}（{name}）** **没有持证年限要求**（第十七条）。\n\n"
                f"只需满足一般条件：**本记分周期和申请前最近 1 个记分周期内没有记满 12 分记录**。\n\n"
                f"因为 {target} 本身就属于第十六条规定的可初次申领车型，"
                f"所以已持任何准驾车型驾驶证的人都可以直接申请增驾。")
        return result(text,
                      [source("《机动车驾驶证申领和使用规定》第十六条、第十七条",
                              f"{name}（{target}）属于可初次申领车型，增驾无持证年限要求")],
                      f"ADD:{target}")
    return None


def q_first_apply(question: str) -> Optional[Dict]:
    if not re.search(r"(初次申领|第一次申领|直接考|直接申请|能直接|可以直接|能不能初次)", question):
        return None
    codes = extract_codes(question)
    if not codes:
        return None
    code = codes[0]
    if "增驾" in question and len(codes) >= 2:
        code = codes[-1]
    name = LICENSE_TABLE[code]["name"]

    if code in ONLY_BY_ADD:
        text = (f"**{code}（{name}）不能初次申领**，只能通过增驾取得。\n\n"
                f"依据第十六条第一款：初次申领可申请的准驾车型为 A3、B2、C1、C2、C3、C4、C5、D、E、F、M、N、P，"
                f"**不含 {code}**。\n\n"
                "只能通过增驾取得的车型共 4 种：A1（大型客车）、A2（重型牵引挂车）、B1（中型客车）、C6（轻型牵引挂车）。")
        if code == "C6":
            text += ("\n\n增驾 C6 的条件：已取得 C1 或 C2 驾驶证满 1 年以上，"
                     "且本记分周期和申请前最近 1 个记分周期内无记满 12 分记录（第十七条第一项）。"
                     "\n考试科目只有**科目二 + 科目三安全文明驾驶常识**（第三十条第三款）。")
        return result(text,
                      [source("《机动车驾驶证申领和使用规定》第十六条",
                              "初次申领机动车驾驶证的，可以申请准驾车型为城市公交车（A3）、大型货车（B2）、"
                              "小型汽车（C1）……有轨电车（P）")],
                      f"FIRST_APPLY:{code}")

    text = (f"**{code}（{name}）可以初次申领。**\n\n"
            "它属于第十六条第一款列举的初次申领车型清单（A3、B2、C1、C2、C3、C4、C5、D、E、F、M、N、P）。\n\n"
            "所以既可以初次申领，也可以由已持其他准驾车型驾驶证的人增驾取得。")
    return result(text,
                  [source("《机动车驾驶证申领和使用规定》第十六条",
                          f"{name}（{code}）属于初次申领可申请的准驾车型")],
                  f"FIRST_APPLY:{code}")


def q_revoke(question: str) -> Optional[Dict]:
    """吊销/禁驾后的重新申领期限。

    与「注销后恢复驾驶资格」是两套制度，答案里必须显式排除后者，
    否则用户会把"5 年禁驾后重新报考"误解成"2 年内考科目一就能恢复"。
    """
    # 自证相关性：调度表的主题正则只做粗筛，这里再按同一口径确认一次，
    # 避免"科目二考完多久考科目三"这类问句被误接。
    if not revoke_topic_ok(question):
        return None

    q = norm(question)
    for rule in REVOKE_RULES:
        if not has(q, rule["need"]):
            continue
        if rule.get("any_") and not has(q, rule["any_"]):
            continue
        if rule.get("forever"):
            text = (f"**终生不得重新取得机动车驾驶证（终生禁驾）。**\n\n"
                    f"情形：{rule['desc']}。\n\n"
                    f"依据：《机动车驾驶证申领和使用规定》{rule['law']}。\n\n"
                    "终生禁驾没有期满后重新申领的通道。")
        else:
            text = (f"**{rule['years']} 年内不得申请机动车驾驶证。**\n\n"
                    f"情形：{rule['desc']}。\n\n"
                    f"依据：《机动车驾驶证申领和使用规定》{rule['law']}。\n\n"
                    "禁驾期满后按**初次申领**程序重新报考，须重新参加科目一至科目四全部考试，"
                    "取得的是新驾驶证。")
            if rule["need"] == ["醉酒"] and not rule.get("any_"):
                text += ("\n\n对照：**醉酒驾驶营运机动车**为 **10 年**；"
                         "**饮酒后或者醉酒驾驶发生重大交通事故构成犯罪**的为**终生禁驾**。")
        text += "\n\n" + RECOVER["disambig"]
        return result(text,
                      [source(f"《机动车驾驶证申领和使用规定》{rule['law']}", rule["desc"])],
                      f"REVOKE:{rule['years'] if not rule.get('forever') else 'FOREVER'}")

    if "吊销" in q:
        return result(
            "吊销后重新申请的期限**取决于吊销原因，不能统一回答\"2年\"**：\n\n"
            + _REVOKE_TABLE_TEXT +
            "\n\n期满后按**初次申领**程序重新报考全部科目，不存在「考科目一恢复驾驶资格」的通道。\n\n"
            + RECOVER["disambig"] +
            "\n\n请补充具体的违法情形，我可以给出准确期限。",
            [source("《机动车驾驶证申领和使用规定》第十五条",
                    "醉酒驾驶机动车或者饮酒后驾驶营运机动车依法被吊销机动车驾驶证未满5年的……")],
            "REVOKE:TABLE")
    return None


def q_internship(question: str) -> Optional[Dict]:
    """实习期：定义 / 高速陪同 / 限制 / 标志 / 是否换证 / 记满12分。"""
    q = question
    # 自证相关性：无"实习"二字一律不答
    if "实习" not in q:
        return None
    # 子问题优先分派（避免"问标志答定义"）
    sub = ext.q_internship_extra(q)
    if sub:
        return sub

    src = [source("《机动车驾驶证申领和使用规定》第七十六条",
                  "机动车驾驶人初次取得汽车类准驾车型或者初次取得摩托车类准驾车型后的12个月为实习期")]

    if "高速" in q or "快速路" in q:
        text = ("**实习期内驾驶机动车上高速公路，必须由他人陪同。**\n\n"
                f"{INTERNSHIP['highway']}\n\n"
                f"依据：{INTERNSHIP['highway_law']}。\n\n"
                f"{INTERNSHIP['original_not_restricted']}\n\n"
                "另外，实习期内还不得驾驶公共汽车、营运客车、执行任务的警车/消防车/救护车/工程救险车，"
                "不得驾驶载有爆炸物品、易燃易爆化学物品、剧毒或放射性等危险物品的机动车，"
                "驾驶的机动车不得牵引挂车（第七十七条第一款）。")
        src.append(source("《机动车驾驶证申领和使用规定》第七十七条第二款",
                          "驾驶人在实习期内驾驶机动车上高速公路行驶，应当由持相应或者包含其准驾车型驾驶证"
                          "3年以上的驾驶人陪同"))
        return result(text, src, "INTERNSHIP:HIGHWAY")

    text = (f"{INTERNSHIP['text']}\n\n依据：{INTERNSHIP['law']}。\n\n"
            f"**高速限制**：{INTERNSHIP['highway']}（{INTERNSHIP['highway_law']}）\n\n"
            f"**其他限制**：{INTERNSHIP['restrictions']}（{INTERNSHIP['restrictions_law']}）\n\n"
            f"**实习标志**：实习期内驾驶机动车的，应当在车身后部粘贴或者悬挂统一式样的实习标志"
            f"（第七十六条第二款）。\n\n"
            f"实习期内记满 12 分的，{INTERNSHIP['cancel']}（{INTERNSHIP['cancel_law']}）")
    return result(text, src, "INTERNSHIP")


def q_recover(question: str) -> Optional[Dict]:
    if not re.search(r"(恢复驾驶资格|恢复驾照|恢复驾驶)", question):
        return None
    text = f"{RECOVER['text']}\n\n依据：{RECOVER['law']}。\n\n{RECOVER['note']}"
    return result(text,
                  [source("《机动车驾驶证申领和使用规定》第七十九条",
                          "有第一款第八项情形被注销机动车驾驶证未超过2年的，机动车驾驶人参加道路交通安全法律、"
                          "法规和相关知识考试合格后，可以恢复驾驶资格")],
                  "RECOVER")


def q_physical(question: str) -> Optional[Dict]:
    q = question

    # 色弱：与色盲分开判定（旧实现把色弱当色盲答，结论过严）
    if "色弱" in q:
        return ext.q_color_weak(q)
    # 肢体：第5/6/8目决策表（旧实现完全没有，交由模型裸答 → C5 常被答成 C1）
    if re.search(r"(上肢|下肢|手指|手掌|拇指|肢体|截肢|手臂|胳膊|躯干|颈部)", q):
        r = ext.q_limb(q)
        if r:
            return r

    if has(q, ["色盲", "辨色"]):
        return result(
            "**红绿色盲不能申请任何准驾车型的机动车驾驶证。**\n\n"
            "第十四条第二项第3目原文只有一句：\"辨色力：无红绿色盲。\""
            "该项**没有按车型分列**，对 A1 至 P 全部准驾车型一律适用。\n\n"
            "⚠️ 常见错误：认为\"只有大型车要求辨色力\"或\"色盲可以申请 C1/C2\"——这两种说法都是错的。\n\n"
            "（注意与听力条款区分：听力允许佩戴助听设备后申请 C1/C2，但辨色力没有这类放宽。）",
            [source("《机动车驾驶证申领和使用规定》第十四条第二项第3目", "辨色力：无红绿色盲")],
            "PHYSICAL:color")

    if has(q, ["单眼", "一只眼", "一只眼睛"]):
        return result(
            "**符合条件可以申请，共 5 种车型。**\n\n"
            "单眼视力障碍，优眼裸视力或者矫正视力达到对数视力表 **5.0 以上**，"
            "且水平视野达到 **150 度**的，可以申请：\n\n"
            "**C1（小型汽车）、C2（小型自动挡汽车）、C3（低速载货汽车）、C4（三轮汽车）、"
            "C5（残疾人专用小型自动挡载客汽车）**\n\n"
            "依据：第十四条第二项第2目。\n\n"
            "⚠️ 两个常见错误：① 只列 C1/C2，漏掉 C3/C4/C5；② 断言\"一只眼睛看不见一律不能申请\"——都不对。",
            [source("《机动车驾驶证申领和使用规定》第十四条第二项第2目",
                    "单眼视力障碍，优眼裸视力或者矫正视力达到对数视力表5.0以上，且水平视野达到150度的，"
                    "可以申请小型汽车（C1）、小型自动挡汽车（C2）、低速载货汽车（C3）、三轮汽车（C4）、"
                    "残疾人专用小型自动挡载客汽车（C5）")],
            "PHYSICAL:monocular")

    if has(q, ["视力", "裸视", "矫正"]):
        codes = extract_codes(q)
        need_50 = ("A1", "A2", "A3", "B1", "B2", "N", "P")
        if codes and codes[0] in need_50:
            body = (f"申请 **{codes[0]}（{LICENSE_TABLE[codes[0]]['name']}）** 需要两眼裸视力或者矫正视力"
                    "达到**对数视力表 5.0 以上**（第十四条第二项第2目）。")
        elif codes:
            body = (f"申请 **{codes[0]}（{LICENSE_TABLE[codes[0]]['name']}）** 需要两眼裸视力或者矫正视力"
                    "达到**对数视力表 4.9 以上**（第十四条第二项第2目）。")
        else:
            body = ("视力要求分两档（第十四条第二项第2目）：\n\n"
                    "- 申请 **A1、A2、A3、B1、B2、N、P**：两眼裸视力或者矫正视力达到对数视力表 **5.0 以上**；\n"
                    "- 申请**其他准驾车型**：两眼裸视力或者矫正视力达到对数视力表 **4.9 以上**。\n\n"
                    "单眼视力障碍的另有放宽条款（可申请 C1/C2/C3/C4/C5，需优眼 5.0 以上且水平视野 150 度）。")
        return result(body,
                      [source("《机动车驾驶证申领和使用规定》第十四条第二项第2目",
                              "申请大型客车、重型牵引挂车、城市公交车、中型客车、大型货车、无轨电车或者有轨电车"
                              "准驾车型的，两眼裸视力或者矫正视力达到对数视力表5.0以上。申请其他准驾车型的，"
                              "两眼裸视力或者矫正视力达到对数视力表4.9以上")],
                      "PHYSICAL:vision")

    if has(q, ["身高"]):
        codes = extract_codes(q)
        m = re.search(r"(\d{3})\s*(?:厘米|cm|公分)", q) or re.search(r"身高\D{0,3}(\d{3})", q)
        h = int(m.group(1)) if m else None
        need = None
        if codes:
            need = 155 if codes[0] in ("A1", "A2", "A3", "B2", "N") else (150 if codes[0] == "B1" else None)
        if codes and need is None:
            return result(
                f"**{codes[0]}（{LICENSE_TABLE[codes[0]]['name']}）没有身高要求。**\n\n"
                "第十四条第二项第1目只对 A1、A2、A3、B2、N（155 厘米以上）和 B1（150 厘米以上）作了规定，"
                "其他准驾车型法规未设身高门槛。",
                [source("《机动车驾驶证申领和使用规定》第十四条第二项第1目",
                        "申请大型客车（A1）、重型牵引挂车（A2）、城市公交车（A3）、大型货车（B2）、"
                        "无轨电车（N）准驾车型的，身高为155厘米以上。申请中型客车（B1）准驾车型的，身高为150厘米以上")],
                "PHYSICAL:height_none")
        if codes and need and h is not None:
            ok = h >= need
            return result(
                f"**{'能。' if ok else '不能。'}** 申请 {codes[0]}（{LICENSE_TABLE[codes[0]]['name']}）"
                f"要求身高 **{need} 厘米以上**，你提供的身高是 {h} 厘米，"
                f"{'刚好达到标准' if h == need else ('高于标准' if h > need else '低于标准')}。\n\n"
                "依据：第十四条第二项第1目。\n\n"
                "⚠️ 注意：身高只是身体条件之一，还需同时满足视力、辨色力、听力、上肢、下肢等全部条件。",
                [source("《机动车驾驶证申领和使用规定》第十四条第二项第1目",
                        f"申请{LICENSE_TABLE[codes[0]]['name']}（{codes[0]}）准驾车型的，身高为{need}厘米以上")],
                "PHYSICAL:height_judge")
        return result(
            "**身高要求（第十四条第二项第1目）：**\n\n"
            "- 申请 A1（大型客车）、A2（重型牵引挂车）、A3（城市公交车）、B2（大型货车）、N（无轨电车）：**155 厘米以上**\n"
            "- 申请 B1（中型客车）：**150 厘米以上**\n"
            "- 其余准驾车型：法规未作身高要求",
            [source("《机动车驾驶证申领和使用规定》第十四条第二项第1目",
                    "申请大型客车（A1）、重型牵引挂车（A2）、城市公交车（A3）、大型货车（B2）、无轨电车（N）"
                    "准驾车型的，身高为155厘米以上。申请中型客车（B1）准驾车型的，身高为150厘米以上")],
            "PHYSICAL:height")

    if has(q, ["听力", "耳聋", "助听"]):
        return result(
            "**听力条件（第十四条第二项第4目）：**\n\n"
            "标准：两耳分别距音叉 50 厘米能辨别声源方向。\n\n"
            "有听力障碍但**佩戴助听设备**能够达到以上条件的，可以申请 **C1（小型汽车）、C2（小型自动挡汽车）** 准驾车型。\n\n"
            "⚠️ 注意：听力放宽只限 C1/C2，不能扩大到其他车型；且这与辨色力不同——辨色力没有任何放宽。",
            [source("《机动车驾驶证申领和使用规定》第十四条第二项第4目",
                    "有听力障碍但佩戴助听设备能够达到以上条件的，可以申请小型汽车、小型自动挡汽车准驾车型的机动车驾驶证")],
            "PHYSICAL:hearing")
    return None


# ============================================================
# 调度表
# ------------------------------------------------------------
# 每一项：(处理器, 主题正则, 覆盖的诉求集合)
#
# 为什么要"主题正则"而不是"意图优先级"？
# 旧实现按意图排序，一旦意图判错（"F证有年龄上限吗"被判成 ADD），
# 整条链路全错。现在每个处理器自带主题正则**自证相关性**，
# 调度器只把真正属于它的问题交给它；顺序只用于"同题多解"时取更具体者。
#
# 主题正则宁可窄不可宽：窄了就是漏答（走 RAG 兜底），
# 宽了就是答非所问（用户看不出错）——后者危害大得多。
# ============================================================
QUALIFY = {"QUALIFY"}
SCORE_COV = {"SCORE", "VALUE", "PENALTY"}

_HANDLERS = [
    # ── 1. 驾驶证状态异常（注销/吊销/暂扣期间驾驶）──
    (ext.q_status_drive,
     r"(?:注销|吊销|暂扣|扣留|超期|过期|失效).{0,12}(?:开车|驾驶|上路|骑车|算|属于)",
     {"QUALIFY", "PENALTY"}),
    # ── 2. 不得申请：第十五条（全车型）+ 第十八条（AB 类）──
    (ext.q_no_ab,
     r"(?:人员死亡|死亡|同等责任|主要责任|全部责任|醉酒|醉驾|酒驾|饮酒|吸毒|毒品|戒毒|社区康复|"
     r"追逐竞驶|逃逸|无证驾驶|重伤|再次饮酒|二次酒驾).{0,26}(?:申请|考|增驾|拿|领)",
     QUALIFY | {"PENALTY"}),
    # ── 3. 注销情形清单 ──
    (ext.q_cancel_list, r"注销", {"LIST"}),
    # ── 4. 记分周期 ──
    (ext.q_period, r"记分周期", {"DURATION", "DEFINE"}),
    # ── 5. 记满 12 分处理 ──
    (ext.q_full_score, r"(?:记满|满)\s*12\s*分", {"PROCEDURE", "SUBJECT", "PENALTY"}),
    # ── 6. 吊销后重考期限（口语"继续考/还能考/什么时候能考"也必须接住）──
    # 必须早于记分处理器：醉驾致死/逃逸致罪这类问句若被记分模板抢答，
    # 会把"终生禁驾"答成"5年内不得重新取得"。
    (q_revoke, REVOKE_TOPIC,
     {"DURATION", "QUALIFY"}),
    # ── 7. 记分（含醉驾/学法减分/超员超载超速矩阵）──
    (fact_score.q_score,
     r"(?:记|扣)\s*几?\s*分|几分|多少分|记分|扣分|超速|超员|超载|超重|超限|学法减分|醉.{0,2}驾|饮酒",
     SCORE_COV | QUALIFY),
    # ── 8. 身体条件（色盲/色弱/视力/身高/听力/肢体）──
    (q_physical,
     r"(色盲|色弱|辨色|视力|裸视|眼睛|单眼|身高|听力|耳聋|助听|上肢|下肢|手指|手掌|拇指|肢体|残疾|截肢)",
     {"QUALIFY", "VALUE"}),
    # ── 9. 体检 / 身体条件证明提交周期 ──
    (ext.q_physical_report, r"(体检|身体条件证明|身体检查|提交身体)", {"DURATION", "PROCEDURE"}),
    # ── 10. 实习期（含标志/是否换证/高速/限制）──
    (q_internship, r"实习",
     {"QUALIFY", "DEFINE", "RESTRICT", "DURATION", "PROCEDURE", "SIGN"}),
    # ── 11. 考试科目与合格标准 ──
    (fact_exam.q_exam,
     r"(科目[一二三四]|考几科|几个科目|及格|合格标准|考试内容|考试科目|考什么科目|"
     r"考试不合格|重新预约|增驾.{0,6}考试|考试有效期)",
     {"SUBJECT", "VALUE", "DURATION"}),
    # ── 12. 车辆业务（检验/免检/报废/转让/核载/车辆类别）──
    (fact_vehicle.q_vehicle,
     r"(年检|检验|检测|上线|免检|报废|二手车|过户|转让|电动|电摩|核载|座|检一次|几年检)",
     {"DURATION", "LIST", "PENALTY", "PROCEDURE", "QUALIFY", "VALUE", "SIGN"}),
    # ── 12.5 行政业务：审验 / 备案 / 身体状况变化 / 校车 / 境外军队换证 ──
    (fact_admin.q_review, r"审验", {"DURATION", "PROCEDURE"}),
    (fact_admin.q_overdue_review, r"审验", {"PENALTY"}),
    (fact_admin.q_school_bus, r"校车", {"DURATION", "PROCEDURE"}),
    (fact_admin.q_info_change,
     r"(信息|地址|电话|联系方式|住址|姓名).{0,8}(变更|变化|改了|换了)", {"PROCEDURE"}),
    (fact_admin.q_body_change,
     r"(身体条件|身体状况|身体).{0,10}(变化|不适合|不符合|下降)", {"PROCEDURE", "QUALIFY"}),
    (fact_admin.q_foreign_military, r"(境外|国外|军队|部队|武警|军警)", {"PROCEDURE", "QUALIFY"}),
    (fact_admin.q_disease,
     r"(癫痫|心脏病|精神病|痴呆|眩晕|帕金森|震颤麻痹|癔病|美尼尔|器质性)", QUALIFY),
    (fact_admin.q_forever, r"(终生|终身)", {"LIST", "QUALIFY"}),
    (fact_admin.q_vehicle_reg,
     r"(抵押|变更登记|颜色|发动机|车架|改装|报废|注销登记|号牌|牌照|遗失|补领|电子|检验标志|年检标志)",
     {"PROCEDURE", "QUALIFY", "LIST", "SIGN"}),
    (fact_admin.q_locked, r"锁定", {"DEFINE", "QUALIFY"}),
    # ── 13. 学习驾驶证明（必须早于换证，二者都含"有效期"）──
    (q_study_cert, r"(学习驾驶证明|学车证明|考试有效期)",
     {"DURATION", "VALIDITY", "PROCEDURE"}),
    # ── 14. 换证 / 有效期 / 核发地 / 延期 ──
    (ext.q_renew, r"(换证|换领|有效期|到期|过期|超期|失效|核发地|转入|迁入|延期)",
     {"DURATION", "VALIDITY", "PROCEDURE", "QUALIFY"}),
    # ── 15. 恢复驾驶资格 / 注销后可恢复期限 ──
    (ext.q_recover_window, r"恢复", {"QUALIFY", "PROCEDURE", "DURATION"}),
    (q_recover, r"(恢复驾驶资格|恢复驾照|恢复驾驶)", {"QUALIFY", "PROCEDURE"}),
    # ── 16. 70 周岁以上可申请车型（必须早于年龄驾驶限制）──
    (q_age70_apply, r"70\s*(?:周?岁)", QUALIFY | {"VALUE", "PROCEDURE"}),
    # ── 17. 年龄驾驶限制 ──
    (q_age_drive, r"\d{2}\s*(?:周?岁)", QUALIFY | {"PROCEDURE"}),
    # ── 18. 增驾条件 ──
    (q_add, r"(增驾|增加)", {"QUALIFY", "DURATION", "VALUE"}),
    # ── 19. 初次申领 ──
    (q_first_apply, r"(初次申领|第一次申领|直接考|直接申请|能直接|可以直接)", QUALIFY),
    # ── 20. 包含关系（能不能开某个车型）──
    (q_contain, r"(能开|可以开|能否开|能驾驶|可以驾驶|能不能开|能骑)", QUALIFY),
    # ── 21. 准驾范围 ──
    (q_scope, r"(准驾|开什么车|能开什么|可以开什么|能开哪些|可以开哪些|啥车)", QUALIFY | {"LIST"}),
    # ── 22. 申请年龄 ──
    (q_age_apply, r"(年龄|多大|多少岁|几岁|周岁|\d{2}\s*岁)", QUALIFY | {"VALUE", "DURATION"}),
    # ── 23. 罚则兜底（知识库未收录具体罚则时的通用条款）──
    (ext.q_penalty_fallback, r"(处罚|罚款|怎么罚|拘留|扣车)", {"PENALTY"}),
]

_MULTI_DEMAND = ("并且", "还有", "另外", "同时", "顺便")


def _finalize(r: Dict) -> Dict:
    """统一出口：数字表达规范化（十二个月→12个月等）。"""
    r["answer"] = num_style(r["answer"])
    return r


def _candidates(q: str, aspects):
    """按主题正则筛选候选处理器，再做诉求门控。"""
    for fn, topic, cov in _HANDLERS:
        if not re.search(topic, q):
            continue
        if aspects and not (aspects <= cov):
            continue
        yield fn, cov


def lookup(question: str, max_len: int = 44) -> Optional[Dict]:
    """
    确定性查表。命中返回 {'answer','sources','fact'}，否则 None（交回 RAG）。

    三道闸门（v2 核心，专治"答非所问"）：
    1. 主题闸门 —— 处理器必须自带主题正则命中；
    2. 诉求闸门 —— 用户的子诉求（科目/罚则/清单/流程/期限/数值）必须被处理器覆盖；
    3. 复杂度闸门 —— 过长、多问句、多诉求（并且/另外/同时）不直答。
    """
    q = question.strip()
    if not q:
        return None
    if len(q) > max_len:
        return None
    if q.count("？") + q.count("?") > 1:
        return None
    if any(w in q for w in _MULTI_DEMAND):
        return None

    aspects = detect_aspects(q)
    for fn, cov in _candidates(q, aspects):
        try:
            r = fn(q)
        except Exception as e:
            print(f"[FactTable] {fn.__name__} 查表出错: {e}")
            continue
        if r:
            r.setdefault("covers", frozenset(cov))
            r["match"] = fn.__name__
            return _finalize(r)
    return None


def audit(question: str) -> List[Dict]:
    """
    体检工具：列出该问题命中了哪些处理器、各自是否给出答案。

    用途——发现"主题命中但处理器弃权"（guard mismatch）：调度表的主题正则说
    "这题归你"，处理器内部守卫却说"不是我的"，于是问题被后面的处理器抢答或掉进 RAG。
    这类静默错配只能靠对账发现，属于机制性缺陷，不是单题错误。
    """
    q = question.strip()
    aspects = detect_aspects(q)
    rows = []
    for fn, topic, cov in _HANDLERS:
        if not re.search(topic, q):
            continue
        rows.append({"handler": fn.__name__, "topic_hit": True,
                     "aspect_ok": (not aspects) or bool(aspects <= cov),
                     "answered": False, "fact": None})
    # 逐条试跑（按调度顺序），记录第一个给出答案的处理器
    hit = lookup(q)
    for row in rows:
        fn = dict((f.__name__, f) for f, _, _ in _HANDLERS)[row["handler"]]
        try:
            r = fn(q)
        except Exception as e:
            row["error"] = str(e)
            r = None
        row["answered"] = bool(r)
        row["fact"] = r.get("fact") if r else None
    return {"question": q, "aspects": sorted(aspects), "rows": rows,
            "winner": (hit or {}).get("fact"), "winner_match": (hit or {}).get("match")}


def build_fact_hint(question: str) -> Optional[str]:
    """
    为 RAG 提示词生成"权威事实"片段。
    与 lookup 的区别：不限长度（长问题更需要依据），但同样过主题+诉求闸门——
    诉求不匹配时宁可不注入，也不给模型一条错位的"权威事实"。
    """
    q = question.strip()
    aspects = detect_aspects(q)
    for fn, cov in _candidates(q, aspects):
        try:
            r = fn(q)
        except Exception:
            continue
        if r:
            txt = num_style(r["answer"].replace("**", ""))
            return (f"【系统权威事实（已逐条核对法规原文，优先级高于检索片段，不得违背）】\n"
                    f"{txt}\n【权威事实结束】")
    return None
