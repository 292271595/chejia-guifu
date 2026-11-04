# -*- coding: utf-8 -*-
"""向术语对照表追加「违法处罚」类口语映射。

背景：原有 50 条只覆盖"办事"口径（上牌、过户、转籍、换证…），
"违法处罚"这一整类没进表。后果是"闯红灯怎么处罚"检索不到记分条——
163号令的原文写的是"驾驶机动车不按交通信号灯指示通行"，和"闯红灯"三个字
没有任何字面交集，向量相似度也低。用户问的是口语，法规写的是术语，
中间缺一层翻译，检索就退化成猜。

每条都逐字取自 163号令原文，分值经条文核对，不凭记忆填。
"""
import io
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "glossary_colloquial.json")

NEW = [
    dict(id="penalty-run-red-light", group="违法处罚",
         colloquial=["闯红灯", "冲红灯", "闯信号灯", "红灯还过", "不按信号灯走"],
         official="驾驶机动车不按交通信号灯指示通行",
         search_terms=["不按交通信号灯指示通行", "违反道路交通信号灯通行", "一次记6分"],
         statute="《道路交通安全违法行为记分管理办法》第十条第八项",
         note="记6分；罚款按《道路交通安全法》第九十条，警告或20元以上200元以下"),
    dict(id="penalty-plate-cover", group="违法处罚",
         colloquial=["遮挡号牌", "挡号牌", "遮牌照", "污损号牌", "故意遮挡车牌", "糊车牌"],
         official="驾驶未悬挂机动车号牌或者故意遮挡、污损机动车号牌的机动车上道路行驶",
         search_terms=["故意遮挡、污损机动车号牌", "未悬挂机动车号牌", "一次记9分"],
         statute="记分办法第九条第四项；《道路交通安全法》第九十条、第九十五条第二款",
         note="记9分，罚款200元"),
    dict(id="penalty-seatbelt", group="违法处罚",
         colloquial=["不系安全带", "没系安全带", "未系安全带", "不戴安全带"],
         official="驾驶机动车在道路上行驶时，机动车驾驶人未按规定系安全带",
         search_terms=["未按规定系安全带", "一次记1分"],
         statute="记分办法第十二条第九项",
         note="驾驶人记1分；乘车人不记分，罚款按各地规定"),
    dict(id="penalty-phone", group="违法处罚",
         colloquial=["开车打电话", "开车玩手机", "开车接电话", "打手持电话", "边开车边刷手机"],
         official="驾驶机动车有拨打、接听手持电话等妨碍安全驾驶的行为",
         search_terms=["拨打、接听手持电话", "妨碍安全驾驶", "一次记3分"],
         statute="记分办法第十一条第六项",
         note="记3分，罚款按《道路交通安全法》第九十条"),
    dict(id="penalty-emergency-lane", group="违法处罚",
         colloquial=["占用应急车道", "走应急车道", "应急车道", "堵车走应急道"],
         official="驾驶机动车在高速公路或者城市快速路上违法占用应急车道行驶",
         search_terms=["违法占用应急车道行驶", "一次记6分"],
         statute="记分办法第十条第十一项",
         note="记6分，罚款200元；仅限高速和城市快速路"),
    dict(id="penalty-no-yield-pedestrian", group="违法处罚",
         colloquial=["不礼让行人", "不让行人", "斑马线不让", "不避让行人", "没让行人"],
         official="驾驶机动车行经人行横道不按规定减速、停车、避让行人",
         search_terms=["行经人行横道", "不按规定减速、停车、避让行人", "一次记3分"],
         statute="记分办法第十一条第七项",
         note="记3分，罚款按《道路交通安全法》第九十条"),
    dict(id="penalty-line-cross", group="违法处罚",
         colloquial=["压实线", "压线", "压黄线", "压白实线", "违反禁止标线", "越实线"],
         official="驾驶机动车违反禁令标志、禁止标线指示",
         search_terms=["违反禁令标志、禁止标线指示", "一次记1分"],
         statute="记分办法第十二条第四项",
         note="记1分，罚款按《道路交通安全法》第九十条"),
    dict(id="penalty-fatigue", group="违法处罚",
         colloquial=["疲劳驾驶", "开太久", "连续开车", "开车打瞌睡", "疲劳开车"],
         official="连续驾驶中型以上载客汽车、危险物品运输车辆超过4小时未停车休息或者停车休息时间少于20分钟",
         search_terms=["连续驾驶超过4小时", "停车休息时间少于20分钟", "一次记9分"],
         statute="记分办法第九条第七项",
         note="中型以上载客汽车、危险物品运输车辆记9分；载货汽车为第十一条第十四项，记3分"),
    dict(id="penalty-no-helmet", group="违法处罚",
         colloquial=["不戴头盔", "没戴头盔", "骑摩托不戴头盔", "头盔"],
         official="驾驶摩托车，不戴安全头盔",
         search_terms=["驾驶摩托车不戴安全头盔", "一次记1分"],
         statute="记分办法第十二条第十项",
         note="记1分，罚款按《道路交通安全法》第九十条"),
    dict(id="penalty-wrong-way", group="违法处罚",
         colloquial=["逆行", "逆向行驶", "倒着开", "反方向开"],
         official="驾驶机动车在高速公路、城市快速路上倒车、逆行、穿越中央分隔带掉头",
         search_terms=["逆行", "倒车", "穿越中央分隔带掉头", "一次记12分"],
         statute="记分办法第八条第六项",
         note="高速和城市快速路逆行记12分；普通道路逆行属第十一条第四项，记3分"),
    dict(id="penalty-unlicensed", group="违法处罚",
         colloquial=["无证驾驶", "没驾照开车", "没本开车", "无照驾驶", "驾照被吊销还开车"],
         official="未取得机动车驾驶证驾驶机动车",
         search_terms=["未取得机动车驾驶证驾驶机动车", "二百元以上二千元以下罚款", "十五日以下拘留"],
         statute="《道路交通安全法》第九十九条第一款第一项",
         note="罚款200～2000元，可并处15日以下拘留；不属记分范围（记分只针对持证驾驶人）"),
    dict(id="penalty-illegal-parking", group="违法处罚",
         colloquial=["违停", "乱停车", "违章停车", "停路边被拍"],
         official="驾驶机动车在高速公路或者城市快速路上违法停车",
         search_terms=["违法停车", "违反规定停放", "一次记9分"],
         statute="记分办法第九条第三项",
         note="高速、城市快速路违停记9分；普通道路违停不记分，按《道路交通安全法》第九十三条处罚"),
]


def main():
    data = json.load(io.open(PATH, encoding="utf-8"))
    entries = data["entries"]
    have = {e["id"] for e in entries}
    added = 0
    for e in NEW:
        if e["id"] in have:
            continue
        entries.append(e)
        added += 1
    data["updated"] = "2026-11-04"
    data["description"] = data.get("description", "") + (
        "；2026-11 增补「违法处罚」类口语（闯红灯、遮挡号牌、应急车道等 12 条），"
        "补上此前只有办事口径、没有处罚口径的缺口")
    json.dump(data, io.open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"条目 {len(entries) - added} → {len(entries)}（新增 {added}）")


if __name__ == "__main__":
    main()
