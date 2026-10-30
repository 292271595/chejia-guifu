# 车驾管知识库问答系统

基于 **RAG（检索增强生成）** 架构的智能问答系统，覆盖驾驶证业务、机动车登记、交通违法处理三大知识领域。

## 系统架构

```
用户提问 → 向量检索(ChromaDB) → 获取最相关片段 → 拼接Prompt → 大模型回答
                   ↑
         本地Embedding模型(sentence-transformers)
                   ↑
         车驾管知识库(3份Markdown文档)
```

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

- **向量数据库**: ChromaDB (免费、本地、持久化)
- **Embedding**: sentence-transformers `all-MiniLM-L6-v2` (免费、本地)
- **大模型**: DeepSeek Chat (OpenAI兼容API，可替换为任意兼容模型)
- **后端**: FastAPI + Uvicorn
- **前端**: 纯HTML+CSS+JS (零依赖)

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
| 2. 检索扩展 | `backend/vector_store.py` `expand_query()` | 口语 → 法条术语（醉驾→吊销/不得重新取得/禁驾），把第十五条拉回 top-8 |
| 3. 生成约束 | `backend/rag_engine.py` 系统提示词【吊销 ≠ 注销】 | 明确"2年恢复"只适用于换证注销，禁止移植到吊销 |
| 4. 确定性护栏 | `backend/rag_engine.py` `_guard_revoke_restore_confusion()` | 检测答案里的制度缝合痕迹，按第十五条整体校正 |

回归测试（离线，秒级，不花 token）：

```bash
python tools/eval_fact200.py              # 200 题事实层评测
python tools/eval_retrieval.py            # 检索 top-8 命中率
python tools/test_penalty_regression.py   # 吊销/注销专项：口语改写 19 例 + 护栏 5 例
```
