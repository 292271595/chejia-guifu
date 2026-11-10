# -*- coding: utf-8 -*-
"""事实表离线自测：不联网、不走 LLM，纯查表结果核对。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\RAG\rag-vehicle-system\backend")

import fact_table as FT

CASES = [
    ("C1能开什么车", "SCOPE:C1"),
    ("A2准驾车型有哪些", "SCOPE:A2"),
    ("大客车能开什么车", "SCOPE:A1"),
    ("A2驾驶证能开C6轻型牵引挂车吗", "CONTAIN:A2->C6"),
    ("C1能开B2吗", "CONTAIN:C1->B2"),
    ("A1能开A2吗", "CONTAIN:A1->A2"),
    ("70周岁以上还能申请哪些准驾车型", "AGE70_APPLY"),
    ("学习驾驶证明有效期是多久", "STUDY_CERT"),
    ("增驾A1需要什么条件", "ADD:A1"),
    ("C1能直接增驾A2吗", "ADD:A2"),
    ("B2能增驾摩托车吗", "ADD:MOTO"),
    ("增驾C6要什么条件", "ADD:C6"),
    ("考A1多大年龄", "AGE_APPLY:A1"),
    ("C1年龄要求多少岁", "AGE_APPLY:C1"),
    ("63岁能开大客车吗", "AGE_DRIVE:63:A1"),
    ("62岁能开大客车吗", "AGE_DRIVE:62:A1"),
    ("66岁能开大客车吗", "AGE_DRIVE:66:A1"),
    ("70岁还能开摩托车吗", "AGE_DRIVE:70:MOTO"),
    ("68岁能开C1吗", "AGE_DRIVE:68:C1"),
    ("C6能初次申领吗", "FIRST_APPLY:C6"),
    ("B2能直接考吗", "FIRST_APPLY:B2"),
    ("占用应急车道行驶记几分", "SCORE:6"),
    ("高速应急车道违法停车记几分", "SCORE:9"),
    # 科目一与科目二的预约规则不同：「10 日后预约」「5 次上限」只管科目二三
    ("科目一考试不合格多久可以重新预约", "EXAM:TIMES_K1"),
    # 口语说法：「挂了/重考」不等于「问考试内容」，不能被科目定义接走
    ("科目一挂了啥时候能重考", "EXAM:TIMES_K1"),
    ("科目一考什么内容", "EXAM:S1_CONTENT"),
    ("科目一多少分及格", "EXAM:PASS_S1"),
    # 口语说法「扣了6分能减分不」要进记分处理器，不能被实习期接走答成实习期定义
    ("扣了6分能减分不，我还在实习期", "SCORE:STUDY_REDUCE"),
    ("实习期能上高速吗", "INTERNSHIP:HIGHWAY"),
    ("科目二考试不合格多久能再约", "EXAM:TIMES"),
    # 问句指向「不予受理」情形时，首句必须直接给结论
    ("实习期内可以学法减分吗", "SCORE:STUDY_REDUCE"),
    ("驾驶证被暂扣期间可以学法减分吗", "SCORE:STUDY_REDUCE"),
    ("代替他人记分牟利记几分", "SCORE:12"),
    ("闯红灯扣几分", "SCORE:6"),
    ("不系安全带扣几分", "SCORE:1"),
    ("开车打电话扣几分", "SCORE:3"),
    ("遮挡号牌记几分", "SCORE:9"),
    ("高速倒车记几分", "SCORE:12"),
    ("醉驾吊销驾驶证多久能重考", "REVOKE:5"),
    ("醉驾营运车吊销几年", "REVOKE:10"),
    # 这两条原先期望 REVOKE:FOREVER / RECOVER，但实际由 BAN / RENEW 处理器接住，
    # 且输出内容经人工核对是正确的（终生禁驾 / 分三档含"注销未超2年可恢复"）。
    # 期望值随处理器归属调整，不是放宽标准——答案要点见 eval_generation 的端到端断言。
    ("肇事逃逸构成犯罪还能考驾照吗", "BAN:逃逸犯罪"),
    ("驾驶证被吊销多久能重考", "REVOKE:TABLE"),
    ("增驾有实习期吗", "INTERNSHIP"),
    ("实习期上高速公路有什么要求", "INTERNSHIP:HIGHWAY"),
    ("实习期是多久", "INTERNSHIP"),
    # 问"被注销怎么恢复"，首句就该答恢复，不能答成"过期换证分三档"（恢复藏在第二段里）
    ("驾驶证因超期未换证被注销怎么恢复", "RECOVER:WINDOW"),
    ("红绿色盲能考驾驶证吗", "PHYSICAL:color"),
    ("一只眼睛看不见能考驾照吗", "PHYSICAL:monocular"),
    ("听力不好能考驾照吗", "PHYSICAL:hearing"),
    ("申请A1身高要求多少", "PHYSICAL:height"),
    # ── 网页真题回归（2026-11 从各地交管「高频问答」抓取，均为用户原话）──
    # 这批题暴露的是同一类病：模板靠单个业务词就命中，然后拿固定话术换个说法，
    # 看起来专业、条号齐全，实际答的是另一件事。期望值 = 应归哪个处理器；
    # 期望 None = 必须不命中、交回检索（误命中比不命中危害大得多）。
    ("夫妻两人名下各有一辆车，可以直接互换号牌吗", "REG:PLATE_SWAP"),
    ("哪些驾驶人需要参加审验教育", "REVIEW:LIST"),
    ("C1驾驶证实习期内可以增驾摩托车吗", "INTERNSHIP:ADD"),
    ("C2考过科目一了，要去别的城市，剩下的科目怎么办", "EXAM:TRANSFER"),
    ("用临时身份证上的牌，拿到正式身份证后要变更吗", "REG:ID_CHANGE"),
    ("饮酒后驾驶机动车怎么处罚", "SCORE:ALCOHOL"),
    ("饮酒后驾驶营运机动车怎么处罚", "SCORE:ALCOHOL_OPERATE"),
    ("醉驾营运车吊销几年", "REVOKE:10"),
    ("70岁还能开摩托车吗", "AGE_DRIVE:70:MOTO"),
    # ── 同词跨制度：一个词对应两个结论相反的制度，分流错了就会答反（2026-11-04）──
    # 延期换证（第七十五条）延期期间不得驾驶；驾驶资格延期（第六十五条）延长期间可以驾驶。
    ("我今年63岁了，我有A2驾驶证，可以申请延期继续开A2吗", "AGE_DRIVE:63:A2"),
    ("62岁A2可以申请延长吗", "AGE_DRIVE:62:A2"),
    ("驾驶证延期换证怎么办", "RENEW:DELAY"),
    ("因出国驾驶证延期换证怎么办", "RENEW:DELAY"),
    ("延期换证期间能开车吗", "RENEW:DELAY"),
    ("驾驶证能延期吗", "RENEW:DELAY_TWO"),
    # 车型明确时必须按车型判，不能套摩托车模板（70 岁以上 C1/C2 能开，D/E 才要换 F）
    ("70岁C1还能开吗", "AGE_DRIVE:70:C1"),
    ("70岁C2还能开吗", "AGE_DRIVE:70:C2"),
    ("70岁D证还能骑吗", "AGE_DRIVE:70:MOTO"),
    ("6年免检的车，第2年和第4年要上线检测吗", "VEHICLE:EXEMPT"),
    ("车子报废了，注销证明在哪里下载", "REG:SCRAP_CERT"),
    # 同一个"电子"字，检验标志 ≠ 电子驾驶证。曾被"检验标志电子凭证效力"整段盖掉。
    ("电子驾驶证申请不成功是什么原因", None),
    # ── 累积记分：分值分档（第十二条档 / 24 档 / 36 档）──
    # 原来只认"记满 12 分"，扣了 N 分（N>12）整类漏到 RAG，且答案只写到考科目一。
    ("驾驶证扣了35分怎么办", "FULL_SCORE"),
    ("扣了18分怎么处理", "FULL_SCORE"),
    ("扣了24分怎么办", "FULL_SCORE"),
    ("扣了48分怎么办", "FULL_SCORE"),
    ("扣了12分怎么办", "FULL_SCORE"),
    ("累积记分达到12分怎么处理", "FULL_SCORE"),
    ("一个记分周期扣了18分怎么处理", "FULL_SCORE"),
    ("扣两次12分怎么办", "FULL_SCORE"),
    ("扣满12分会怎样", "FULL_SCORE"),
    ("满分教育可以异地参加吗", "FULL_SCORE:PLACE"),
    ("记满12分不学习会怎样", "FULL_SCORE:REFUSE"),
    # 反向：单次记分问法不能被满分学习模板吞掉，仍走 SCORE 逐条答
    ("闯红灯扣几分", "SCORE:6"),
    ("超速两次扣6分怎么处罚", "SCORE:OVERSPEED_TABLE"),
    # 记分周期本身仍归 PERIOD
    ("记分周期是多久", "PERIOD"),
    # 未满 12 分的记分清零，问句里没有"记分周期"字样也要接住
    ("扣了6分多久清零", "PERIOD"),
    ("记分什么时候清零", "PERIOD"),
    ("学法减分怎么清零", "SCORE:STUDY_REDUCE"),
    # 满了 12 分的"多久清零"要走满分学习，不能答成周期届满自动清除
    ("扣了35分多久清零", "FULL_SCORE"),
    # "要学多久"带 DURATION 诉求，曾被 cov 挡掉掉进 RAG
    ("扣满12分要学多久", "FULL_SCORE"),
    # 追问补全后的典型形态："扣满 12 分要考哪几个科目"，
    # 不接住会被 EXAM:COUNT 抢答成"增驾考哪几科"
    ("扣满12分要考哪几个科目", "FULL_SCORE"),
    ("扣满12分考科目几", "FULL_SCORE"),
    # 反向：普通科目考试问题仍归 EXAM
    ("增驾A2要考哪几个科目", "EXAM:COUNT"),
    ("科目一考什么内容", "EXAM:S1_CONTENT"),
    # AB 类满分学习天数与 C 类差一倍以上（30 / 7），问句点了车型就必须按车型给
    ("大车驾驶人记满12分要学习多少天", "FULL_SCORE"),
    ("A2扣满12分怎么办", "FULL_SCORE"),
    ("B2记满12分要学多久", "FULL_SCORE"),
    # ── 伤亡事故 / 非法改装后的检验周期（公交管〔2022〕295号第四条）──
    # 原实现只有标准优化周期，没有「仍按原规定周期检验」这个例外：
    # 「发生伤人事故后检车周期有变化吗」被答成「10 年内每 2 年一检」，
    # 恰好是这辆车不能适用的那个周期——答案与事实相反，用户按此办会漏检。
    ("发生伤人事故后，检车周期有变化吗", "VEHICLE:CYCLE_ACCIDENT"),
    ("发生伤人事故后年检周期有变化吗", "VEHICLE:CYCLE_ACCIDENT"),
    ("出过伤人事故的车还要年检吗", "VEHICLE:CYCLE_ACCIDENT"),
    ("撞伤人之后年检多久一次", "VEHICLE:CYCLE_ACCIDENT"),
    ("撞死人了 年检周期", "VEHICLE:CYCLE_ACCIDENT"),
    ("非法改装被处罚过 年检周期有变化吗", "VEHICLE:CYCLE_ACCIDENT"),
    ("摩托车发生伤人事故 检验周期", "VEHICLE:CYCLE_ACCIDENT"),
    ("出过伤人事故的车还能免检吗", "VEHICLE:EXEMPT_ACCIDENT"),
    ("非法改装被查过 还能免检吗", "VEHICLE:EXEMPT_ACCIDENT"),
    ("发生伤亡事故后免检资格还有吗", "VEHICLE:EXEMPT_ACCIDENT"),
    # 只提「事故」、没说伤没伤人 → 分两种情况答，不替用户假定
    ("事故车还能享受免检吗", "VEHICLE:EXEMPT_ACCIDENT_ASK"),
    ("事故车年检周期", "VEHICLE:CYCLE_ACCIDENT_ASK"),
    # 反向：没人受伤不触发例外；面包车本来就按原周期执行，事故不改变它
    ("就蹭掉点漆没人受伤 年检周期有变化吗", "VEHICLE:CYCLE"),
    ("没出过事故 年检周期", "VEHICLE:CYCLE"),
    ("面包车发生伤人事故 检验周期", "VEHICLE:CYCLE_ACCIDENT_NOCHANGE"),
    ("年检多久一次", "VEHICLE:CYCLE"),

    # ── 换证超期与注销恢复：这一域原先一条用例都没有，所以「超三年」被答成
    #    「提前 90 天来办」、「注销超过两年」被答成「考科目一恢复」都没人发现。
    #    分界线是「超期满 3 年 = 注销满 2 年」，两档结论完全相反（只考科目一 / 重考全部）。
    # 超期不满 1 年：还没注销，正常换证
    ("驾驶证过期半年了怎么办", "RENEW:EXPIRED"),
    # 超期 1~3 年：已注销但未满 2 年，可考科目一恢复
    ("超两年没换证怎么办", "RENEW:OVERDUE_1_3"),
    ("驾驶证过期两年了还能换证吗", "RENEW:OVERDUE_1_3"),
    # 超期满 3 年：注销满 2 年，恢复窗口关闭，只能重新申领
    ("驾驶证超三年未换证怎么办", "RENEW:OVERDUE_GE3"),
    ("驾驶证超过3年没换证", "RENEW:OVERDUE_GE3"),
    # 注销年限：说「超过两年」与「未超过两年」只差一个字，结论相反
    ("驾驶证注销超过两年怎么办", "RECOVER:CLOSED"),
    ("驾驶证注销超过2年怎么办", "RECOVER:CLOSED"),
    ("驾驶证注销未超过两年怎么办", "RECOVER:OPEN"),
    ("驾驶证注销两年内怎么恢复", "RECOVER:OPEN"),
    # 正好满 2 年落在边界：法条是「未超过」含 2 年，可恢复但要提醒 2 年内考完
    ("驾驶证注销满两年了还能恢复吗", "RECOVER:OPEN"),
    # 只说「过期几年」没说「注销几年」：要换算成注销时长（超期 - 1），不能退回三档
    ("过期两年了还能恢复吗", "RECOVER:OPEN"),
    ("过期四年了还能恢复吗", "RECOVER:CLOSED"),
    # 没给年限 → 三档并列，让用户自己定位
    ("驾驶证注销了怎么办", "RECOVER:WINDOW"),
    # 省略说法：没有「换证」二字，靠「超 N 年」+ 证件词进处理器
    ("驾照超了三年怎么办", "RENEW:OVERDUE_GE3"),
    ("驾驶证3年没换了", "RENEW:OVERDUE_GE3"),
    ("驾驶证超了两年怎么办", "RENEW:OVERDUE_1_3"),
    # 反向：吊销语境不能被超期分支抢走，那是「什么时候能重考」
    ("驾驶证被吊销三年了能考吗", "REVOKE:TABLE"),
    # 反向：不能被新分支抢走
    ("驾驶证期满换证要多久", "RENEW:BEFORE_90"),
    ("不换证会怎么样", "RENEW:CONSEQUENCE"),
    ("驾驶证注销情形有哪些", "CANCEL:LIST"),
    ("注销期间开车算无证驾驶吗", "STATUS_DRIVE"),
]

print("=" * 88)
print(f"{'问句':<30} {'期望':<24} {'实际':<24} 结果")
print("=" * 88)
ok = 0
fail = []
for q, expect in CASES:
    r = FT.lookup(q)
    actual = r["fact"] if r else "（未命中→走RAG）"
    # expect 为 None 表示"必须不命中"：该交给检索层，事实层不得抢答
    good = (r is None) if expect is None else (r is not None and actual == expect)
    if good:
        status = "OK"
        ok += 1
    else:
        status = "FAIL"
        fail.append((q, expect or "（不命中）", actual))
    print(f"{q:<30} {expect or '（不命中）':<24} {actual:<24} {status}")

print("=" * 88)
print(f"通过 {ok}/{len(CASES)}")
if fail:
    print("\n未通过明细：")
    for q, e, a in fail:
        print(f"  {q}  期望={e}  实际={a}")
