# -*- coding: utf-8 -*-
"""
事实层公共基础（Fact Common）
==============================
把"车型代号、准驾包含关系、数字与百分比解析、子诉求（aspect）识别"这些
跨域复用的能力抽出来，供 fact_table / fact_score / fact_exam / fact_vehicle /
fact_license_ext 共用。

设计目标（对应压力测试暴露的几类系统性问题）
------------------------------------------------
1. 车型代号识别原先只认 [ABCMNP]+数字，D/E/F 摩托车代号整类漏识别
   （"F证有年龄上限吗""D证能开什么"全部落空）→ 统一为两段式识别。
2. "已持 A2 增驾 C6"这类问题，事实层不会判断"A2 准驾范围已含 C6"
   → 提供 covers() 做包含关系推理。
3. "货车超载50%"被"7座以上超员50%"规则抢答 → 提供 subject_of() /
   parse_percent()，把"主体（载客/载货）× 量级（百分比）"显式化。
4. 模板命中就给答案，不管用户到底问的是科目、罚则还是清单
   → 提供 detect_aspects()，让每个模板声明自己"能回答哪类诉求"。
"""

import re
from typing import Dict, List, Optional, Tuple

# ============================================================
# 一、准驾车型及代号（172号令 附件1，逐字取自原文）
# ============================================================
LICENSE_TABLE: Dict[str, Dict] = {
    "A1": dict(name="大型客车", vehicle="大型载客汽车",
               also=["A3", "B1", "B2", "C1", "C2", "C3", "C4", "M"], first_apply=False),
    "A2": dict(name="重型牵引挂车", vehicle="总质量大于4500kg的汽车列车",
               also=["B1", "B2", "C1", "C2", "C3", "C4", "C6", "M"], first_apply=False),
    "A3": dict(name="城市公交车", vehicle="核载10人以上的城市公共汽车",
               also=["C1", "C2", "C3", "C4"], first_apply=True),
    "B1": dict(name="中型客车", vehicle="中型载客汽车（含核载10人以上、19人以下的城市公共汽车）",
               also=["C1", "C2", "C3", "C4", "M"], first_apply=False),
    "B2": dict(name="大型货车", vehicle="重型、中型载货汽车；重型、中型专项作业车",
               also=["C1", "C2", "C3", "C4", "M"], first_apply=True),
    "C1": dict(name="小型汽车", vehicle="小型、微型载客汽车以及轻型、微型载货汽车；轻型、微型专项作业车",
               also=["C2", "C3", "C4"], first_apply=True),
    "C2": dict(name="小型自动挡汽车",
               vehicle="小型、微型自动挡载客汽车以及轻型、微型自动挡载货汽车；轻型、微型自动挡专项作业车；上肢残疾人专用小型自动挡载客汽车",
               also=[], first_apply=True),
    "C3": dict(name="低速载货汽车", vehicle="低速载货汽车", also=["C4"], first_apply=True),
    "C4": dict(name="三轮汽车", vehicle="三轮汽车", also=[], first_apply=True),
    "C5": dict(name="残疾人专用小型自动挡载客汽车",
               vehicle="残疾人专用小型、微型自动挡载客汽车（允许上肢、右下肢或者双下肢残疾人驾驶）",
               also=[], first_apply=True),
    "C6": dict(name="轻型牵引挂车", vehicle="总质量小于（不包含等于）4500kg的汽车列车",
               also=[], first_apply=False),
    "D": dict(name="普通三轮摩托车", vehicle="发动机排量大于50ml或者最大设计车速大于50km/h的三轮摩托车",
              also=["E", "F"], first_apply=True),
    "E": dict(name="普通二轮摩托车", vehicle="发动机排量大于50ml或者最大设计车速大于50km/h的二轮摩托车",
              also=["F"], first_apply=True),
    "F": dict(name="轻便摩托车", vehicle="发动机排量小于等于50ml，最大设计车速小于等于50km/h的摩托车",
              also=[], first_apply=True),
    "M": dict(name="轮式专用机械车", vehicle="轮式专用机械车", also=[], first_apply=True),
    "N": dict(name="无轨电车", vehicle="无轨电车", also=[], first_apply=True),
    "P": dict(name="有轨电车", vehicle="有轨电车", also=[], first_apply=True),
}

# 第十六条第一款原文清单：只能通过增驾取得的车型
ONLY_BY_ADD = ["A1", "A2", "B1", "C6"]

# 汽车类 / 摩托车类（实习期条款按"大类"判断）
CAR_CODES = ["A1", "A2", "A3", "B1", "B2", "C1", "C2", "C3", "C4", "C5", "C6", "M", "N", "P"]
MOTO_CODES = ["D", "E", "F"]

# 车型别称 -> 代号（按长度降序匹配，避免短词抢先）
ALIAS: Dict[str, str] = {
    "残疾人专用小型自动挡载客汽车": "C5",
    "残疾人专用小型自动挡": "C5",
    "普通三轮摩托车": "D", "普通二轮摩托车": "E",
    "大型客车": "A1", "重型牵引挂车": "A2",
    "城市公交车": "A3", "中型客车": "B1", "大型货车": "B2",
    "小型汽车": "C1", "小型自动挡汽车": "C2",
    "低速载货汽车": "C3", "三轮汽车": "C4", "轻型牵引挂车": "C6",
    "轻便摩托车": "F", "轮式专用机械车": "M",
    "无轨电车": "N", "有轨电车": "P",
    # 口语别称
    "大客车": "A1", "大巴车": "A1", "大巴": "A1", "客车A1": "A1",
    "牵引车": "A2", "半挂": "A2", "挂车头": "A2",
    "公交车": "A3", "巴士": "A3",
    "中巴": "B1", "中型载客": "B1",
    "大货车": "B2", "大货": "B2", "货车B2": "B2", "渣土车": "B2",
    "小轿车": "C1", "小汽车": "C1", "小车": "C1", "轿车": "C1",
    "自动挡": "C2",
    "农用车": "C3", "四轮农用": "C3",
    "房车": "C6", "拖挂房车": "C6",
    "三轮摩托": "D", "二轮摩托": "E",
}

# ============================================================
# 二、申请年龄条件（第十四条第一项，原文逐条对应）
# ============================================================
AGE_RULES: Dict[str, Tuple[int, Optional[int], str]] = {
    "C1": (18, None, "第十四条第一项第1目"),
    "C2": (18, None, "第十四条第一项第1目"),
    "C5": (18, None, "第十四条第一项第1目"),
    "F":  (18, None, "第十四条第一项第1目"),
    "D":  (18, 70, "第十四条第一项第2目"),
    "E":  (18, 70, "第十四条第一项第2目"),
    "C6": (20, 70, "第十四条第一项第3目"),
    "C3": (18, 63, "第十四条第一项第4目"),
    "C4": (18, 63, "第十四条第一项第4目"),
    "M":  (18, 63, "第十四条第一项第4目"),
    "A3": (20, 63, "第十四条第一项第5目"),
    "B1": (20, 63, "第十四条第一项第5目"),
    "B2": (20, 63, "第十四条第一项第5目"),
    "N":  (20, 63, "第十四条第一项第5目"),
    "P":  (20, 63, "第十四条第一项第5目"),
    "A1": (22, 63, "第十四条第一项第6目"),
    "A2": (22, 63, "第十四条第一项第6目"),
}

AGE_SPECIAL = {
    "A1": "接受全日制驾驶职业教育的学生，申请大型客车（A1）的，年龄放宽至19周岁以上、63周岁以下（第十四条第一项第7目）。",
    "A2": "接受全日制驾驶职业教育的学生，申请重型牵引挂车（A2）的，年龄放宽至19周岁以上、63周岁以下（第十四条第一项第7目）。",
}

# 第六十五条：63 / 70 周岁禁驾清单
FORBIDDEN_AT_63 = ["A1", "A2", "A3", "B1", "B2", "M", "N", "P"]
FORBIDDEN_AT_70 = ["C3", "C4", "C6", "D", "E"]

DOWNGRADE_RULES = {
    "A1": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "A2": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证；其中属于持有重型牵引挂车驾驶证的，还可以保留轻型牵引挂车（C6）准驾车型",
    "A3": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "B1": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "B2": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "M": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "N": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "P": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
    "D": "换领准驾车型为轻便摩托车（F）的机动车驾驶证",
    "E": "换领准驾车型为轻便摩托车（F）的机动车驾驶证",
    "C6": "换领准驾车型为小型汽车（C1）或者小型自动挡汽车（C2）的机动车驾驶证",
}

# ============================================================
# 三、增驾条件（第十七条）
# ============================================================
ADD_RULES: Dict[str, List[Tuple[List[str], int, int, str]]] = {
    "C6": [(["C1", "C2"], 1, 1, "第十七条第一项")],
    "B1": [(["A3", "B2", "C1", "C2", "C3", "C4"], 2, 2, "第十七条第二项")],
    "A2": [(["B1", "B2"], 2, 2, "第十七条第三项"),
           (["A1"], 1, 2, "第十七条第三项")],
    "A1": [(["A3", "B1"], 2, 3, "第十七条第四项"),
           (["B2"], 3, 3, "第十七条第四项"),
           (["A2"], 1, 3, "第十七条第四项")],
}

NO_YEAR_ADD = ["A3", "B2", "C1", "C2", "C3", "C4", "D", "E", "F", "M", "N", "P"]

# ============================================================
# 四、车型代号识别（两段式：带数字 / 单字母摩托车类）
# ============================================================
_CODE_DIGIT_RE = re.compile(r"(?<![A-Za-z0-9])([ABCDEFMNP])\s*([1-6])(?![0-9])", re.I)
# 单字母代号：D/E/F/M/N/P，必须紧跟"证/照/本"或标点/结尾，避免误伤英文
_CODE_LETTER_RE = re.compile(
    r"(?<![A-Za-z0-9])([DEFMNP])(?=\s*(?:证|照|本|类)|[\s，,。、；;？?！!和及与或跟]|$)")
_ALIAS_SORTED = sorted(ALIAS.keys(), key=len, reverse=True)


def extract_codes(text: str) -> List[str]:
    """从文本中抽取准驾车型代号（去重保序）。支持代号与中文别称。

    修复点：原实现只匹配 [ABCMNP]+数字，导致 D/E/F 摩托车代号整类无法识别
    （"F证有没有年龄上限""D证能开什么车"全部落到 RAG 且容易被答错）。
    """
    found: List[str] = []
    for m in _CODE_DIGIT_RE.finditer(text):
        code = (m.group(1) + m.group(2)).upper()
        if code in LICENSE_TABLE and code not in found:
            found.append(code)
    for m in _CODE_LETTER_RE.finditer(text):
        code = m.group(1).upper()
        if code in LICENSE_TABLE and code not in found:
            found.append(code)
    for alias in _ALIAS_SORTED:
        if alias in text:
            code = ALIAS[alias]
            if code not in found:
                found.append(code)
    return found


def covers(holder: str, target: str) -> bool:
    """holder 的准驾范围是否包含 target（含传递闭包）。

    "持 A2 增驾 C6" 这类问题必须先判断这一步：A2 准予驾驶 C6，
    本来就不需要增驾，直接回答"需要增驾"是硬错误。
    """
    if holder == target:
        return True
    seen, stack = set(), [holder]
    while stack:
        cur = stack.pop()
        for nxt in LICENSE_TABLE.get(cur, {}).get("also", []):
            if nxt == target:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def covering_codes(target: str) -> List[str]:
    """列出准驾范围包含 target 的所有车型代号（按粗→细排序输出用）。"""
    order = ["A1", "A2", "A3", "B1", "B2", "C1", "C2", "C3", "C4", "C5", "C6",
             "D", "E", "F", "M", "N", "P"]
    return [c for c in order if c != target and covers(c, target)]


# ============================================================
# 五、口语规范化 / 关键词工具
# ============================================================
def norm(question: str) -> str:
    """口语规范化：把常见口语说法换成法规用语，便于匹配。

    注意顺序与边界：先把"醉驾"归一为"醉酒"，再用否定环视处理剩余"酒驾"，
    否则 "醉酒" 中的 "酒驾" 会被二次替换成 "醉饮酒"，污染关键词。
    """
    q = question.replace("醉驾", "醉酒")
    q = re.sub(r"(?<!醉)酒驾", "饮酒", q)
    q = q.replace("扣分", "记分").replace("驾照", "驾驶证")
    return q


def has(text: str, words) -> bool:
    return any(w in text for w in words)


def fmt_codes(codes: List[str]) -> str:
    return "、".join(f"{c}（{LICENSE_TABLE[c]['name']}）" for c in codes if c in LICENSE_TABLE)


# ============================================================
# 六、中文数字 <-> 数值
# ============================================================
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def cn2int(s: str) -> Optional[int]:
    """中文数字转整数，支持 一/十二/二十/百分之五十/一百 等常见写法。"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            total += (cur or 1) * unit
            cur = 0
        else:
            return None
    return total + cur


_PCT_RE = re.compile(r"百分之\s*([0-9]+|[一二三四五六七八九十百两]+)|([0-9]+)\s*[%％]")


def parse_percent(text: str) -> Optional[int]:
    """抽取问题中的百分比数值（"超载50%""百分之五十"→50）。"""
    m = _PCT_RE.search(text)
    if not m:
        return None
    return cn2int(m.group(1) or m.group(2))


# 注意：不要把单字"分"放进来——"百分之50"里的"百"会被误当成数量词
# 改写成"100分之50"。分钟/分（记分）已由更长单位覆盖。
_NUM_UNITS = ("个月", "个记分周期", "记分周期", "周岁", "小时", "分钟", "厘米",
              "公斤", "千克", "毫升", "岁", "年", "日", "天", "次", "种", "项",
              "个", "周", "米", "人", "座", "升")
_NUM_STYLE_RE = re.compile(
    r"(?<!第)(?:百分之([一二三四五六七八九十百两]+)|([一二三四五六七八九十百两]+))(?!分之)"
    r"(?=(?:" + "|".join(sorted(_NUM_UNITS, key=len, reverse=True)) + r"))"
)


def num_style(text: str) -> str:
    """把中文数字量词统一为阿拉伯数字：十二个月→12个月，三个月→3个月。

    压力测试里"记分周期为十二个月""提前三个月"这类表述与用户/判分口径
    （12个月、90日）对不上，属于同一类表达不一致问题，统一在输出层收敛。
    条款序号（第十二条、第三项）不在单位表内，不会被改写。
    """
    def _rep(m):
        if m.group(1):
            val = cn2int(m.group(1))
            return f"{val}%" if val is not None else m.group(0)
        raw = m.group(2)
        val = cn2int(raw)
        if val is None:
            return m.group(0)
        # "一次记12分""一项规定"属于固定法律表达，保持原样，不改成"1次"
        if val == 1 and raw in ("一", "壹"):
            return m.group(0)
        return str(val)
    return _NUM_STYLE_RE.sub(_rep, text)


# ============================================================
# 七、违法主体识别（载客 / 载货 / 营运客车 / 危险品 / 校车）
# ============================================================
SUBJECT_PAT = {
    "cargo": re.compile(r"(载货|货车|货物|超重|超限|渣土|牵引挂车(?!.*载客))"),
    "passenger": re.compile(r"(载客|客车|超员|载人|核定人数|乘客)"),
    "ops_bus": re.compile(r"(校车|公路客运|旅游客运|营运客)"),
    "danger": re.compile(r"(危险物品|危化品|危险化学品|爆炸物品|剧毒|放射性)"),
}


def subject_of(q: str) -> str:
    """判断违法行为涉及的主体类型。载货优先于载客（"货车超载"不含载客语义）。"""
    if SUBJECT_PAT["danger"].search(q):
        return "danger"
    if SUBJECT_PAT["ops_bus"].search(q):
        return "ops_bus"
    if SUBJECT_PAT["cargo"].search(q) and not re.search(r"载客|客车|超员|载人", q):
        return "cargo"
    if SUBJECT_PAT["passenger"].search(q):
        return "passenger"
    return "any"


ROAD_PAT = {
    "highway": re.compile(r"(高速公路|高速|城市快速路|快速路)"),
    "other": re.compile(r"(非高速|普通道路|其他道路|国道|省道|县道|市区|城区|一般道路)"),
}


def road_of(q: str) -> str:
    if ROAD_PAT["other"].search(q):
        return "other"
    if ROAD_PAT["highway"].search(q):
        return "highway"
    return "any"


# ============================================================
# 八、子诉求（aspect）识别
# ============================================================
# 设计动机：模板一旦命中意图就直接作答，于是"增驾C6考什么科目"被
# 增驾条件模板抢答成"需持C1满1年"，答非所问且用户看不出错。
# 解法：先识别用户"问的是哪一类诉求"，再要求模板声明自己覆盖哪些诉求，
# 覆盖不到就不许直接作答（降级到 RAG），从机制上杜绝"命中即套模板"。
ASPECT_PAT = {
    # 只认"科目/考试内容"类表述。"能考什么驾照""考什么车"问的是车型，不是考试科目，
    # 若被当成 SUBJECT 诉求，会把身体条件、准驾范围类处理器全部挡掉。
    "SUBJECT":  re.compile(r"(考什么科目|考哪些科目|考哪几科|考几[科个]|几个科目|"
                           r"考试内容|考试科目|科目[一二三四])"),
    "PENALTY":  re.compile(r"(怎么处罚|如何处罚|处罚|罚款|罚多少|扣车|拘留|什么后果|后果是|怎么罚)"),
    "LIST":     re.compile(r"(哪些情形|什么情形|哪些情况|哪几种情形|几种情形|有哪些.{0,6}(情形|情况|行为)|包括哪些)"),
    # 只认"程序性诉求"本身。原来还含"换证|审验|转入"这些**业务名**，
    # 结果"逾期未审验还开车怎么处罚"被判成 PROCEDURE，把只覆盖 PENALTY 的罚则处理器挡在门外。
    "PROCEDURE": re.compile(r"(流程|手续|材料|怎么办|如何办理|怎么办理|去哪|在哪里办|哪里办|"
                            r"需要带|提交什么|怎么弄)"),
    "DURATION": re.compile(r"(多久|多长时间|几年|几个月|多少日|几天|期限|有效期|上限)"),
    "VALUE":    re.compile(r"(多少[分厘米公斤种次个]|几[分厘米公斤种次个])"),
    # 只认"标志/标识"本身：含"悬挂"会把"未悬挂号牌记几分"误判成标志类诉求
    "SIGN":     re.compile(r"(实习标志|学车专用标识|残疾人机动车专用标志|检验标志|粘贴标志|悬挂.{0,3}标志)"),
}

# 模板默认覆盖的诉求集合
DEFAULT_ASPECTS = frozenset({"QUALIFY"})


def detect_aspects(q: str) -> frozenset:
    """识别问题包含哪些"子诉求"。返回空集表示只问常规资格结论。"""
    got = set()
    for name, pat in ASPECT_PAT.items():
        if pat.search(q):
            got.add(name)
    return frozenset(got)


# ============================================================
# 九、来源结构
# ============================================================
def source(title: str, snippet: str, source_file: str = "driving_license.md") -> Dict:
    return {"title": title, "source": source_file, "score": 1.0, "snippet": snippet[:300]}


def result(answer: str, src: List[Dict], fact: str, aspects=None) -> Dict:
    r = {"answer": answer, "sources": src, "fact": fact}
    if aspects is not None:
        r["covers"] = frozenset(aspects)
    return r
