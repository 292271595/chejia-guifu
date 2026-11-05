"""领域守卫验收：哪些该拦、哪些必须放行。

分三组，全部纯字符串，不调模型，毫秒级跑完：

  A 组  车驾问题必须全部放行（含题库 116 条 + 一堆口语问法）
        —— 这一组是底线。只要有一条被误杀，用户就会发现"这也不懂"，
        比漏答十个护照问题严重。
  B 组  明确的他域问题必须全部拦下
  C 组  跨域问题必须放行（「用护照能办驾驶证吗」这种，答案是车驾侧的）

用法：
  python tools/test_scope_guard.py
  python tools/test_scope_guard.py --engine   # 再验一遍真实链路（要引擎）
"""
import glob
import json
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_root, "backend"))

import scope_guard


def load_bank():
    """题库里的问法——每一题都是真实该答的问题。"""
    qs = []
    for p in glob.glob(os.path.join(_root, "data", "eval_100_part*.json")):
        d = json.load(open(p, encoding="utf-8"))
        rows = d if isinstance(d, list) else (
            d.get("questions") or d.get("items") or d.get("cases") or [])
        for r in rows:
            q = r.get("q") or r.get("question")
            if q:
                qs.append(q)
    return qs


# 题库之外的写法。题库是"标准问法"，真实用户不会这么规整——
# 误杀通常就发生在这类口语表达上。
ORAL = [
    "喝酒开车被抓了", "我车被撞了对方跑了", "被拍了违章怎么处理",
    "我的驾照扣了分怎么查", "买二手车要注意什么", "新车多久年审",
    "追尾了怎么办", "我停在路边被贴条了", "被扣车了怎么办", "电子眼拍到了",
    "限号今天能开吗", "这条路限速多少", "撞了人要赔多少",
    "异地买车能上本地牌吗", "交强险到期了", "我的车年检过期",
    "驾驶人体检去哪", "换本要多久", "摇号中签了怎么上牌",
    "公司名下的车过户给个人", "实习期能上高速吗", "驾照被吊销还能考吗",
    "C1 升 B2 要什么条件", "科目二挂了三次", "学法减分怎么弄",
    "大货车超载怎么罚", "电动车要戴头盔吗", "车卖了没过户出事算谁的",
    "我驾照是老家的能在北京用吗", "扣满12分车还能开吗",
    "B2 记满12分要学多久", "摩托车能上高速吗", "C1 能开黄牌货车吗",
    "罚款没交会怎么样", "年检标志还要贴吗", "驾驶证照片能换吗",
]

# 明确不在范围内。前几条是用户实际踩过的（护照），其余按领域铺开。
OFF_TOPIC = [
    "怎么办护照", "护照过期了怎么换", "签证要多久下来", "港澳通行证怎么续签",
    "社保断缴有什么影响", "公积金怎么提取", "医保报销比例是多少",
    "退休手续怎么办理", "房贷利率是多少", "房产过户流程",
    "股票怎么开户", "信用卡逾期怎么办", "征信花了怎么恢复",
    "高考志愿怎么填", "小孩上学怎么报名", "考研要准备多久",
    "机票退票手续费多少", "火车票改签规则", "酒店订单能退吗", "旅游攻略推荐",
    "个税怎么申报", "营业执照怎么办", "发票丢了怎么补",
    "结婚证怎么领", "离婚财产怎么分", "疫苗本丢了怎么办",
]

# 跨域：既提到他域的事，问的又是车驾。这类必须放行，
# 否则用户会碰到"我就顺嘴提了句护照，它就不答了"。
CROSS = [
    "用护照能办驾驶证吗",
    "身份证过期了能换驾驶证吗",
    "居住证没办下来能上牌吗",
    "医保和车辆保险的区别",
    "社保断了影响我贷款买车吗",
    "户口迁到外地了车牌怎么办",
    "护照丢了驾驶证还在能开车吗",
]

# 中性残句：没有车驾痕迹，也没有他域痕迹。交给追问补全，这里不拦。
NEUTRAL = ["要多久", "那 B2 呢", "可以吗", "多少钱", "几年能考", "那要去哪办"]


def main():
    bank = load_bank()
    fails = 0

    print("=" * 72)
    print(f"A 组 · 必须放行（题库 {len(bank)} 条 + 口语 {len(ORAL)} 条）")
    for q in bank + ORAL:
        d = scope_guard.check(q)
        if d:
            fails += 1
            print(f"  FAIL 误杀[{d}] {q}")
    print(f"  放行 {len(bank) + len(ORAL) - sum(1 for q in bank + ORAL if scope_guard.check(q))}"
          f"/{len(bank) + len(ORAL)}")

    print("\nB 组 · 必须拦下")
    hit = 0
    for q in OFF_TOPIC:
        d = scope_guard.check(q)
        if d:
            hit += 1
        else:
            fails += 1
            print(f"  FAIL 漏网 {q}")
    print(f"  拦住 {hit}/{len(OFF_TOPIC)}")

    print("\nC 组 · 跨域必须放行")
    c_hit = 0
    for q in CROSS:
        d = scope_guard.check(q)
        if not d:
            c_hit += 1
        else:
            fails += 1
            print(f"  FAIL 误杀[{d}] {q}")
    print(f"  放行 {c_hit}/{len(CROSS)}")

    print("\nD 组 · 中性残句不拦（交给追问补全）")
    n_hit = 0
    for q in NEUTRAL:
        if not scope_guard.check(q):
            n_hit += 1
        else:
            fails += 1
            print(f"  FAIL 误拦 {q}")
    print(f"  放行 {n_hit}/{len(NEUTRAL)}")

    print("\n" + "=" * 72)
    if fails:
        print(f"失败 {fails} 处")
        return 1
    print("全部通过")
    return 0


def engine_check():
    """真实链路走一遍：拒答要快、字段要全、正常问题不受影响。"""
    import time
    from rag_engine import create_rag_engine

    eng = create_rag_engine()
    if not eng:
        print("引擎没起来，跳过 --engine")
        return

    print("\n" + "=" * 72)
    print("E 组 · 真实链路")
    for q in ["怎么办护照", "社保怎么交"]:
        t0 = time.time()
        r = eng.query(q)
        sec = time.time() - t0
        ok = r.get("refused") and r.get("degraded") == "off_topic"
        print(f"  {'OK  ' if ok else 'FAIL'} {sec:.3f}s {q} -> "
              f"refused={r.get('refused')} domain={r.get('scope_domain')}")

    for q in ["驾驶证扣了35分怎么办", "C1 能开什么车"]:
        r = eng.query(q)
        ok = not r.get("refused")
        print(f"  {'OK  ' if ok else 'FAIL'} 正常问题 {q} -> 命中 {r.get('fact_hit')}")


if __name__ == "__main__":
    code = main()
    if "--engine" in sys.argv:
        try:
            engine_check()
        except Exception as e:
            print(f"引擎组失败：{e}")
            code = code or 1
    sys.exit(code)
