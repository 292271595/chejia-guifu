# 200 条真实问答核验台账

生成日期：2026-11-19。来源：用户提供的一问一答清单，逐条跑系统命中 + 检索知识库原文核对。
用途：决定哪些能做成合规表行、哪些是硬伤要纠正、哪些没有法条依据不能进固定答案库。
分类口径按项目文档 6.3「增长机制禁区」：无来源、无守卫、仅凭用户说对的一律不入库。

## 硬伤-需纠正（6 条）

- 第 7 题｜科一没过能补考吗
  用户答案：当天可补考一次。
  系统命中：EXAM:TIMES_K1　检索依据：driving_license　⚠️ 答案写"当天可补考一次"，法条与系统均为"当场补考一次"——当天≠当场
- 第 21 题｜C1能增驾摩托吗
  用户答案：能，但要过实习期。
  系统命中：ADD:MOTO　检索依据：driving_license　⚠️ 增驾摩托答"要过实习期"，与系统答案（无持证年限要求）冲突，法条第十七条无此要求
- 第 24 题｜C1满三年能增B1吗
  用户答案：满2年且连续2个记分周期无满分记录。
  系统命中：RAG(漏)　检索依据：driving_license　⚠️ 题目写"满三年"但答案写"满2年"；C1增B1法条确为满2年，题目数字本身错误
- 第 36 题｜增驾要重新体检吗
  用户答案：要。
  系统命中：PHYSICAL_REPORT:TABLE　检索依据：mps_convenience_measures　⚠️ 问"增驾要重新体检吗"，问法错配到体检周期处理器；属路由问题非答案问题
- 第 76 题｜过期两年
  用户答案：1–3年内考科一可恢复。
  系统命中：RENEW:OVERDUE_1_3　检索依据：mps_convenience_measures　⚠️ 超期2年答"1–3年内考科一可恢复"口径不准；法条分界是注销未满2年可恢复
- 第 80 题｜身体不适合开车
  用户答案：30日内申请注销。
  系统命中：BODY_CHANGE　检索依据：driving_license　⚠️ 身体不适合开车答"30日内申请注销"不完整：先降级，无其他准驾车型才注销

## 法规有据-可补候选（88 条）

- 第 2 题｜报名要带什么
  用户答案：身份证、体检证明、照片；增驾再带原驾驶证。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 3 题｜C1和C2考哪个
  用户答案：C1能开手动+自动，C2只能自动，C2更好考。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 9 题｜科二能取消预约吗
  用户答案：提前一天可取消。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 12 题｜安全文明驾驶是科四吗
  用户答案：是，和科三分开考。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 15 题｜约了考试没空去
  用户答案：提前一天取消。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 16 题｜取消要提前多久
  用户答案：至少提前一天。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 17 题｜约了不去算什么
  用户答案：算本次考试不合格。
  系统命中：RAG(漏)　检索依据：driving_exam
- 第 20 题｜外地能考驾照吗
  用户答案：能，凭身份证全国通考。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 25 题｜增驾后有效期变吗
  用户答案：不变，记分周期按初次领证算。
  系统命中：RAG(漏)　检索依据：traffic_violations
- 第 28 题｜超龄降级怎么办
  用户答案：要先体检。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 29 题｜C2过了科一要去外地
  用户答案：申请变更考试地。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 30 题｜变更考试地能几次
  用户答案：最多3次。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 31 题｜外地考了一半能转回吗
  用户答案：能，变更考试地继续考。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 34 题｜增驾期间扣分影响吗
  用户答案：申请前记分周期不能有满分记录。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 35 题｜想升A1要啥条件
  用户答案：满足相应准驾年限和记分要求。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 37 题｜驾驶证到期怎么换
  用户答案：到期前90日内申请，带身份证、驾驶证、体检证明。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 38 题｜提前多久能换
  用户答案：到期前90天内。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_law
- 第 40 题｜异地能换吗
  用户答案：能，核发地或外地车管都行。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 42 题｜证损坏了怎么换
  用户答案：办损毁换证。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 43 题｜补证要回原籍吗
  用户答案：不用，异地可补。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 44 题｜补了新证旧证还能用吗
  用户答案：作废，不能再用。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 46 题｜警医邮网点能办吗
  用户答案：能。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 55 题｜什么是审验
  用户答案：定期到交管部门接受检查和学习。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 67 题｜能减多少
  用户答案：一个周期最多6分。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_current
- 第 70 题｜哪些情况不能减
  用户答案：满分、实习期、暂扣期、有未处理违法等。
  系统命中：RAG(漏)　检索依据：traffic_violations
- 第 72 题｜会人脸识别吗
  用户答案：会，随机抓拍防替考。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 73 题｜审验前要处理违章吗
  用户答案：要。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 74 题｜C1扣分要学习吗
  用户答案：一般不审验，除非事故致死担责。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 81 题｜70岁每年交身体证明
  用户答案：按规定须提交身体条件证明。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 83 题｜三力测试是啥
  用户答案：记忆力、判断力、反应力测试。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 84 题｜三力测试怎么考
  用户答案：20题、20分钟、90分合格。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures　⚠️ 三力测试答"20题、20分钟"，知识库只写满分100/90分合格/一年5次，无这两个数字
- 第 88 题｜和纸质版一样吗
  用户答案：有同等效力。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 90 题｜扣留期间能补证吗
  用户答案：不能。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 91 题｜新车怎么上牌
  用户答案：向住所地车管所申请注册登记。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 92 题｜带什么材料
  用户答案：身份证、发票、合格证、交强险、购置税。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 93 题｜上牌多少钱
  用户答案：号牌100元、行驶证10元、登记证10元。
  系统命中：RAG(漏)　检索依据：vehicle_registration　⚠️ 上牌费"号牌100元、行驶证10元"知识库查无收费数字依据
- 第 95 题｜"一件事"是啥
  用户答案：网上一次办结上牌。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 96 题｜要开车去车管所吗
  用户答案：预查验的不用，未预查验要验车。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 97 题｜预查验是啥
  用户答案：出厂前已查验，免到场验车。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 99 题｜网上几次机会
  用户答案：随机50选1、自编50次。
  系统命中：RAG(漏)　检索依据：driving_exam　⚠️ 选号"自编50次"知识库未命中该数字，需另行核实
- 第 100 题｜选号后不去上牌
  用户答案：弃号两年内禁止网上选号。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 101 题｜能用老号牌吗
  用户答案：符合条件可以。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_work
- 第 103 题｜没上牌能办临牌吗
  用户答案：能。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 104 题｜临牌能办几次
  用户答案：最多3次。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 105 题｜分期车能网上上牌吗
  用户答案：能，可合并办抵押。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 107 题｜电子行驶证单独申请
  用户答案：上牌时勾选合并申领即可。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 110 题｜带什么材料
  用户答案：身份证、发票、登记证、行驶证。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 112 题｜要开车去吗
  用户答案：要，需查验车辆。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 113 题｜外地买车本地上牌
  用户答案：可办转籍/转入。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 118 题｜互换啥条件
  用户答案：同上。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 119 题｜互换后多久能再换
  用户答案：一年一次。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 125 题｜改色要备案吗
  用户答案：要。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 126 题｜改色后多久备案
  用户答案：变更后10日内。
  系统命中：RAG(漏)　检索依据：vehicle_registration　⚠️ 改色备案答"10日内"，164号令第二十三条为"三十日内"
- 第 129 题｜使用性质变了
  用户答案：办变更登记。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 130 题｜搬家了联系方式要改吗
  用户答案：30日内变更备案。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 132 题｜装行李架合规吗
  用户答案：不超车身，货物距车顶不超0.5米。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 133 题｜货车能装尾板吗
  用户答案：允许，可跨省通办。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 134 题｜残疾人辅助装置能装吗
  用户答案：允许加装。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_work
- 第 136 题｜变更能跨省办吗
  用户答案：部分项目可跨省通办。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 137 题｜变更要开车去吗
  用户答案：一般要查验。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 141 题｜6–10年怎么检
  用户答案：第6、10年上线，其余两年领标。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_current
- 第 142 题｜10年以上
  用户答案：每年检验。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_current
- 第 146 题｜营运改非营运
  用户答案：不享受免检。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_current
- 第 149 题｜还要贴纸标吗
  用户答案：不用，电子标即可。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 155 题｜贷款还清怎么解押
  用户答案：车管所办理解押。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 156 题｜带什么材料
  用户答案：身份证、登记证、结清证明等。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 157 题｜解押能代办吗
  用户答案：能，需委托书。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 164 题｜车辆灭失
  用户答案：申请注销。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 165 题｜被查封扣押能注销吗
  用户答案：不能。
  系统命中：RAG(漏)　检索依据：colloquial_terms
- 第 170 题｜带什么材料
  用户答案：身份证、发票、合格证、CCC证书。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 172 题｜临时牌怎么注销
  用户答案：带身份证到交管部门、交回号牌。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 173 题｜超标车过渡期后
  用户答案：不能上路，需置换或报废。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 174 题｜黄牌能换绿牌吗
  用户答案：不能直接换，车要符合新国标。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 175 题｜怎么区分两类车
  用户答案：看合格证/公告目录，新国标≤25km/h等。
  系统命中：RAG(漏)　检索依据：vehicle_inspection
- 第 177 题｜未满18能骑吗
  用户答案：不能。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 178 题｜摩托车怎么上牌
  用户答案：带材料到车管所查验注册。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 179 题｜摩托能上高速吗
  用户答案：黄牌可以，蓝牌轻便摩托不行。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 180 题｜上高速能带人吗
  用户答案：两轮摩托不能带人。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 181 题｜摩托临牌怎么装
  用户答案：纸质随车携带即可。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 183 题｜违章能12123处理吗
  用户答案：符合条件的可线上办。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 185 题｜线上处理啥条件
  用户答案：警告或200元以下、6分以下、备案后发生。
  系统命中：RAG(漏)　检索依据：traffic_violation_procedure
- 第 188 题｜能备案几辆
  用户答案：最多3辆，每天1辆、每年5次。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_current　⚠️ 备案车"每天1辆每年5次"依据未命中，需另行核实
- 第 192 题｜挪车注意啥
  用户答案：实名、拍清号牌、10分钟可催单。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 194 题｜哪些事故能快处
  用户答案：仅财产损失、无争议的碰撞。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 196 题｜军人办业务带啥
  用户答案：身份证或军官证等有效证件。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 197 题｜外籍人员带啥
  用户答案：护照、签证/居留许可、住宿登记。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 198 题｜港澳台居民带啥
  用户答案：港澳台居民居住证或通行证+住宿登记。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 199 题｜能委托别人代办吗
  用户答案：能，需委托书和双方身份证明。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures

## 操作类-无统一法条（30 条）

- 第 13 题｜每科能考几次
  用户答案：当天可补考一次；科二、科三预约各不超5次。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 18 题｜驾校能退费吗
  用户答案：按合同和进度协商。
  系统命中：RAG(漏)　检索依据：compulsory_insurance_regulation
- 第 19 题｜考试费怎么交
  用户答案：12123或驾校代缴。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 26 题｜A1能降成C1吗
  用户答案：带身份证、驾驶证、照片去窗口降级换证。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 27 题｜自愿降级怎么办
  用户答案：不用体检，窗口申请。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 32 题｜增驾大车啥条件
  用户答案：年龄、驾龄、无满分记录，具体问当地。
  系统命中：RAG(漏)　检索依据：driving_license
- 第 41 题｜证丢了怎么补
  用户答案：12123或窗口办遗失补证。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 47 题｜外地证转回本地
  用户答案：12123或窗口办转入。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 53 题｜照片能自己传吗
  用户答案：按系统提示操作。
  系统命中：RAG(漏)　检索依据：vehicle_inspection_work
- 第 59 题｜审验能网上学吗
  用户答案：1–8分可在12123学。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 60 题｜扣几分必须现场学
  用户答案：9–11分须现场。
  系统命中：RAG(漏)　检索依据：traffic_violations
- 第 68 题｜怎么操作
  用户答案：12123申请网上学习考试。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 87 题｜电子驾驶证怎么申请
  用户答案：12123申领。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 94 题｜网上能办吗
  用户答案：能，12123新车上牌"一件事"。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 98 题｜车牌号怎么选
  用户答案：网上50选1或自编，现场50选1。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 106 题｜上牌能代办吗
  用户答案：能，用电子委托。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 108 题｜单位车怎么上牌
  用户答案：单位版12123或窗口。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 116 题｜夫妻的车能换牌吗
  用户答案：要符合同一所有人等条件，不能直接换。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 131 题｜怎么改联系方式
  用户答案：12123或窗口。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 138 题｜单位车变更所有人
  用户答案：窗口或单位版办理。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_regulation
- 第 160 题｜车在外地撞坏
  用户答案：向当地回收企业交售报废。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 171 题｜车牌丢了
  用户答案：到网点补领。
  系统命中：RAG(漏)　检索依据：vehicle_registration
- 第 184 题｜哪些不能线上办
  用户答案：罚款超200、记分超6分、现场处罚等。
  系统命中：RAG(漏)　检索依据：road_traffic_safety_law
- 第 186 题｜忘密码又换手机号
  用户答案：找回密码里用新手机号。
  系统命中：RAG(漏)　检索依据：exam_acceptance_standards
- 第 187 题｜啥是备案非本人机动车
  用户答案：把别人名下小型车绑到自己12123。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 189 题｜怎么备案
  用户答案：扫码或短信验证。
  系统命中：RAG(漏)　检索依据：spc_traffic_accident_interpretation
- 第 190 题｜被别人备案怎么解除
  用户答案：12123里解除备案关系。
  系统命中：RAG(漏)　检索依据：driving_work_standard
- 第 191 题｜车被堵怎么挪车
  用户答案：12123"一键挪车"。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 195 题｜快处易赔从哪进
  用户答案：微信公众号/随申办等入口。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures
- 第 200 题｜怎么办业务预约
  用户答案：当地政务APP或网上预约。
  系统命中：RAG(漏)　检索依据：mps_convenience_measures

## 系统已有答案（76 条）

- 第 1 题｜多大年龄能考驾照
  用户答案：小型车满18周岁，大车要求更高（20–22周岁起）。
  系统命中：AGE:APPLY_ALL　检索依据：mps_convenience_measures
- 第 4 题｜体检去哪做
  用户答案：联网医院或车管所自助体检机。
  系统命中：PHYSICAL:WHERE　检索依据：mps_convenience_measures
- 第 5 题｜科目一怎么约考
  用户答案：交管12123上预约。
  系统命中：EXAM:YUEKAO_GAP　检索依据：mps_convenience_measures
- 第 6 题｜科一多少分及格
  用户答案：90分合格。
  系统命中：EXAM:PASS_S1　检索依据：driving_work_standard
- 第 8 题｜科二考哪些
  用户答案：倒车入库、侧方停车、坡道定点、直角转弯、曲线行驶。
  系统命中：EXAM:S2_CONTENT　检索依据：driving_exam
- 第 10 题｜科二没过多久再约
  用户答案：10天后。
  系统命中：EXAM:TIMES　检索依据：road_traffic_safety_regulation
- 第 11 题｜科三考什么
  用户答案：实际道路驾驶技能。
  系统命中：EXAM:S3_CONTENT　检索依据：exam_acceptance_standards
- 第 14 题｜学习驾驶证明有效期
  用户答案：3年。
  系统命中：STUDY_CERT　检索依据：driving_exam
- 第 22 题｜实习期能增驾吗
  用户答案：不能。
  系统命中：INTERNSHIP:ADD　检索依据：driving_license
- 第 23 题｜增驾要重考科一吗
  用户答案：一般要考科一和相应科目。
  系统命中：EXAM:TIMES_K1　检索依据：mps_convenience_measures
- 第 33 题｜C1增驾摩托哪个车型
  用户答案：D、E、F。
  系统命中：ADD:MOTO　检索依据：driving_license
- 第 39 题｜换证要体检吗
  用户答案：要，联网医院出证明。
  系统命中：RENEW:MEDICAL　检索依据：mps_convenience_measures
- 第 45 题｜换证能网上办吗
  用户答案：能，12123。
  系统命中：RENEW:BEFORE_90　检索依据：mps_convenience_measures
- 第 48 题｜转入和期满换证一起
  用户答案：能合并办。
  系统命中：RENEW:TRANSFER　检索依据：driving_work_standard
- 第 49 题｜没到期能转入吗
  用户答案：能。
  系统命中：RENEW:TRANSFER　检索依据：vehicle_registration
- 第 50 题｜转入要体检吗
  用户答案：未期满一般不用。
  系统命中：RENEW:TRANSFER　检索依据：driving_work_standard
- 第 51 题｜换证照片啥要求
  用户答案：1寸白底免冠近期彩照。
  系统命中：RENEW:BEFORE_90　检索依据：mps_convenience_measures
- 第 52 题｜换证多少钱
  用户答案：工本费约10元。
  系统命中：RENEW:BEFORE_90　检索依据：road_traffic_safety_law
- 第 54 题｜70岁以上换证
  用户答案：要先过"三力测试"。
  系统命中：RENEW:AGE70　检索依据：mps_convenience_measures
- 第 56 题｜哪些人要审验
  用户答案：A/B证有记分的、校车、事故致死担责的。
  系统命中：REVIEW:LIST　检索依据：driving_license
- 第 57 题｜A/B没扣分要审验吗
  用户答案：不用，免审。
  系统命中：EDGE:REVIEW_NO_POINT　检索依据：exam_acceptance_standards
- 第 58 题｜审验学多久
  用户答案：不少于3小时。
  系统命中：REVIEW:EDU　检索依据：driving_license
- 第 61 题｜记分周期多久
  用户答案：12个月，从初次领证日起算。
  系统命中：PERIOD　检索依据：traffic_violations
- 第 62 题｜记满12分怎么办
  用户答案：参加满分学习考试。
  系统命中：FULL_SCORE　检索依据：traffic_violations
- 第 63 题｜满分学几天
  用户答案：7天（大车A/B为30天）。
  系统命中：FULL_SCORE　检索依据：traffic_violations
- 第 64 题｜满分考什么
  用户答案：科目一。
  系统命中：FULL_SCORE　检索依据：driving_exam
- 第 65 题｜实习期记满12分
  用户答案：注销实习准驾车型。
  系统命中：EDGE:INTERN_FULL12　检索依据：driving_work_standard
- 第 66 题｜学法减分是啥
  用户答案：学习考试把已扣的分补回来。
  系统命中：SCORE:STUDY_REDUCE　检索依据：traffic_violations
- 第 69 题｜满分了还能减吗
  用户答案：不能，直接满分学习。
  系统命中：FULL_SCORE　检索依据：mps_convenience_measures
- 第 71 题｜减分考几道题
  用户答案：20道，答对18道合格。
  系统命中：SCORE:STUDY_REDUCE　检索依据：traffic_violations
- 第 75 题｜过期一年多还能换吗
  用户答案：考科一恢复。
  系统命中：RENEW:OVERDUE_1_3　检索依据：mps_convenience_measures
- 第 77 题｜过期超三年
  用户答案：注销，得重新考。
  系统命中：RENEW:OVERDUE_GE3　检索依据：mps_convenience_measures
- 第 78 题｜什么是"锁定"
  用户答案：有未处理事项导致暂不能用。
  系统命中：LOCKED　检索依据：mps_convenience_measures
- 第 79 题｜注销和吊销区别
  用户答案：注销可重考，吊销是处罚、有年限限制。
  系统命中：REVOKE:TABLE　检索依据：mps_convenience_measures
- 第 82 题｜70岁能考小车吗
  用户答案：能，先过三力测试。
  系统命中：AGE70_APPLY　检索依据：mps_convenience_measures
- 第 85 题｜出国/服兵役换证
  用户答案：可申请延期。
  系统命中：RENEW:BEFORE_90　检索依据：driving_license
- 第 86 题｜能延期多久
  用户答案：最长3年。
  系统命中：RENEW:DELAY_TWO　检索依据：driving_license
- 第 89 题｜军人驾驶证怎么换
  用户答案：按当地规定办理。
  系统命中：FOREIGN:MILITARY　检索依据：mps_convenience_measures
- 第 102 题｜保留原号条件
  用户答案：用满一年，注销/转让后保留两年。
  系统命中：REG:KEEP_PLATE　检索依据：mps_convenience_measures
- 第 109 题｜二手车怎么过户
  用户答案：交易市场开票，车管所查验办转让登记。
  系统命中：VEHICLE:TRANSFER　检索依据：mps_convenience_measures
- 第 111 题｜过户前要处理违章吗
  用户答案：要。
  系统命中：VEHICLE:TRANSFER　检索依据：mps_convenience_measures
- 第 114 题｜什么是转籍
  用户答案：车辆档案从一地转到另一地。
  系统命中：VEHICLE:RELOCATE　检索依据：mps_convenience_measures
- 第 115 题｜转入要什么
  用户答案：身份证、登记证、交验车。
  系统命中：RENEW:TRANSFER　检索依据：driving_work_standard
- 第 117 题｜名下两车能互换号牌吗
  用户答案：同一所有人、同辖区、同号牌种类、非营运。
  系统命中：REG:PLATE_SWAP　检索依据：mps_convenience_measures
- 第 120 题｜过户后原号能留吗
  用户答案：原车主符合条件可保留。
  系统命中：VEHICLE:TRANSFER　检索依据：mps_convenience_measures
- 第 121 题｜登记证丢了能过户吗
  用户答案：先补领。
  系统命中：VEHICLE:TRANSFER　检索依据：road_traffic_safety_regulation
- 第 122 题｜二手车发票怎么开
  用户答案：交易市场开具。
  系统命中：VEHICLE:TRANSFER　检索依据：vehicle_registration
- 第 123 题｜抵押车能过户吗
  用户答案：原车主、现车主、抵押权人共同申请。
  系统命中：VEHICLE:TRANSFER_MORTGAGE　检索依据：vehicle_registration
- 第 124 题｜过户多久办完
  用户答案：受理后1日内。
  系统命中：VEHICLE:TRANSFER　检索依据：vehicle_registration
- 第 127 题｜换发动机办啥
  用户答案：申请变更登记。
  系统命中：REG:CHANGE　检索依据：vehicle_registration
- 第 128 题｜换车身车架
  用户答案：要备案。
  系统命中：REG:CHANGE　检索依据：vehicle_inspection_work
- 第 135 题｜改装被罚还能备案
  用户答案：先处理，符合规定才可。
  系统命中：REG:CHANGE　检索依据：vehicle_inspection_current
- 第 139 题｜私家车多久年检
  用户答案：10年内第6、第10年上线；10年以上每年。
  系统命中：VEHICLE:CYCLE　检索依据：vehicle_inspection_current
- 第 140 题｜新车6年内上线吗
  用户答案：不用，每两年领标。
  系统命中：VEHICLE:CYCLE　检索依据：mps_convenience_measures
- 第 143 题｜免检车怎么领标
  用户答案：12123申领检验标志。
  系统命中：VEHICLE:EXEMPT　检索依据：vehicle_inspection_current
- 第 144 题｜免检就啥都不用管
  用户答案：不行，要有交强险、处理违章。
  系统命中：VEHICLE:EXEMPT　检索依据：vehicle_inspection_current
- 第 145 题｜哪些车不免检
  用户答案：事故致死、非法改装、营运转非、面包车等。
  系统命中：VEHICLE:EXEMPT　检索依据：vehicle_inspection
- 第 147 题｜面包车能免检吗
  用户答案：不能。
  系统命中：VEHICLE:EXEMPT_NO　检索依据：vehicle_inspection_current
- 第 148 题｜检验标丢了
  用户答案：电子标同等效力，12123申领。
  系统命中：VEHICLE:CYCLE　检索依据：vehicle_inspection_current
- 第 150 题｜年检过期了
  用户答案：尽快补检，期间别上路。
  系统命中：VEHICLE:OVERDUE　检索依据：vehicle_inspection_current
- 第 151 题｜摩托车多久年检
  用户答案：参照第6、10年规定。
  系统命中：VEHICLE:CYCLE　检索依据：vehicle_inspection_current
- 第 152 题｜新能源车年检
  用户答案：同燃油车。
  系统命中：VEHICLE:CYCLE　检索依据：vehicle_inspection_work
- 第 153 题｜能预约年检吗
  用户答案：12123预约检验。
  系统命中：VEHICLE:CYCLE　检索依据：mps_convenience_measures
- 第 154 题｜异地能年检吗
  用户答案：能。
  系统命中：VEHICLE:CROSS_INSPECT　检索依据：vehicle_inspection_current
- 第 158 题｜什么是注销登记
  用户答案：报废、灭失等注销车辆。
  系统命中：CANCEL:LIST　检索依据：vehicle_registration
- 第 159 题｜达到报废标准
  用户答案：交售给回收企业。
  系统命中：REG:SCRAP　检索依据：road_traffic_safety_regulation
- 第 161 题｜异地报废怎么注销
  用户答案：报废地传信息给登记地注销。
  系统命中：REG:SCRAP　检索依据：vehicle_registration
- 第 162 题｜摩托报废怎么销户
  用户答案：交回收企业拆解报废。
  系统命中：REG:SCRAP　检索依据：vehicle_registration
- 第 163 题｜自愿报废
  用户答案：申请注销登记。
  系统命中：REG:SCRAP　检索依据：vehicle_registration
- 第 166 题｜想保留原号注意啥
  用户答案：注销前处理完违章。
  系统命中：REG:KEEP_PLATE　检索依据：mps_convenience_measures
- 第 167 题｜注销多久
  用户答案：1日内。
  系统命中：RECOVER:WINDOW　检索依据：driving_work_standard
- 第 168 题｜报废要开车去吗
  用户答案：交售回收企业，不用开到车管所。
  系统命中：REG:SCRAP　检索依据：mps_convenience_measures
- 第 169 题｜电动车怎么上牌
  用户答案：交验车，带身份证明等。
  系统命中：VEHICLE:CATEGORY　检索依据：vehicle_registration
- 第 176 题｜电动摩托要驾照吗
  用户答案：属机动车，要E/F证。
  系统命中：VEHICLE:CATEGORY　检索依据：colloquial_terms
- 第 182 题｜二手车没来历证明
  用户答案：可由街道/派出所出具合法证明。
  系统命中：VEHICLE:TRANSFER　检索依据：vehicle_registration
- 第 193 题｜什么是快处快赔
  用户答案：轻微事故快速处理理赔。
  系统命中：ACCIDENT:NO_SETTLE　检索依据：mps_convenience_measures

## 剩余候选分批计划（2026-11-19）

前面「法规有据-可补候选」共 88 条，其中 18 条已处理（第 9/15/16/17 取消预约、28 降级、29/30/31 变更考试地、36 申请体检、37/38/40/42 期满换证、43/44/90 补证、76 过期两年、80 身体条件变化），剩余 73 条按主题分成 10 批。每批单独实测、单独修一类、单独跑回归，不跨批混改。

第一批 A · 报名与异地报考、准驾增驾（7 条）：第 2、3、12、20、25、34、35 题。已知第 20 题「外地能考驾照吗」被变更考试地入口抢答，本批先修这一类。进度：第 20 题这一类已修（98ccd73）；第 34/35 题增驾口语这一类已修（8048e50），其余 4 条待处理。

第二批 B · 审验、记分与学法减分（5 条）：第 55、67、70、73、74 题。

第三批 C · 证件与考试监管（6 条）：第 46、72、81、83、84、88 题。第 83/84 题三力测试的「20 题、20 分钟」知识库无此数字，只核定义，不补数字。

第四批 D · 新车上牌、选号与临牌（13 条）：第 91、92、93、95、96、97、99、100、101、103、104、105、107 题。第 93 题收费金额、第 99 题自编 50 次查无原文，不入库。

第五批 E · 转移与变更登记（14 条）：第 110、112、113、118、119、125、126、129、130、132、133、134、136、137 题。第 126 题台账旧判「应为 30 日」是错的，164 号令第十六条写「变更后 10 日内」，按原文不按台账改。

第六批 F · 年检与检验（4 条）：第 141、142、146、149 题。

第七批 G · 解押与注销（7 条）：第 155、156、157、164、165、170、172 题。

第八批 H · 电动车与摩托车（8 条）：第 173、174、175、177、178、179、180、181 题。

第九批 I · 违法处理、备案非本人车、挪车与快处（5 条）：第 183、185、188、192、194 题。第 188 题「每天 1 辆、每年 5 次」查无原文，不入库。

第十批 J · 身份材料与委托代办（4 条）：第 196、197、198、199 题。

每批统一纪律：先把该批问句逐条实测事实层与端到端，区分「答案有但入口接不住」和「真知识缺口」；前者只改公共判据和让位守卫，后者才考虑加表行，加表行前先查重并核原文逐字依据。没有统一法条原文的操作类与数字，按 6.3 禁区只留台账不入库。每批结束登记入口词三样、补断言、跑一键回归、更新修改记录。
