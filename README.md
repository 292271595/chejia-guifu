# 车驾管知识库问答系统

基于 **RAG（检索增强生成）** 架构的智能问答系统，覆盖驾驶证业务、机动车登记、交通违法处理三大知识领域。

## 系统架构

```
用户提问 → 事实层（确定性秒答） ─命中─→ 直接返回法条
                │未命中
                ↓
        混合检索：向量(0.55) + BM25(0.45) → 取 top-5 片段
                ↑                      ↓
      嵌入服务（可插拔，见下）      拼接 Prompt → 大模型(glm-4-flash) 单遍生成
                ↑                      ↓
        车驾管知识库(735 个片段)   带来源引用的答案（小程序按 blocks 渲染）
```

嵌入后端是可插拔的，三种任选，由 `EMBED_BACKEND` 控制：

| 后端 | 值 | 维度 | 依赖 | 适用 |
|---|---|---|---|---|
| 云端嵌入 | `cloud`（默认） | 1024 | 一个 API Key | 云托管 0.5 核 1G，镜像约 300MB |
| 本地模型 | `local` | 512 | torch，镜像约 1.5GB | 断网环境、1 核 2G |
| 纯 BM25 | `none` | — | 无 | 嵌入服务不可用时的兜底（口语问句召回变弱） |

> 换后端 = 换向量空间，必须重建库：`python tools/rebuild_kb.py --key sk-xxx`


## 目录结构

```
rag-vehicle-system/
├── backend/
│   ├── main.py              # FastAPI 服务入口 + API接口
│   ├── document_loader.py   # 文档加载与语义分块
│   ├── vector_store.py      # ChromaDB向量库封装
│   └── rag_engine.py        # RAG问答引擎
├── data/
│   └── knowledge_base/
│       ├── driving_license.md       # 驾驶证业务知识
│       ├── vehicle_registration.md  # 机动车登记业务知识
│       └── traffic_violations.md    # 交通违法与处罚知识
├── frontend/
│   └── index.html           # 问答前端界面
├── chroma_db/               # 向量数据库持久化目录(自动生成)
├── build_kb.py              # 知识库构建脚本
└── requirements.txt
```

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 构建知识库
```bash
python build_kb.py
```

### 3. 配置大模型API

修改 `backend/main.py` 中的配置，或设置环境变量：

```bash
$env:API_KEY = "你的DeepSeek_API_Key"
$env:API_BASE = "https://api.deepseek.com/v1"
$env:MODEL = "deepseek-chat"
```

> 注册DeepSeek获取免费API Key：https://platform.deepseek.com

### 4. 启动服务
```bash
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. 访问系统
打开浏览器 → http://localhost:8000

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 问答界面 |
| `/api/query` | POST | 提交问题 `{"question": "..."}` |
| `/api/status` | GET | 服务状态与知识库信息 |

## 技术栈

- **向量数据库**: ChromaDB 1.5.9（版本锁死，1.x 持久化格式与 0.4/0.5 不兼容）
- **Embedding**: 可插拔——云端 `BAAI/bge-m3`（1024 维）/ 本地 `bge-small-zh-v1.5`（512 维）/ 纯 BM25
- **检索**: 向量 0.55 + BM25 0.45 混合，口语问句经 `expand_query` 补法条术语
- **大模型**: 智谱 `glm-4-flash`（长期免费，OpenAI 兼容接口）
- **后端**: FastAPI + Uvicorn（单 worker，云端 0.5 核 1G 可跑）
- **前端**: 微信小程序（原生，云托管 `callContainer` 通道）+ 纯 HTML 页面

## 核心特性

1. **不胡说**: 严格基于知识库回答，未知信息会明确告知"暂无相关信息"
2. **可溯源**: 每个回答附带知识库引用来源和相似度评分
3. **完全免费可运行**: ChromaDB免费 + 本地Embedding免费 + DeepSeek免费API
4. **易于迁移**: 替换知识库Markdown文档即可用于任何垂直领域
5. **轻量部署**: 单机运行，无需GPU，适合个人/小团队
## 问答准确性：四层防线

线上真实出现过一类硬错误：问「醉驾后多久可以继续考驾驶证」，系统答成
「吊销后注销未超过2年、考科目一即可恢复驾驶资格」——把 **吊销**（违法后的惩罚，5年禁驾、期满重新报考全部科目）
和 **注销**（超期未换证的行政管理性注销，2年内考科目一恢复）缝成了一句话。

修复采用了分层兜底，任何一层单独失效都不会漏到用户侧：

| 层 | 位置 | 作用 |
|---|---|---|
| 1. 事实层路由 | `backend/fact_table.py` `REVOKE_TOPIC` + `revoke_topic_ok()` | 接住口语问法（继续考/还能考/什么时候能考/重新拿证/撞死人），命中即查表直答，不经模型 |
| 2. 检索扩展 | `backend/vector_store.py` `expand_query()` | 口语 → 法条术语（醉驾→吊销/不得重新取得/禁驾），把第十五条拉回 top-5 |
| 3. 生成约束 | `backend/rag_engine.py` 系统提示词【吊销 ≠ 注销】 | 明确"2年恢复"只适用于换证注销，禁止移植到吊销 |
| 4. 确定性护栏 | `backend/rag_engine.py` `_guard_revoke_restore_confusion()` | 检测答案里的制度缝合痕迹，按第十五条整体校正 |

回归测试（离线，秒级，不花 token）：

```bash
python tools/eval_fact200.py              # 200 题事实层评测
python tools/eval_retrieval.py            # 检索命中率
python tools/test_penalty_regression.py   # 吊销/注销专项：口语改写 19 例 + 护栏 5 例
```

## 性能预算：为什么不再有"第二遍自我验证"

微信云托管 `callContainer` 对接口有 **15 秒硬上限**（官方限制，不可配置）。
早期实现是「生成 → 再拿草稿做第二遍 LLM 核查」两次串行调用，实测单题 8.6~19.0 秒，
必然击穿上限。现已改为 **单遍生成 + 确定性核对**：

| 环节 | 做法 | 耗时 |
|---|---|---|
| 事实层 | 命中即查表直答，不经 LLM | 0.00~0.26s |
| 检索 | top_k=5，混合检索（0.55 向量 + 0.45 BM25） | 0.04~0.5s |
| 生成 | 单遍 LLM，`max_tokens=900`，单片段截断 600 字 | 1~8s（有波动） |
| 数字核对 | `_check_numbers()` 纯代码校验答案数字是否有原文依据 | 0.000s |
| 硬超时 | `_call_llm_until()` 用墙钟卡死 10 秒预算 | 上限 10s |
| 兜底 | 超时/失败返回 3 条法规原文，而非报错 | +0.05s |

> ⚠️ 两个容易踩的坑，改这段代码前务必知道：
> 1. `openai` SDK 的 `timeout` 参数是 httpx 的 **read timeout**（相邻数据块间隔），**不是总时长**。
>    实测设 9.9s、实际跑了 16.72s。只有 `future.result(timeout=)` 才是真墙钟硬超时。
> 2. 线程池的 `fut.result(timeout=)` **从提交时刻起算**，排队时间也算进去。
>    worker 数少于并发数时，请求还没开始执行就会被判超时。宁可多给 worker（阻塞在 I/O 上不耗 CPU）。

实测（本机在线）：8 并发长尾问题最大 **6.98s**，0 降级 0 报错；单题最坏被 10s 预算锁死。
