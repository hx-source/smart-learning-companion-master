# 智学伴 AI 个性化学习伴侣系统

这是一个基于 Flask 的智能学习助手项目，集成了用户认证、知识库管理、文档处理、向量检索和本地/远程模型调用。

## 核心功能

- 用户注册 / 登录 / 注销
- 邮箱验证码注册与密码重置
- 账户锁定与登录安全策略
- 用户 API 密钥管理
- 用户资料与头像上传
- 文档上传与知识库管理
- 文档格式支持：PDF、TXT、DOCX
- OCR 文本提取（PaddleOCR + PyMuPDF）
- 文本分块与向量化
- 向量检索：Faiss 本地向量索引
- 语义问答：RAG 流程 + Ollama / DeepSeek / 自定义模型
- 学习记录保存与会话历史

## 项目结构

- `app.py` - Flask 应用主入口与页面/API 路由
- `run.py` - 启动脚本，负责初始化数据库并启动服务
- `config.py` - 应用配置与环境变量读取
- `app/` - 应用模块
  - `models/` - 数据库模型
  - `routes/` - Blueprint 路由
  - `services/` - 业务逻辑服务
  - `utils/` - 文档处理、文本拆分、向量服务
- `requirements.txt` - Python 依赖
- `templates/` - 前端 HTML 模板
- `static/` - 静态资源
- `uploads/` - 上传文档目录（运行时自动创建）

## 主要实现功能

### 1. 用户认证与安全

- 注册时发送邮箱验证码
- 邮箱验证码验证、密码设置、账户激活
- 登录支持用户名或邮箱
- 登录失败次数限制与锁定策略
- JWT Token 与 CSRF 保护
- 密码重置与密码修改
- 登录、注册、重置、登出等操作日志记录

### 2. 知识库管理

- 创建/删除/重命名知识库
- 浏览知识库列表
- 上传文档到指定知识库
- 查看知识库统计信息
- 查看向量数据、文件来源
- 按文件来源删除向量数据

### 3. 文档处理

- 支持 PDF、TXT、DOCX 文件格式
- PDF 读取文本内容，如果文本不足则自动切换 OCR 识别
- DOCX 读取段落和表格内容
- TXT 直接读取文本

### 4. RAG（Retrieval-Augmented Generation）流程

该项目的 RAG 流程包括以下步骤：

1. 用户上传文档到知识库。
2. `FileProcessor` 提取文件文本。
3. `TextSplitter` 将长文本拆分为多个语义块。
4. `EmbeddingModel` 将每个文本块转为向量。默认使用 Ollama 的嵌入接口；不可用时使用本地字符哈希降级。
5. `VectorStore` 使用 Faiss 构建本地向量索引，并保存文本、元数据、ID 等。
6. 用户提问时，`QAModule` 生成查询向量并检索最相似的文档块。
7. 将检索到的文档块拼接成上下文，并构建提示词发送给 LLM。
8. 如果知识库中有相关上下文，则返回基于知识库的回答；否则使用常规模型回答。
9. 将问题 / 回答 / 来源 / 会话 ID 保存到 MySQL 学习记录中。

## RAG 实现细节

### 文本分块

- 使用 `TextSplitter` 将每页文本按段落拆分
- 每个分块最大长度 `500` 字符，重叠 `100` 字符
- 处理超长段落，尽量保持语义完整

### 嵌入与向量存储

- 使用 `EmbeddingModel.get_embedding` 获取文本嵌入
- `VectorStore.add_embeddings` 归一化向量并写入 Faiss 索引
- 本地持久化：`*_vector_store.pkl` 和 `*_faiss.index`
- 查询时使用内积搜索，转换为相似度结果

### 上下文构建与回答生成

- 如果检索到文档上下文，`QAModule._build_prompt` 会将上下文与问题合并
- 如果没有上下文，则直接发送问题给 LLM
- 默认生成模型为 `qwen2.5:7b`
- 可使用 `Ollama` 本地模型或 `DeepSeek` API

## 如何使用

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 复制环境变量模板

```bash
copy .env.example .env
```

### 3. 填写 `.env`

至少配置：

- `MYSQL_HOST`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DB`
- `OLLAMA_BASE_URL`
- `USE_OLLAMA=true`

如果需要远程模型服务，可额外填写：

- `DEEPSEEK_API_KEY`
- `KIMI_API_KEY`
- `ZHIPU_API_KEY`
- `QQ_EMAIL`
- `QQ_EMAIL_AUTH_CODE`

### 4. 启动数据库

确保 MySQL 服务已启动，并且 `.env` 中的 `zhixueban` 数据库可访问。

如果需要手动初始化数据库，可以运行：

```bash
python run.py
```

`run.py` 会检查数据库连接、创建表并启动 Flask 服务器。

### 5. 启动项目

```bash
python run.py
```

默认访问地址：

```
http://localhost:5000
```

### 6. 使用流程

- 访问 `/register` 创建用户
- 登录后访问首页、个人中心、知识库页面
- 上传文档到知识库
- 在问答界面输入问题，系统会先尝试检索知识库，然后生成回答
- 可在个人中心配置 API 密钥

## 项目运行时说明

- `uploads/` 目录用于保存用户上传文档
- `static/avatars/` 用于保存头像上传
- `knowledge_base_*_vector_store.pkl` 和 `*_faiss.index` 为知识库持久化文件
- 如果 `Ollama` 未连接成功，系统会回退到本地向量 fallback 嵌入

## 进一步扩展建议

- 增加更多模型支持，如 OpenAI、Azure、本地 LLM
- 增加知识库向量数据删除接口的前端操作
- 增加用户权限和角色管理
- 增强问答结果的来源可视化
- 将知识库索引迁移到 Milvus、Pinecone 或 Weaviate

## 逐步思考

1. 先明确项目目标：构建一个带知识检索的学习助手。
2. 用 Flask 搭建 Web/API 框架，MySQL 保存用户与学习记录。
3. 将文档处理模块独立出来，支持 PDF/OCR、DOCX、TXT。
4. 用文本分块减少检索粒度，避免上下文过长。
5. 用向量化 + Faiss 实现近似搜索，构建知识库索引。
6. 结合 LLM 生成回答，实现 `retrieval + generation`。
7. 设计知识库管理接口、用户认证、API 密钥管理。
8. 考虑异常情况：OCR 失败、Faiss 文件读取失败、Ollama 未连通。
9. 最终让系统支持用户上传文档、检索知识、生成答案并记录历史。
