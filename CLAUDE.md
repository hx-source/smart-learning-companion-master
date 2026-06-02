# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本代码库中工作时提供指导。

## 项目概述

智学伴是一个基于 Flask 的 AI 学习伴侣，支持 RAG 功能——用户上传文档到知识库，系统通过向量检索 + LLM 根据检索内容回答问题。

## 运行项目

```bash
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 配置 MySQL 和 Ollama
python run.py
```

## 架构

```
run.py           # 入口脚本，初始化数据库表后启动 Flask
app.py           # Flask 应用工厂、路由处理、内存缓存 (api_cache)
config.py        # 环境变量读取

app/models/      # SQLAlchemy 模型：User、LearningRecord、UserLog
app/routes/      # Blueprint 路由 (auth.py 处理登录/注册/csrf)
app/services/    # 业务逻辑
app/utils/       # 文档处理、向量服务、文本分块
```

## 核心类

**QAModule** (`app/services/knowledge_service.py`)：知识库单例（按名称）。RAG 流程：`add_document()` → 文件处理 → 文本分块 → `EmbeddingModel.get_batch_embeddings()` → `VectorStore.add_embeddings()`。查询流程：`query_with_knowledge()` → 嵌入问题 → `VectorStore.search()` → `_build_prompt()` → `_call_llm()`。

**VectorStore** (`app/utils/vector_service.py`)：基于 Faiss 的向量存储。持久化到项目根目录的 `knowledge_base_{name}_vector_store.pkl` 和 `knowledge_base_{name}_faiss.index`。使用 `IndexFlatIP`（内积 = 余弦相似度，向量已归一化）。删除操作需要重建索引。

**EmbeddingModel** (`app/utils/vector_service.py`)：调用 Ollama `/api/embeddings` 接口（默认模型 `bge-m3`，1024 维）。Ollama 不可用时回退到字符哈希嵌入。

**AIService** (`app/services/ai_service.py`)：模型路由优先级：`user.use_ollama` → DeepSeek API key → Kimi API key → 智谱 API key → 回退消息。默认 LLM：`qwen2.5:7b`（通过 Ollama）。

## RAG 配置

- 分块大小：500 字符，重叠：100 字符
- 向量维度：1024（bge-m3），归一化后计算余弦相似度
- 默认知识库：`default`

## 环境变量

`.env` 必填项：
- `MYSQL_HOST`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DB`
- `OLLAMA_BASE_URL`（默认：`http://localhost:11434`）
- `USE_OLLAMA=true`

可选：`DEEPSEEK_API_KEY`、`KIMI_API_KEY`、`ZHIPU_API_KEY`、`QQ_EMAIL`、`QQ_EMAIL_AUTH_CODE`

## 运行时文件

- `uploads/` - 用户上传的文档（自动创建）
- `static/avatars/` - 用户头像上传
- `knowledge_base_*_vector_store.pkl`、`knowledge_base_*_faiss.index` - 向量持久化文件，位于项目根目录