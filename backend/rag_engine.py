"""
RAG问答引擎 —— 检索增强生成 + 确定性核对
四道防线防止编造：
0. 确定性事实层：命中即查表直答，不经 LLM（见 fact_table）
1. 检索覆盖：top_k=5 + 单片段截断 600 字，控制 prompt 在 3000 字量级
2. 来源强制：每个事实性陈述必须标注[片段X]
3. 数字核对：纯代码检查答案中的法规数字是否在上下文中出现（不调 LLM）
4. 确定性护栏：正则纠正车型/色盲/记分/吊销注销等硬错误

⏱ 性能红线：云托管 callContainer 超时 15s，本引擎端到端必须 < 12s。
   历史实现串行调两次 LLM，实测 8.6~19.0s，已移除第二遍 LLM 验证。
"""
from typing import Dict, List, Optional
import re
import time
import concurrent.futures
import openai
from vector_store import VectorStore

# LLM 调用的硬超时执行器。
# ⚠️ 不能用 openai SDK 的 timeout 参数来控总时长：它是 httpx 的 read timeout，
# 语义是「两次数据块之间的间隔」，服务端缓慢但持续吐字时可以远超设定值
# （实测设 9.9s、实际 16.7s）。只有 future.result(timeout=) 才是真正的墙钟硬超时。
_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8,
                                                      thread_name_prefix="llm")


class RAGEngine:
    """
    RAG 问答引擎，流程：
    1. 检索 top_k=5 个最相关片段（每片段截断 600 字）
    2. 单遍生成：基于上下文生成答案（必须标注来源）
    3. 数字核对：纯代码校验，不调 LLM
    4. 确定性护栏：正则纠正法规硬错误
    """

    def __init__(self, vector_store: VectorStore, api_base: str, api_key: str, model: str = "glm-4-flash"):
        self.vector_store = vector_store
        self.model = model

        # 生成侧 key 缺失时不在这里抛错：检索（嵌入 key）和生成（模型 key）是两套凭据，
        # 一个缺失不应该让整个引擎起不来。缺 key 时 client=None，生成阶段抛
        # RuntimeError，由 query() 统一降级为「检索原文」，服务仍然可用、可诊断。
        # 历史实现是在 init_engine 里直接 return False，结果：一个变量填错，
        # /health 只说「缺少生成侧 API_KEY」，连向量库有没有加载成功都看不到。
        self.client = None
        self.gen_ready = False
        if api_key:
            self.client = openai.OpenAI(
                base_url=api_base,
                api_key=api_key,
                timeout=30.0,  # 兜底上限；有预算控制的调用会用 with_options 覆盖成更小的值
                max_retries=1,
            )
            self.gen_ready = True

        import datetime
        today = datetime.date.today().strftime("%Y年%m月%d日")

        # ── 紧凑型系统提示词 ──
        # 将19条冗长规则精简为结构化速查表，Qwen3-8B更容易遵循
        self.system_prompt_template = f"""你是车驾管业务问答助手。当前日期：{today}。

【核心原则】
1. 只能基于下方"知识库内容"回答。知识库没有的信息，回答："根据目前知识库，该问题暂无准确信息，请咨询当地车管所或交管部门。"
2. 每个事实性陈述必须基于知识库原文，不可编造。回答中不要出现[片段X]标记，直接给出简洁答案。
3. 不同车型标准不同，严格按用户问的车型对照，不可跨车型套用。
4. **引用条款号只能用知识库片段里出现过的**。片段里没写到的条号一律不写——宁可只说法规名称、不写条号，也不要编一个像样的条号。

【分项条文必须逐项对照 —— 违反即错误】
- 条文里带（一）（二）（三），或片段末尾标了「含 N 个并列分项」的，**每一项是独立的一套要件**，不是同一件事的不同说法。
- 必须先判断用户情形**是否完整满足某一项的全部要件**（责任等级、人数、金额、车型等），再说该项结论；只满足部分要件的，明确说"不落入本项"。
- ⚠️ 严禁把几个分项的共同结果（如"处三年以下有期徒刑或者拘役"）拎出来，配上其中某一项的条件做概括。这正是最常错的地方：
  《交通肇事解释》第二条（一）是"死亡一人或重伤三人以上，**负全部或者主要责任**"，第二条（二）是"**死亡三人以上**，负同等责任"。
  因此"负同等责任致一人死亡"**不构成交通肇事罪**，只能说成"情形的分界在人数和责任等级上"，绝不能答成"负同等以上责任致人死亡即处三年以下"。
- 用户问的是区间（如"同等以上责任""超员多少"）时，把区间覆盖的每种情形**分开说，一句一项**，不要合并成一个结论。

【车型代码速查】
A1=大型客车 A2=重型牵引挂车 A3=城市公交车 B1=中型客车 B2=大型货车
C1=小型汽车 C2=小型自动挡汽车 C3=低速载货汽车 C4=三轮汽车 C5=残疾人专用小型自动挡载客汽车 C6=轻型牵引挂车
D=普通三轮摩托车 E=普通二轮摩托车 F=轻便摩托车 M=轮式专用机械车 N=无轨电车 P=有轨电车

【年龄要求速查】(172号令第十四条)
A1=22-63岁 A2=22-63岁 A3=20-63岁 B1=20-63岁 B2=20-63岁
C1/C2=18岁以上无上限(70岁以上需通过三力测试) C6=20-70岁 D/E=18-70岁 F=18岁以上无上限

【关键禁止事项 —— 违反即错误】
- 辨色力：所有车型统一要求"无红绿色盲"，无例外。不可与听力条款混淆（听力允许戴助听器≠辨色力允许红绿色盲）
  第十四条身体条件第3项原文仅一句"辨色力：无红绿色盲"，**没有任何车型限定**，对A1-P全部准驾车型一律适用。
  ⚠️ 严禁回答"部分车型（如C1/C2）没有明确要求无红绿色盲""具体要求因地区而异"——这是错误的！
- B2=大型货车，任何时候都不能称为"中型客车"（B1才是中型客车）
- C1准驾范围：C2/C3/C4。不含C6/摩托车(D/E/F)/中型客车(B1)/大型货车(B2)
- 实习期（第七十六条）：初次取得**汽车类**或初次取得**摩托车类**准驾车型后的12个月为实习期。
  · **同大类增驾不单设实习期**：如C1增驾B2/A1、E增驾D（同属汽车类或同属摩托车类）→ 无新实习期。
  · **跨大类增驾有12个月实习期**：持汽车类增驾摩托车类（如C1增驾D），或持摩托车类增驾汽车类（如E增驾C1）。
  ⚠️ 严禁回答"增驾后一律有12个月实习期"（错），也严禁回答"增驾一律没有实习期"（错）。必须先判断是否跨越汽车类/摩托车类。
  依据：172号令第七十六条已删除139号令"增加准驾车型后的12个月"表述；第七十七条第三款"在增加准驾车型后的实习期内，驾驶原准驾车型的机动车时不受上述限制"，指的正是跨大类增驾产生的实习期。
- 【吊销 ≠ 注销，两者是不可互换的两套制度，严禁混答】(172号令第十五条 / 第七十九条)
  · 醉驾（非营运）→ 吊销驾驶证，**5年内不得重新取得**；醉驾营运机动车 → **10年**；
    饮酒后/醉酒驾驶发生重大交通事故构成犯罪、肇事逃逸构成犯罪 → **终生禁驾**。
  · 吊销后的正确路径是：**禁驾期满后按初次申领重新报考，重考科目一至科目四全部科目**。
  · 「注销未超过2年、考科目一合格后恢复驾驶资格」（第七十九条第八项）**只适用于"超过有效期1年以上未换证被注销"**，
    与吊销、酒驾、醉驾完全无关。
  · ⚠️ 严禁把"醉驾被吊销后多久能再考"答成"2年"或"注销未超过2年可以恢复驾驶资格"——这是把两条不同制度缝在一起，属硬错误。
- 概括性回答中A1/A2年龄为22-63岁，不能说"A1/A2是20-63岁"（A3/B1/B2才是20-63岁）。即便在概括AB类车型时，也必须将A1/A2的22-63岁和A3/B1/B2的20-63岁分开说明，不能合并。
  ⚠️ 错误示例："申请A1、A2、A3、B1、B2等车型的，年龄需在20-63岁之间"←这是错的！
  正确示例："A1、A2为22-63岁；A3、B1、B2为20-63岁"
- 增驾A1有四条路径：A3满2年、B1满2年、B2满3年、A2满1年。回答时不能遗漏任何一条，尤其不能遗漏A2满1年这条路径。A2增驾A1是满1年，不是满2年。
  ⚠️ 错误示例："A2满2年可增驾A1"←这是错的！A2满1年即可增驾A1。

【增驾路径速查】(172号令第十六/十七条)
- C1增驾B1：持C1满2年，近2周期无满分，B1身高150cm以上
- C1增驾B2：无持证年限要求，近1周期无满分（B2可初次申领）
- C1增驾C6：持C1满1年，近1周期无满分
- C1不能直接增驾A1/A2（需要先增驾到B1或B2）
- 增驾A1路径：A3/B1满2年 或 B2满3年 或 A2满1年，近3周期无满分
- 增驾A2路径：B1/B2满2年 或 A1满1年，近2周期无满分
- 只能通过增驾获得的车型：A1、A2、B1、C6（共4种）——A3/B2可初次申领，不属于只能增驾
  ⚠️ 172号令第十六条初次申领可申请的车型为A3、B2、C1、C2、C3、C4、C5、D、E、F、M、N、P，不含A1、A2、B1、C6

【考试科目速查】
- C6考试：科目二 + 科目三安全文明驾驶常识（不含科目三道路驾驶技能）
- 科目三 = 道路驾驶技能 + 安全文明驾驶常识（两者独立，不可混淆）

【摩托车增驾速查】(172号令第十六、十七条) —— 最高优先级法规，绝对正确，必须严格遵守；即使知识库片段的表面表述看似冲突，也以此速查为准：
- D（普通三轮摩托车）、E（普通二轮摩托车）、F（轻便摩托车）既可初次申领，也可增驾，不属于"只能通过增驾获得"的车型
- 已持有任何准驾车型驾驶证（包括B2大型货车、C1小型汽车等）的人，均可申请增驾摩托车D/E/F，**没有持证年限要求**
- 增驾摩托车的唯一条件：本记分周期和申请前最近1个记分周期内没有记满12分记录
- ⚠️ 严禁回答"B2/C1等不能增驾摩托车"或"B2不允许直接增驾摩托车"——这是错误的！持B2增驾摩托车完全合法
- 增驾考试：已持F申请增驾D或E、或已持E申请增驾D的，考试科目二和科目三；其他组合按初次申领考试科目执行

【审验速查】(172号令第七十二条)
AB类驾驶证(A1/A2/A3/B1/B2)审验：记分周期结束后30日内审验，无记分免审，有记分需参加3小时学习

【记分速查】(163号令第八～十二条) —— 问"记几分"必须精确到分值，不可含糊、不可说"未提及"
一次记12分（第八条）：饮酒后驾驶；致人轻伤以上或死亡事故后逃逸(不构成犯罪)；使用伪造变造号牌/行驶证/驾驶证/校车标牌或使用他车号牌行驶证；校车/公路客运/旅游客运超员20%以上，或其他载客汽车超员100%以上；校车/中型以上载客载货/危险品车在高速快速路超速20%以上，或其他车在高速快速路超速50%以上；在高速/快速路倒车、逆行、穿越中央分隔带掉头；**代替实际驾驶人接受处罚和记分牟取经济利益**。
一次记9分（第九条）：7座以上载客汽车超员50%以上未达100%；校车/中型以上载客载货/危险品车在**非高速**道路超速50%以上；**在高速公路或城市快速路上违法停车**；未悬挂号牌或故意遮挡、污损号牌；**驾驶与准驾车型不符的机动车**；未取得校车驾驶资格驾驶校车；连续驾驶中型以上载客汽车/危险品车超4小时未停车休息或休息少于20分钟。
一次记6分（第十条）：含"在高速公路或城市快速路上**违法占用应急车道行驶**"等。
一次记3分、1分见第十一条、第十二条。

⚠️ 三组最易混淆（必须区分行为性质，不可混答）：
- 高速/快速路**违法停车** = 9分（第九条第三项）；高速/快速路**违法占用应急车道行驶** = 6分（第十条第十一项）。"停车"与"行驶"行为不同，分值不同。
- **驾驶与准驾车型不符** = 9分（第九条第五项），不是12分。
- **代替他人接受处罚和记分牟利** = 记12分（第八条第七项）。知识库同时有罚款条款（处违法所得三倍以下罚款、最高5万元），但**记分与罚款并存**，不可因看到罚款就说"未提及记分"。

【身体条件完整性要求】
- 单眼视力障碍符合条件可申请的车型为**5种**：C1、C2、C3、C4、C5，回答时不得只列C1/C2。
- 70周岁以上可申请车型为**4种**：C1、C2、C5、F，且**必须同时说明"需通过记忆力、判断力、反应力等能力测试"**，不可省略该前提。
- 听力障碍佩戴助听设备：**仅** C1、C2 两种，不可扩大到C3/C4/C5。
- 准驾车型对照（附件1）：A2准予驾驶的其他车型含 **C6**，故**持A2可直接驾驶C6，无需再增驾C6**；A1不含C6（A1准予驾驶A3、B1、B2、C1、C2、C3、C4、M）。

【恢复驾驶资格】(172号令第七十九条)
驾驶证因"超过有效期1年以上未换证"被注销(第八项)，注销未超过2年的，参加道路交通安全法律法规和相关知识考试(科目一)合格后可恢复驾驶资格 → 引条款应写**第七十九条**，不是第二十七条(第二十七条是"直接申请考试"，非"恢复驾驶资格")。

=== 知识库内容 ===
{{context}}
=== 知识库内容结束 ===

=== 输出要求（硬约束） ===
- **总字数不超过 400 字**，直接给结论，不铺垫、不复述问题、不写"根据知识库"。
- 用户一次问多件事，或问题落在一个区间/多个分项上时，用 1./2. 分点，每点一句话说清"什么情形 → 什么结论 + 依据条款号"。
- 一种违法行为的处罚常常是多项并列（罚款、拘留、记分、吊销、禁驾），知识库片段里出现的都要列出，不要只答其中一项。
- 引用写到"《XX规定》第X条"即可，不要抄原文；原文交给下方来源列表。
- 宁可少说，不要为了完整而堆砌——超长回答会让用户在手机上读不完；但**分项的每种情形都不能省**，省掉一种就是把结论说错。

请回答用户问题：{{question}}"""

    # ── 并列分项识别 ──
    # 法规里「(一)(二)(三)」每一项是**一套独立要件**，不是同一条件的不同写法。
    # 小模型最常见的走样就是把多个分项的共同结果提取出来、配上其中一个分项的条件，
    # 做成一勺烩的概括（2026-11-04「致人死亡负同等以上责任」那次就是这么错的：
    # 解释第二条(一)要求「死亡一人…负全部或主要责任」，(二)才轮到「死亡三人以上、同等责任」，
    # 模型却合成「同等以上责任致一人死亡→处三年以下」，把不构成犯罪的情形说成了犯罪）。
    # 因而不改原文，只在片段尾部加一条元信息，明确"这是 N 个分项、必须逐项对照"。
    _ENUM_CN_RE = re.compile(r"[（(]\s*([一二三四五六七八九十]{1,3})\s*[）)]")
    _ENUM_DIGIT_RE = re.compile(r"(?m)^\s*(\d{1,2})\s*[.、．]\s*(?=\S)")

    @classmethod
    def _count_enumerations(cls, text: str) -> int:
        """统计片段里的并列分项数（中文括号序号与阿拉伯数字序号取较大者）"""
        cn = {m.group(1) for m in cls._ENUM_CN_RE.finditer(text)}
        dg = {m.group(1) for m in cls._ENUM_DIGIT_RE.finditer(text)}
        # 序号必须成串出现（至少两个不同序号）才算分项，单个「(一)」多半是行内引用
        return max(len(cn) if len(cn) >= 2 else 0, len(dg) if len(dg) >= 2 else 0)

    # 「…情形之一的，处三年以下有期徒刑或者拘役：」这种"共同结论 + 分项条件"的体例。
    # 小模型最容易在这里把共同结论配到某一项的条件上，所以把两者在上下文里拆开说清楚。
    _LEAD_CONCL_RE = re.compile(
        r"(?P<lead>[^\n]{8,200}?)[：:]\s*\n+\s*[（(]\s*[一1]\s*[）)]"
    )
    _CONCL_VERB_RE = re.compile(r"(处|记|罚|吊销|拘留|没收|责令|不得|可以|应当)")

    @classmethod
    def _lead_conclusion(cls, text: str) -> str:
        """抽出「下列情形之一」前面的那句共同结论；抽不到就返回空串"""
        m = cls._LEAD_CONCL_RE.search(text)
        if not m:
            return ""
        lead = m.group("lead").strip()
        # 结论一般是最后一个逗号之后的部分："…情形之一的，处三年以下有期徒刑或者拘役"
        tail = re.split(r"[，,]", lead)[-1].strip()
        if 4 <= len(tail) <= 40 and cls._CONCL_VERB_RE.search(tail):
            return tail
        return ""

    def _format_context(self, search_results: List[Dict], max_chars: int = 600):
        """将检索结果格式化为 prompt 上下文。

        max_chars 单片段截断：法条片段动辄 1500+ 字，8 条拼起来 prompt 超过 1 万字，
        是生成耗时的主因（实测 prompt 10014~13365 字）。截到 600 字并尽量在句子边界
        断开，prompt 压到 3000 字量级，生成耗时随输入 token 数近似线性下降。

        两个针对"分项条文"的例外（分项型条文是答案出错的重灾区，宁可多花几十个 token）：
        1. 分项条文放宽到 900 字，避免截断把 (三)(四) 切掉，让模型以为只有前两项；
        2. 片段尾部加元信息，明确"本条有 N 个分项、须逐项对照"，并在截断时说明还剩几项。

        返回 (context 文本, 分项片段信息列表)。
        """
        context_parts = []
        enum_info = []
        for i, result in enumerate(search_results):
            content = (result.get("content") or "").strip()
            n_enum = self._count_enumerations(content)
            limit = 900 if n_enum >= 2 else max_chars
            shown = n_enum
            truncated = False
            if limit and len(content) > limit:
                head = content[:limit]
                # 优先在句末断开，避免把一条款切成半句导致模型误读
                cut = max(head.rfind("。"), head.rfind("；"), head.rfind("\n"))
                if cut >= int(limit * 0.6):
                    head = head[:cut + 1]
                content = head + "\n…（片段后略）"
                truncated = True
                if n_enum:
                    shown = self._count_enumerations(content)

            source = result["metadata"].get("title", "未知")
            score = f"{result['score']:.2f}"
            block = f"[片段{i+1}] (来源: {source}, 相似度: {score})\n{content}\n"

            if n_enum >= 2:
                meta = f"（元信息：本片段含 {n_enum} 个并列分项，每项要件不同，须逐项对照用户情形，不得合并陈述"
                if truncated and shown < n_enum:
                    meta += f"；此处因长度截断只保留前 {shown} 项，原文共 {n_enum} 项"
                concl = self._lead_conclusion(content)
                if concl:
                    meta += (f"；「{concl}」是这 {n_enum} 项的共同结论，逐项适用，"
                             f"但每一项的前置要件不同，必须先把用户情形对进某一项再说结论")
                meta += "）"
                block += meta + "\n"
                enum_info.append({
                    "idx": i + 1,
                    "title": source,
                    "count": n_enum,
                    "shown": shown,
                    "content": content,
                })
            context_parts.append(block)
        return "\n".join(context_parts), enum_info

    def _call_llm(self, system_prompt: str, user_message: str, max_tokens: int = 900,
                  timeout: float = None) -> str:
        """调用大模型，返回回答文本。

        传了 timeout 时用 with_options 派生一个「不重试 + 限定超时」的子 client：
        - 不重试：这层调用已经被 _call_llm_until 用墙钟卡死，SDK 再重试只会让
          被丢弃的线程多活一轮，把线程池占满，反而拖垮后续用户的请求。
        - 限定超时：让被丢弃的线程在 timeout 内自然结束，不会挂满 120s。
        """
        payload = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.05,  # 极低温度，减少随机性
            max_tokens=max_tokens,
        )
        client = self.client
        if client is None:
            raise RuntimeError("生成侧 API_KEY 未配置，跳过生成直接返回检索原文")
        if timeout is not None:
            client = client.with_options(timeout=max(1.0, timeout), max_retries=0)
        return client.chat.completions.create(**payload).choices[0].message.content.strip()

    def _call_llm_until(self, system_prompt: str, user_message: str,
                        max_tokens: int, deadline: float) -> str:
        """带墙钟硬超时的 LLM 调用：剩余时间不足即抛 TimeoutError。

        线程池里的请求不会因为 future 超时被真正终止（Python 无法强杀线程），
        但它的结果会被丢弃，调用方按预算准时降级。LLM 请求自身也带 timeout，
        泄漏的线程会在最迟 timeout 秒后自然结束，不会无限堆积。
        """
        remaining = deadline - time.time()
        if remaining < 1.5:
            raise TimeoutError(f"预算不足（剩余 {remaining:.1f}s）")
        fut = _LLM_EXECUTOR.submit(self._call_llm, system_prompt, user_message,
                                   max_tokens, remaining)
        try:
            return fut.result(timeout=remaining)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise TimeoutError(f"LLM 超出预算（{remaining:.1f}s 未返回）")

    def query(self, user_question: str, top_k: int = 5, budget: float = 10.0,
              history: Optional[List[Dict]] = None) -> Dict:
        """带多轮历史的查询入口。

        「那 B2 呢」「要多久」这类追问单独拿出来是残句：事实层按关键词查不到
        处理器，检索层也召回不到相关片段，用户体感就是「它记不住上一句」。

        所以先把残句补成独立问句，再交给 _query_impl 走原来的四层。顺序不能反——
        补全放在检索之后没意义，那时检索已经偏了。

        改写成功就拿补全后的句子走四层；改写超时 / 被校验拒收时**不把残句丢给
        检索层**——残句检索必然偏，还会白耗十几秒，改为直接回一段引导话术
        （见 ctx_reason 分支）。宁可少答一次，也不答一件不相干的事。

        预算要扣减：改写花掉的时间从生成预算里出，否则总耗时变成
        「改写 2s + 生成 12s」= 14s，贴着 callContainer 15s 硬上限。
        """
        t0 = time.time()
        effective_q = user_question
        resolved = False
        ctx_reason = ""

        if history:
            try:
                import context_rewrite
                r = context_rewrite.resolve(user_question, history, self.client,
                                            self.model, timeout=3.0)
                if r.get("resolved"):
                    effective_q = r["question"]
                    resolved = True
                    print(f"[RAGEngine] 追问补全 {user_question!r} -> {effective_q!r}"
                          f"（{r.get('sec', 0):.2f}s）")
                elif not r.get("skipped"):
                    # 没被跳过却没成：超时或校验拒收。这句一定要打日志——
                    # 表现上就是「追问没接上」，不打日志根本看不出是被拦还是没触发。
                    ctx_reason = r.get("reason", "")
                    print(f"[RAGEngine] 追问补全未生效：{ctx_reason}")
            except Exception as e:
                ctx_reason = f"{type(e).__name__}: {e}"[:80]
                print(f"[RAGEngine] 追问补全异常（忽略）: {e}")

        # 判定是残句、但补全失败（超时 / 被校验拒收）：
        # 不能把「那 35 分呢」原样丢进检索层。这种句子向量上跟任何法条都不接近，
        # 实测检索出 GA/T 1030 考场验收规范这种完全无关的国标，还耗时 11 秒——
        # 既答错又慢。1 秒内说清楚"没跟上"，让用户补一句，比答一个偏的可控。
        if ctx_reason:
            return {
                "answer": (
                    f"**抱歉，我没跟上你的上一句。**\n\n"
                    f"你这句「{user_question}」单独看缺主语，直接拿去法规库检索会答偏，"
                    f"所以我停在这里没有硬答——答偏比不答更耽误事。\n\n"
                    f"麻烦补全一点再说一次，把「那」「它」换成具体的事：\n"
                    f"- 「那要多久」→ 说成「驾驶证期满换证要多久」\n"
                    f"- 「那 B2 呢」→ 说成「B2 能开什么车」\n\n"
                    f"这样我能直接给你准确流程和依据条文。"
                ),
                "sources": [],
                # 与 timeout / timeout_search 区分开，便于在日志里单独统计
                "degraded": "ctx_unresolved",
                "ctx_reason": ctx_reason,
            }

        # 改写耗时从总预算里扣，留至少 3 秒给生成——低于 3 秒不如直接降级原文
        spent = time.time() - t0
        out = self._query_impl(effective_q, top_k=top_k,
                               budget=max(3.0, budget - spent))
        if resolved:
            # 告诉前端「系统理解成了什么」。前端不渲染也不影响功能，纯诊断与透明用。
            out["resolved_question"] = effective_q
        return out


    def _query_impl(self, effective_q: str, top_k: int = 5, budget: float = 10.0) -> Dict:
        """
        执行 RAG 查询（单遍生成 + 代码核对）
        返回: {"answer": "...", "sources": [...]}

        性能约束：微信云托管 callContainer 的接口超时为 **15 秒硬上限**，
        因此本方法以 budget 为生成预算（由调用方按 QUERY_TIMEOUT 传入，默认 10 秒）。
        超出预算不返回错误，而是降级返回检索到的法规原文——用户总能看到东西。
        历史实现串行调两次 LLM（生成 + 自我验证），实测 8.6~19.0 秒，
        已改为单遍生成 + 确定性代码核对，详见「防线三」。

        架构：确定性事实层优先。
        第 0 道防线：命中 fact_table（记分/年龄/年限/准驾范围等有限枚举事实）
        直接查表返回，完全不经过 LLM，杜绝生成侧走样。
        """
        deadline = time.time() + budget
        # ─── 防线零：确定性事实表直答（零幻觉通道）───
        fact = None
        try:
            import fact_table
            fact = fact_table.lookup(effective_q)
        except Exception as e:
            print(f"[RAGEngine] 事实表查询异常，回退 RAG: {e}")

        if fact:
            print(f"[RAGEngine] 事实表命中 {fact.get('fact')}，进入复核")
            # 事实层答案的数字是代码里写死的常量，理论上不会错，但模板拼接多了
            # 也会串（历史上有过「10 年」写成「5 年」）。这里只做日志自检：
            # 发现对不上就报出来，不改输出——事实层若被降级，等于丢掉最可靠的那条路。
            self._audit_fact_numbers(fact["answer"], fact.get("fact"))

            # ─── 防线零点五：模型复核「是否答错了对象」───
            # 事实层按关键词路由，问句被送错处理器时会用确定性的口吻答出另一个制度，
            # 而答案本身语法、法条、格式全正常（2026-11 事故：问 63 岁 A2 能否延期
            # 继续驾驶，答成第七十五条延期换证）。这种错只能靠语义判断发现。
            verdict = self._review_fact(effective_q, fact, deadline)
            if verdict.get("verdict") == "WRONG":
                # 不重答：实测转检索生成耗时 5~10s，一半超时降级，答案不升反降
                # （tools/exp3_recheck_via_rag.py）。改为退回法规原文，宁可难读不可错答。
                try:
                    sr = self.vector_store.search(effective_q, top_k=3)
                    filtered = [r for r in (sr or []) if r.get("score", 0) >= 0.05]
                except Exception as e:
                    filtered = []
                    print(f"[RAGEngine] 复核拦截后补检索失败({type(e).__name__}: {e})")
                if filtered:
                    out = self._fallback_from_context(filtered, "fact_review")
                    out["fact_hit"] = fact.get("fact")
                    out["fact_review"] = verdict
                    return out
                # 检索也空了：宁可交回原文来源，也不要发一条自己都怀疑的答案
                return {
                    "answer": ("⚠️ 这个问题涉及多项名称相近的制度，系统无法确认哪一项适用于你的情形，"
                               "请补充说明后重试，或咨询当地车管所。\n\n"
                               f"（原候选答案所依据的条款：{'、'.join(fact.get('sources') or []) or '未标注'}）"),
                    "sources": fact["sources"],
                    "fact_hit": fact.get("fact"),
                    "fact_review": verdict,
                    "degraded": True,
                }

            out = {
                "answer": fact["answer"],
                "sources": fact["sources"],
                "fact_hit": fact.get("fact"),
            }
            # 复核结论一并回传，便于线上观测「这道题复核跑没跑、判了什么」，
            # 前端不读这个字段，纯诊断用。
            if verdict.get("verdict"):
                out["fact_review"] = {"verdict": verdict["verdict"],
                                      "reason": verdict.get("reason", "")[:120]}
            if verdict.get("signals"):
                # 信号只用于观测：事实层没错时它们不该影响输出
                out["fact_signals"] = verdict["signals"]
            return out

        # ─── 防线一之前：口语 → 法规术语改写 ───
        # 知识库是法规原文用语，用户说的是车圈口语（转籍/过户/解押/滞纳金），
        # 字面不重叠会导致漏召回，这里补上正式术语再检索。
        search_query = effective_q
        try:
            import glossary
            search_query, gloss_hits = glossary.expand(effective_q)
            if gloss_hits:
                print(f"[RAGEngine] 术语改写命中 {gloss_hits}")
        except Exception as e:
            print(f"[RAGEngine] 术语改写跳过（不影响检索）: {e}")

        # ─── 防线一：检索更多上下文 ───
        search_results = self.vector_store.search(search_query, top_k=top_k)

        if not search_results:
            return {
                "answer": "未找到相关知识库，请先构建知识库。",
                "sources": []
            }

        filtered = [r for r in search_results if r["score"] >= 0.05]
        if not filtered:
            return {
                "answer": "根据目前知识库，该问题暂无准确信息，请咨询当地车管所或交管部门。",
                "sources": search_results
            }

        context, enum_info = self._format_context(filtered)

        # ─── 防线一点五：把确定性事实注入上下文 ───
        # 长问题/多诉求场景不走直答，但把已核对的事实作为最高优先级依据塞进去，
        # 避免模型在长上下文里被相似但不适用的片段带偏。
        try:
            import fact_table
            hint = fact_table.build_fact_hint(effective_q)
            if hint:
                context = hint + "\n\n" + context
                print("[RAGEngine] 已注入确定性事实作为权威依据")
        except Exception as e:
            print(f"[RAGEngine] 事实提示注入失败（忽略）: {e}")

        # ─── 防线二：第一遍生成 —— 强制标注来源 ───
        gen_prompt = self.system_prompt_template.replace("{context}", context).replace("{question}", effective_q)

        try:
            draft_answer = self._call_llm_until(gen_prompt, effective_q,
                                                max_tokens=750, deadline=deadline)
        except TimeoutError as e:
            print(f"[RAGEngine] 生成超时（{e}），降级为检索原文")
            return self._fallback_from_context(filtered, "budget")
        except Exception as e:
            print(f"[RAGEngine] 生成阶段失败({type(e).__name__}: {e})，降级为检索原文")
            return self._fallback_from_context(filtered, "llm_error")

        # ─── 防线三：确定性数字核对（替代原「第二遍 LLM 自我验证」）───
        # 原实现：先调 LLM 生成，再把「草稿 + 全部片段 + 约束」喂给 LLM 做第二遍核查。
        # 两次调用串行，实测单题 8.57s / 18.96s，其中第 2 遍独占 1.64~7.21s，
        # 必然击穿 callContainer 的 15s 硬上限。
        #
        # 2026-11-04 二次改造：原实现拿答案去**本次检索片段**里找依据，片段被截断到
        # 900 字，原文数字常在截断外 → 正确答案也查不到 → 只能放宽到「3 个以上且
        # 六成无据才提示」，于是单个数字写错（500 元写成 200 元）永远拦不住。
        # 现改为对**知识库全库数值索引**核对，且区分量纲（金额/期限/记分/百分比），
        # 依据不再受检索切分影响，单个数写错就能抓到。
        verified_answer, num_degrade = self._number_gate(draft_answer)
        if num_degrade:
            return self._fallback_from_context(filtered, "number")

        # 清理重复的"最终答案："标记（历史上验证环节会带出，保留以防模型自发生成）
        verified_answer = re.sub(r'\n+\s*最终答案\s*[:：]\s*', '\n\n', verified_answer)
        # 去掉重复/复述段落（模型偶发把结论换个说法再说一遍）
        verified_answer = self._dedupe_paragraphs(verified_answer)

        # 如果模型自发生成"【验证通过】"前缀，去掉
        if verified_answer.startswith("【验证通过】"):
            verified_answer = verified_answer.replace("【验证通过】", "", 1).strip()

        # 清理所有[片段X]标记，确保面向用户输出干净
        # 覆盖各种括号变体：[]、【】、［］、()、（）等
        verified_answer = re.sub(r'[\[［【\(（]?\s*片段\s*\d+\s*[\]］】\)）]?', '', verified_answer)
        verified_answer = re.sub(r'\(来源:.*?\)', '', verified_answer)
        verified_answer = re.sub(r'（来源:.*?）', '', verified_answer)
        # 清理因删除标记产生的多余空白和空行
        verified_answer = re.sub(r'[ \t]+', ' ', verified_answer)
        verified_answer = re.sub(r'\n{3,}', '\n\n', verified_answer)
        verified_answer = verified_answer.strip()

        # ─── 防线四：确定性护栏（正则后处理）───
        # 弥补小模型在长约束 prompt 下偶尔走神，对法规硬错误做强制纠正
        verified_answer = self._apply_guardrails(verified_answer, effective_q)

        # ─── 防线五：法条引用对账（编造条号的最后一道闸门）───
        # 前面四道防线都在管"说了什么"，这道管"凭什么是这么说的"。
        verified_answer, degrade = self._citation_gate(verified_answer, context, filtered)
        if degrade:
            return self._fallback_from_context(filtered, "citation")

        return {
            "answer": verified_answer,
            "sources": filtered
        }

    # 引用被摘除后用来占位的说法，保证句子读起来仍然通顺
    _CITE_PLACEHOLDER = "相关规定"

    def _citation_gate(self, answer: str, context: str, filtered: List[Dict]):
        """法条引用对账。返回 (处置后的答案, 是否需要整体降级为检索原文)。

        处置分三档，按引用编造的严重程度递进：
          1. 单条编造（条号不存在 / 法规不在库）→ 就地摘除该引用标注，末尾说明已摘除
          2. 大面积编造（≥2 处且占全部引用一半以上）→ 整段论述的引用基础不可信，
             不再修补，直接降级为检索原文。修补一处、留下九处，比不修更危险。
          3. 条号真实但本次未检索到 → 保留字样，末尾统一提示"请以来源原文为准"

        为什么不能只靠 prompt：模型编条号的成本远低于承认"知识库没有"，
        而用户无法分辨「第九十一条」和「第一百二十五条」哪个是真的。
        """
        try:
            import citation_guard
        except Exception as e:
            print(f"[RAGEngine] 引用对账模块不可用（跳过）: {e}")
            return answer, False

        try:
            res = citation_guard.check(answer, context)
        except Exception as e:
            print(f"[RAGEngine] 引用对账异常（跳过）: {e}")
            return answer, False

        bad = res["bad"]
        total = res["total"]
        # 降级门槛：至少 2 处编造，且编造数多于可信数。
        # 只按比例会误伤"1 处编造 + 1 处正常"这种还有半边依据可用的答案——
        # 那种情况摘掉一处就够了，整段丢掉等于把有效信息也一起扔了。
        if len(bad) >= 2 and len(bad) > (total - len(bad)):
            print(f"[RAGEngine] 引用对账：{len(bad)}/{total} 处引用编造，整段不可信 -> 降级返回原文")
            return answer, True

        if bad:
            print(f"[RAGEngine] 引用对账：摘除 {len(bad)} 处编造引用 -> "
                  f"{[c.get('label') or c['law_raw'] for c in bad]}")
            for c in bad:
                raw = c["raw"]
                if not raw or raw not in answer:
                    continue
                if c["status"] == "no_article":
                    repl = f"《{c['doc']['name']}》{self._CITE_PLACEHOLDER}"
                else:
                    repl = self._CITE_PLACEHOLDER
                answer = answer.replace(raw, repl, 1)
            note = citation_guard.render_removal_note(bad)
            if note:
                answer += note

        unverified = res["unverified"]
        # 提示的门槛：仅当"有引用却一条都没在片段里对上"时才提示，
        # 否则同一段里正常的引用也会被这句提示拖累成"都不太可靠"。
        if unverified and len(unverified) >= max(1, total):
            note = citation_guard.render_unverified_note(unverified)
            if note:
                answer += note
        return answer, False


    def _fallback_from_context(self, filtered: List[Dict], reason: str = "") -> Dict:
        """生成侧失败/超时的兜底：直接返回检索到的法规原文。

        为什么不是返回「请稍后重试」：用户已经等了 10 秒，再给一句「重试」等于白等。
        检索结果本身（法条原文）就有信息量，标明「未经 AI 整理」即可交付。
        这是机制化兜底——不论 LLM 侧超时、限流还是报错，用户都能拿到东西，
        且响应时间被 deadline 锁死在预算内，不会击穿云托管 15s 上限。
        """
        heads = {
            "budget": "检索已完成，AI 整理超时。以下是为你在法规库中找到的原文：",
            "llm_error": "AI 整理暂时不可用。以下是为你在法规库中找到的原文：",
            "citation": "AI 生成的回答中法条引用与法规库原文对不上，已为你改为直接列出原文：",
            "number": "AI 生成的回答中有多个数值在法规原文里找不到对应，为避免误导，已改为直接列出原文：",
            "fact_review": "AI 复核发现，这条答案讲的可能不是你问的那件事（法规里有多项制度名称相近）。"
                           "为避免答错对象，已改为直接列出相关原文：",
        }
        parts = [f"**{heads.get(reason, '以下是为你在法规库中找到的法规原文：')}**"]
        # 取 3 条而不是 2 条：混合检索的排序在长尾问题上并不总准，
        # 正确答案常落在第 2~3 条，只给前 2 条容易把不相关法条推给用户。
        for r in filtered[:3]:
            meta = r.get("metadata") or {}
            title = meta.get("title") or meta.get("source") or "法规原文"
            text = (r.get("content") or "").strip()
            if len(text) > 450:
                cut = max(text.rfind("。", 0, 450), text.rfind("\n", 0, 450))
                text = (text[:cut + 1] if cut > 250 else text[:450]) + "…"
            parts.append(f"**{title}**\n{text}")
        parts.append("> 以上为检索原文，未经 AI 整理。如需完整解答，可把问题问得更具体，或稍后重试。")
        return {"answer": "\n\n".join(parts), "sources": filtered, "degraded": True}

    def quick_lookup(self, question: str, top_k: int = 3) -> Dict:
        """只检索、不调 LLM。用于上游超时兜底：用户至少能看到法规原文。

        检索本身是毫秒级（实测 0.04~0.5s），因此上游 12 秒保险触发后，
        再花几百毫秒补一次检索，总耗时仍在云托管 15 秒上限之内，
        但用户拿到的是法条原文而不是一句「服务繁忙」。
        """
        try:
            hits = self.vector_store.search(question, top_k=top_k)
        except Exception as e:
            print(f"[RAGEngine] 兜底检索失败: {e}")
            return {}
        filtered = [r for r in hits if r.get("score", 0) >= 0.05]
        if not filtered:
            return {}
        return self._fallback_from_context(filtered, "budget")

    # ── 数字核对 ──
    # 老实现的量纲表与"是否出现在片段里"的判定已整体移到 number_guard（对全库索引核对），
    # 这里只保留处置策略，避免两套口径并存。
    def _review_fact(self, question: str, fact: Dict, deadline: float) -> Dict:
        """对事实层答案做「是否答错了对象」的复核。

        返回 {"verdict": OK/WRONG/SKIP, "reason": ..., "signals": [...], "sec": ...}

        设计取舍：
        - 任何异常都 SKIP 并放行。复核是加分项，失败必须让原答案通过，
          否则一次网络抖动就会把正确答案换成难读的原文。
        - 预算不足 2 秒直接跳过，不冒险让复核吃掉检索时间。
        - 实体信号（问句里的车型/年龄/制度词有没有落到答案里）零成本先算，
          既用于日志观测，也作为线索喂给模型——它是线上事故的强先验。
        """
        try:
            import fact_review
        except Exception as e:
            return {"verdict": "SKIP", "reason": f"复核模块不可用: {e}", "sec": 0.0}

        if not fact_review.enabled():
            return {"verdict": "SKIP", "reason": "复核已关闭（FACT_REVIEW=off）", "sec": 0.0}
        if self.client is None:
            return {"verdict": "SKIP", "reason": "生成侧模型未配置", "sec": 0.0}

        answer = fact.get("answer") or ""
        signals = fact_review.entity_signals(question, answer)

        # signal 模式：只有实体信号报警才花那 1.4 秒。
        # 三条线上事故全部能被信号命中（问句里的车型/年龄/制度词没落到答案里），
        # 所以这条省时路线在最常见的错法上并不吃亏；吃亏的是「实体都在、制度答串」。
        if fact_review.mode() == "signal" and not signals:
            return {"verdict": "SKIP", "reason": "signal 模式：无实体信号", "signals": signals, "sec": 0.0}

        remaining = deadline - time.time() if deadline else 5.0
        if remaining < 2.0:
            return {"verdict": "SKIP", "reason": f"预算不足（剩余 {remaining:.1f}s）",
                    "signals": signals, "sec": 0.0}

        timeout = max(1.5, min(3.5, remaining - 1.0))
        r = fact_review.review(question, answer, self.client, self.model,
                               timeout=timeout, signals=signals)
        r["signals"] = signals
        tag = fact.get("fact")
        if r["verdict"] == "WRONG":
            print(f"[RAGEngine] 复核判定答错对象 {tag}（{r['sec']:.1f}s）：{r['reason']}"
                  + (f"｜信号 {signals}" if signals else ""))
        elif r["verdict"] == "SKIP":
            print(f"[RAGEngine] 复核跳过 {tag}：{r['reason']}")
        return r

    @staticmethod
    def _audit_fact_numbers(answer: str, tag: str = ""):
        """事实层答案的数字体检：只报日志，不改输出。"""
        try:
            import number_guard
            res = number_guard.index().check(answer)
        except Exception:
            return
        if res["ungrounded"]:
            print(f"[RAGEngine] ⚠ 事实层自检：{tag} 有 {len(res['ungrounded'])} 个数值"
                  f"在法规原文中无对应 -> {[b['raw'] for b in res['ungrounded']]}")

    def _number_gate(self, answer: str):
        """数字核对闸门。返回 (答案, 是否需要整体降级为检索原文)。

        处置分两档，与引用对账同构：
          · 1 个数字对不上 → 保留答案，末尾点名该数值（保留正文的可读性，
            同时明确告诉用户"这个数别直接采信"）
          · ≥2 个，或无据数占全部数字一半 → 整段数值不可信，不再修补，降级给原文

        为什么单个也提示、不再要求"3 个以上且六成无据"：
        依据源已换成知识库全库原文，不再受片段截断干扰，误报在 116 条已确认正确的
        答案上是 0/627。在这个误报水平上，"单个错"没有理由放过——用户问罚款多少，
        答成 200 元而法定是 500 元，一个数就足以误导。
        """
        try:
            import number_guard
        except Exception as e:
            print(f"[RAGEngine] 数字核对模块不可用（跳过）: {e}")
            return answer, False
        try:
            res = number_guard.index().check(answer)
        except Exception as e:
            print(f"[RAGEngine] 数字核对异常（跳过）: {e}")
            return answer, False

        bad, total = res["ungrounded"], res["total"]
        if not bad:
            return answer, False

        ratio = len(bad) / total if total else 0
        print(f"[RAGEngine] 数字核对：{len(bad)}/{total} 个数值在法规原文中无对应 "
              f"({ratio:.0%}) -> {[b['raw'] for b in bad][:6]}")
        if len(bad) >= 2 or ratio >= 0.5:
            return answer, True
        note = number_guard.render_note(bad)
        return (answer + note if note else answer), False

    @staticmethod
    def _dedupe_paragraphs(text: str) -> str:
        """删除高度复述的段落：后段内容有 ≥80% 的 6 字片段已在前文出现，则判为重复"""
        paras = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        if len(paras) < 2:
            return text
        kept = []
        for p in paras:
            norm = re.sub(r'\s', '', p)
            prev = re.sub(r'\s', '', ''.join(kept))
            if prev and len(norm) >= 10:
                # 判据一：后段首句已在前文出现（典型"把结论再说一遍"）
                first_sent = re.split(r'[。！？；]', norm)[0]
                if len(first_sent) >= 8 and first_sent in prev:
                    continue
                # 判据二：后段 80% 以上的 6 字片段已在前文出现
                grams = {norm[i:i + 6] for i in range(len(norm) - 5)}
                if grams and sum(1 for g in grams if g in prev) / len(grams) >= 0.8:
                    continue
            kept.append(p)
        return "\n\n".join(kept)

    # ── 法规硬约束：这 4 种车型不得初次申领 ──
    # 依据 172号令第十六条：初次申领可申请车型为 A3/B2/C1/C2/C3/C4/C5/D/E/F/M/N/P，
    # 不含 A1、A2、B1、C6，故这 4 种只能增驾取得。
    _NO_FIRST_LICENSE = ("A1", "A2", "B1", "C6")

    # ── 吊销 / 注销 消歧（护栏共用）──
    # 处罚类问句：出现这些词，说明问的是"吊销后多久能再考"，不属于"注销恢复"语境
    _REVOKE_Q = re.compile(r"(吊销|禁驾|醉驾|醉酒|酒驾|饮酒|毒驾|吸毒|肇事逃逸|逃逸)")
    # "吊销"被缝进"注销恢复"的硬错误特征
    _RESTORE_MIX = (
        re.compile(r"(吊销|醉驾|醉酒|酒驾|饮酒).{0,80}(注销未超过|注销后未超过|未超过2年|未超过二年)"),
        re.compile(r"(2年|二年|两年).{0,24}(恢复驾驶资格|恢复驾驶)"),
        re.compile(r"(恢复驾驶资格).{0,24}(2年|二年|两年)"),
    )

    _NEG_MARKERS = ("不能", "不得", "不可", "无法", "禁止", "严禁", "并非", "不属于",
                    "只能", "仅能", "须经", "必须经", "需经", "应经", "不含", "不包括", "除外")

    def _apply_guardrails(self, answer: str, question: str) -> str:
        """确定性护栏（正则后处理）：纠正"初次申领 A1/A2/B1/C6"这类法规硬错误。
        弥补小模型在长约束 prompt 下偶尔走神。

        设计要点（避免误伤合法表述）：
        - 只处理车型与"初次申领"**直接相邻**的写法，不做跨句改写
        - 若相邻上下文已含否定/增驾限定（如"A1不能初次申领"），跳过不改
        - 一旦触发纠正，在答案末尾追加系统校验说明，向用户明示依据
        """
        codes = "|".join(self._NO_FIRST_LICENSE)
        hits = []

        def _negated(full: str, pos: int) -> bool:
            return any(w in full[max(0, pos - 15):pos] for w in self._NEG_MARKERS)

        # 模式一：车型（含"A1、A2"这类并列列表）+（可以/可/能/允许/均/都）+ 初次申领  → 整体改写
        pat1 = re.compile(
            r"(?P<list>(?:" + codes + r")(?:\s*[、/,和及与]\s*(?:" + codes + r"))*)"
            r"\s*(?:可以|可|能够|能|允许|均|都)?\s*初次申领"
        )

        def _fix1(m):
            if _negated(m.string, m.start()):
                return m.group(0)
            hits.extend(re.findall(codes, m.group("list")))
            return f"{m.group('list')}须经增驾取得（不能初次申领）"

        answer = pat1.sub(_fix1, answer)

        # 模式二：初次申领（含"取得"/括号）+ 车型  → 仅把"初次申领"改为"申请增驾"
        pat2 = re.compile(r"初次申领(?:取得)?\s*[（(]?\s*(?P<code>" + codes + r")\s*[）)]?")

        def _fix2(m):
            if _negated(m.string, m.start()):
                return m.group(0)
            hits.append(m.group("code"))
            return f"申请增驾{m.group('code')}"

        answer = pat2.sub(_fix2, answer)

        if hits:
            kinds = "、".join(sorted(set(hits)))
            answer += ("\n\n【系统校验】依据《机动车驾驶证申领和使用规定》（公安部令第172号）第十六条，"
                       "初次申领可申请的准驾车型不含 A1（大型客车）、A2（重型牵引挂车）、"
                       "B1（中型客车）、C6（轻型牵引挂车）；这 4 种车型只能先持有其他准驾车型、"
                       f"满足相应年限与记分条件后通过增驾取得。本回答涉及 {kinds}，已按此口径修正。")

        answer = self._guard_color_blindness(answer)
        answer = self._guard_enumerations(answer, question)
        answer = self._guard_score_confusion(answer, question)
        answer = self._guard_revoke_restore_confusion(answer, question)
        return answer

    def _guard_revoke_restore_confusion(self, answer: str, question: str) -> str:
        """纠正「吊销 → 注销 → 2年恢复」的制度缝合错误。

        这是线上真实出现过的一类硬错误：问题问"醉驾后多久可以继续考驾驶证"，
        模型从检索到的第七十九条片段里抄了"注销未超过2年…可以恢复驾驶资格"，
        把 5 年禁驾 + 全科目重考答成了 2 年 + 考科目一恢复。
        触发条件刻意收窄：问句含处罚词 **且** 答案把吊销套进注销恢复，才整体替换。
        """
        if not self._REVOKE_Q.search(question or ""):
            return answer
        if not any(p.search(answer) for p in self._RESTORE_MIX):
            return answer
        return self._canonical_revoke_answer(question)

    def _canonical_revoke_answer(self, question: str) -> str:
        """按违法情形给出标准的吊销口径（终生 / 10年 / 5年）。"""
        q = question or ""
        if re.search(r"逃逸", q) or re.search(r"(死亡|撞死|致死|重大交通事故|重大事故|构成犯罪)", q):
            head, law = "**终生不得重新取得机动车驾驶证（终生禁驾）。**", "第十五条第三项、第四项"
        elif "营运" in q:
            head, law = "**10 年内不得申请机动车驾驶证。**", "第十五条第六项"
        else:
            head, law = "**5 年内不得申请机动车驾驶证。**", "第十五条第五项"

        return (
            f"{head}\n\n"
            f"依据：《机动车驾驶证申领和使用规定》{law}。\n\n"
            "禁驾期满后按**初次申领**程序重新报考，须重新参加科目一至科目四全部考试，取得的是新驾驶证。\n\n"
            "⚠️ 「注销未超过2年、考科目一恢复驾驶资格」只适用于**因超过有效期1年以上未换证被注销**的情形"
            "（第七十九条第八项），与吊销、酒驾、醉驾无关，不能套用。\n\n"
            "【系统校验】原答案把「吊销后重新申领」与「注销后恢复驾驶资格」混为一谈，已按第十五条校正。"
        )

    # 单眼视力障碍标准表述（172号令第十四条身体条件第2项）
    _CANONICAL_MONOCULAR = (
        "单眼视力障碍的申请条件（《机动车驾驶证申领和使用规定》公安部令第172号第十四条身体条件第2项）：\n\n"
        "优眼裸视力或者矫正视力达到对数视力表 **5.0 以上**，且水平视野达到 **150 度** 的，"
        "可以申请 **C1（小型汽车）、C2（小型自动挡汽车）、C3（低速载货汽车）、"
        "C4（三轮汽车）、C5（残疾人专用小型自动挡载客汽车）共 5 种**准驾车型的机动车驾驶证。\n\n"
        "也就是说，“一只眼睛看不见”并不等于一律不能考驾照：只要优眼视力达标且水平视野达到150度，"
        "仍可申请上述 5 种车型；两项条件任一不满足的，才不能申请。\n\n"
        "【系统校验】本回答已按第十四条身体条件第2项原文口径校正。具体体检认定与业务办理以公安机关交通管理部门答复为准。"
    )

    def _guard_enumerations(self, answer: str, question: str) -> str:
        """确定性补全：模型在长条款上常把枚举截断（只列前两项），
        或把"单眼视力障碍有条件下可申请"错读成"一律不能申请"。"""
        # 断言式硬错误：单眼看成"一律不能申请" → 整体替换为标准表述
        if "单眼" in answer and re.search(
            r"不能申请任何|不符合申请(?:机动车)?驾驶证的条件|不能申请机动车驾驶证|一律不能(?:申请|考)", answer
        ):
            return self._CANONICAL_MONOCULAR

        notes = []

        # 单眼视力障碍：第十四条身体条件第2项列明 C1/C2/C3/C4/C5 共 5 种
        # 仅在回答已列举车型却漏列 C5 时补全，避免对"未达标不能申请"类回答产生噪音
        if "单眼" in answer and ("C1" in answer or "小型汽车" in answer) and "C5" not in answer:
            notes.append(
                "依据第十四条身体条件第2项，单眼视力障碍（优眼裸视力或矫正视力达对数视力表5.0以上、"
                "水平视野达150度）可申请 **C1、C2、C3、C4、C5 共5种**准驾车型，并非仅 C1/C2。"
            )

        # 恢复驾驶资格：必须写明"注销未超过2年"这一时限条件
        # ⚠️ 只在"注销"语境补，吊销语境下这句是反向污染：
        # 用户问"醉驾吊销后多久能再考"，这里硬塞一句"注销未超过2年可以恢复"，
        # 反而把"5年禁驾后重新报考"包装成了"2年恢复"。
        if (self._REVOKE_Q.search(question or "") is None
                and "注销" in (answer + (question or ""))
                and "恢复驾驶资格" in answer
                and "2年" not in answer and "二年" not in answer):
            notes.append(
                "依据第七十九条，因超过有效期1年以上未换证被注销的驾驶证，"
                "**注销未超过2年**的，参加道路交通安全法律法规和相关知识考试（科目一）合格后可恢复驾驶资格；"
                "超过2年的不能恢复。"
            )

        if notes:
            answer += "\n\n【系统校验】" + "\n".join(notes)
        return answer

    def _guard_score_confusion(self, answer: str, question: str) -> str:
        """高速/快速路"违法停车"(9分) 与 "违法占用应急车道行驶"(6分) 极易混淆。
        仅当问句同时含"停车/停放"与"高速/快速路"类表述、且模型答成6分时纠正为9分。"""
        if not re.search(r"停车|停放", question):
            return answer
        if not re.search(r"应急车道|高速公路|高速|城市快速路|快速路", question):
            return answer
        if "6分" not in answer or "9分" in answer:
            return answer

        answer = answer.replace("6分", "9分")
        answer += (
            "\n\n【系统校验】依据《道路交通安全违法行为记分管理办法》（公安部令第163号）：\n"
            "· 第九条第三项：驾驶机动车在**高速公路或者城市快速路上违法停车**的，一次记 **9分**；\n"
            "· 第十条第十一项：驾驶机动车在高速公路或者城市快速路上**违法占用应急车道行驶**的，一次记6分。\n"
            "在应急车道**停车**属于“违法停车”，应记9分；只有“占用应急车道**行驶**”才记6分。"
        )
        return answer

    # 辨色力条款：第十四条身体条件第3项"辨色力：无红绿色盲"，无任何车型限定
    _COLOR_DENIAL = ("没有明确要求", "未明确要求", "没有要求", "未要求", "不要求",
                     "无明确要求", "未作要求", "不适用", "因地区而异", "没有限定", "未限定")

    # 明确断言"色盲可以申请"的错误表述（"不能/未"等否定形式已排除）
    _COLOR_WRONG = (
        r'红绿色盲[^。；\n]{0,15}(?:可以|亦可|(?<![不未无])能|(?<![不未无])可)(?:申请|考|学|报考)',
        r'并非绝对不(?:能|可以)',
        r'不需要辨色(?:能力|力)',
        r'无辨色(?:能力)?要求的',
        r'色盲[^。；\n]{0,10}(?:也|均|同样)?(?:可以|(?<![不未无])能)申请',
    )

    _COLOR_CANONICAL = (
        "依据《机动车驾驶证申领和使用规定》（公安部令第172号）第十四条身体条件第3项，"
        "辨色力要求为“无红绿色盲”。该项仅此一句，没有按准驾车型分列，"
        "因此对 A1、A2、A3、B1、B2、C1、C2、C3、C4、C5、C6、D、E、F、M、N、P 全部准驾车型一律适用。\n\n"
        "结论：**红绿色盲者不能申请任何准驾车型的机动车驾驶证**，不存在“C1/C2 等车型不要求辨色力”的例外。\n\n"
        "说明：本回答已按法规原文口径校正。具体体检认定与业务办理以公安机关交通管理部门答复为准。"
    )

    def _guard_color_blindness(self, answer: str) -> str:
        """纠正辨色力口径的两类错误：①把要求限缩到部分车型 ②断言色盲可以申请。
        法律要点：第十四条身体条件第3项仅一句"辨色力：无红绿色盲"，无车型限定，
        对所有准驾车型一律适用（与身高、视力分车型规定不同）。"""
        if "色盲" not in answer:
            return answer

        # 第一类：直接断言"色盲可以申请/并非绝对不能"——结论本身错误，整段替换为标准表述
        if any(re.search(p, answer) for p in self._COLOR_WRONG):
            return self._COLOR_CANONICAL

        # 第二类：句中出现"没有明确要求/因地区而异"等限缩措辞——删除该句并补正
        corrected = False
        sents = re.split(r'(?<=[。！？；\n])', answer)
        out = []
        for s in sents:
            if "红绿色盲" in s and any(w in s for w in self._COLOR_DENIAL):
                corrected = True
                out.append("依据第十四条身体条件第3项，辨色力要求为“无红绿色盲”，"
                           "该项规定没有任何车型限定，对 A1 至 P 全部准驾车型一律适用。")
                continue
            out.append(s)
        answer = "".join(out).strip()

        if corrected:
            answer += ("\n\n【系统校验】《机动车驾驶证申领和使用规定》（公安部令第172号）第十四条身体条件第3项"
                       "原文为“辨色力：无红绿色盲”，未按车型分列，故所有准驾车型（含 C1、C2）均要求无红绿色盲；"
                       "红绿色盲者不能申请任何准驾车型的机动车驾驶证。")
        return answer


def create_rag_engine(persist_dir: str, api_base: str, api_key: str, model: str, embed_backend: str = "cloud",
                      embed_api_key: str = "", embed_api_base: str = ""):
    """创建 RAGEngine 实例辅助函数。

    生成与嵌入是两套独立凭据：生成走智谱 glm-4-flash（免费），嵌入走云端嵌入服务。
    所以这里分开传 embed_api_key / embed_api_base，未提供时才回落到生成侧配置。
    """
    vector_store = VectorStore(
        persist_dir,
        api_key=embed_api_key or api_key,
        api_base=embed_api_base or api_base,
        embed_backend=embed_backend,
    )
    print(f"[create_rag_engine] 向量库加载完成，文档数: {vector_store.count()}")

    # ── 启动预热 ──
    # BM25 倒排索引与嵌入模型都是「首次检索时构建」的懒加载结构。若不预热，
    # 云托管上一个真实用户的第一个请求要额外承担 2~5 秒构建开销，而这笔开销
    # 直接算在他那份 15 秒预算里。启动时付掉这笔账，用户侧就只剩纯检索耗时。
    t0 = time.time()
    try:
        vector_store._build_bm25_index()
        vector_store.search("驾驶证", top_k=1)  # 触发嵌入前向，把模型彻底唤醒
        # 引用索引只有 9KB，读一次就好；放在预热里加载，省得算进第一个用户的 15 秒预算
        try:
            import citation_guard
            citation_guard.index().load()
        except Exception as e:
            print(f"[create_rag_engine] 引用索引加载失败（不影响问答，仅跳过对账）: {e}")
        print(f"[create_rag_engine] 启动预热完成，耗时 {time.time() - t0:.2f}s")
    except Exception as e:
        print(f"[create_rag_engine] 预热失败（忽略，首次请求会自动重试）: {e}")

    engine = RAGEngine(vector_store, api_base, api_key, model)
    return engine