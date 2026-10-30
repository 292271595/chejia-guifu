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