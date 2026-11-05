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
