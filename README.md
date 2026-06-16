# 智学伴 AI 个性化学习伴侣系统

这是一个基于 Flask 的本地智能学习助手项目。系统支持用户认证、多角色权限、管理员后台、个人/班级知识库、文档上传解析、向量检索、知识库问答、来源展示和学习记录管理。

当前版本已移除云端 API 问答路径，问答与知识库能力统一走本地 Ollama。

## 核心功能

- 用户注册、登录、注销、密码重置
- 多角色权限：学生、教师、管理员
- 管理员后台：用户创建、删除、角色修改、班级知识库查看
- 班级管理：创建班级、分配教师、添加/移除学生
- 学生端不显示后台入口，不能自行切换角色
- 独立知识库页面：`/knowledge-base`
- 学生可创建和维护自己的个人知识库
- 教师可创建个人知识库，也可发布班级知识库
- 学生只可读取自己所在班级的班级知识库并用于问答，不能修改老师发布的文件
- 管理员可管理用户、角色和知识库文件
- 支持 PDF、TXT、DOCX 上传
- 知识库文件管理：上传人、上传时间、解析状态、向量数量、重新解析
- PDF 支持文本提取，扫描类 PDF 可通过 OCR 兜底
- DOCX 支持段落和表格文本提取
- Faiss 本地向量库持久化
- 本地 Ollama 生成模型、嵌入模型和可选重排模型
- 问答支持流式输出和阶段状态提示
- 知识库回答展示来源：文件名、片段、所属知识库
- 学习记录和会话历史保存

## 角色与权限

| 角色 | 后台入口 | 个人知识库 | 班级知识库 | 用户管理 |
| --- | --- | --- | --- | --- |
| 学生 | 不显示、不可访问 | 可创建、上传、删除自己的文件 | 可查看和问答老师发布的班级知识库，不可修改 | 无 |
| 教师 | 不显示管理员后台 | 可创建和维护 | 可发布班级知识库并上传班级资料 | 无 |
| 管理员 | 可访问 `/admin` | 可管理 | 可查看和管理全部班级知识库 | 可创建用户、删除用户、修改角色、维护班级成员 |

角色只能由管理员修改，普通用户不能在前端随意切换权限。

## 知识库逻辑

### 个人知识库

个人知识库归创建者所有。

- 学生创建的知识库只属于该学生。
- 教师也可以创建自己的个人知识库。
- 创建者和管理员可以上传、删除、重命名。
- 其他普通用户不可修改。

### 班级与班级知识库

班级知识库由教师或管理员发布，并绑定到一个真实班级。

- 管理员可在后台创建班级、分配负责教师、添加或移除学生。
- 教师可管理自己负责的班级，并发布知识库到指定班级。
- 学生加入班级后，首页知识库下拉框才会显示该班级的知识库。
- 学生只能读取和问答自己所在班级的知识库，不能上传、删除或重命名老师发布的知识库。
- 发布教师和管理员可以维护其中的文件。

当前涉及三张表：

```text
class_rooms             班级/课程表
class_members           班级成员表，记录学生加入了哪些班级
class_knowledge_bases   班级知识库表，记录知识库发布到哪个班级
knowledge_documents     知识库文件表，记录上传人、解析状态和向量数量
```

### 知识库文件管理

知识库页面会展示每个上传文件的管理信息：

- 文件名、原始路径、上传人、上传时间。
- 解析状态：等待解析、解析中、解析成功、解析失败。
- 向量数量：该文件切分后写入 Faiss 的片段数量。
- 解析失败时保留错误原因，方便定位 DOCX/PDF/嵌入模型问题。
- 管理员、个人知识库创建者、班级知识库发布教师可以删除文件或重新解析。
- 学生访问老师发布的班级知识库时只读，不显示删除和重新解析操作。

重新解析会先删除该文件旧的向量，再使用原文件重新提取文本、切分并写入新向量。每个向量片段的元数据都会记录 `document_id`，因此可以精确定位到某一个上传文件。

### 首页知识库开关

- 开启：提问时优先检索当前选择的知识库；命中后使用 RAG 回答并展示来源。
- 关闭：不检索知识库，直接使用本地 Ollama 模型回答。
- 不再依赖 `default` 知识库作为默认问答入口。

## RAG 流程

1. 用户在知识库页面上传 PDF、TXT 或 DOCX。
2. 系统创建 `KnowledgeDocument` 记录，并标记为解析中。
3. `FileProcessor` 提取文本。
4. `TextSplitter` 将文本切成片段。
5. `EmbeddingModel` 调用 Ollama 嵌入模型生成向量。
6. `VectorStore` 使用 LangChain FAISS 保存向量、文本和元数据。
7. 解析成功后写入向量数量和解析完成时间，失败则记录错误原因。
8. 用户提问时，系统检索相关片段。
9. 如果启用重排，则使用本地重排模型重新排序片段。
10. `QAModule` 将检索上下文和问题组合为提示词。
11. 本地 Ollama 生成回答。
12. 前端展示回答、命中文件、片段来源、所属知识库。
13. 学习记录保存问题、回答、来源、会话 ID 和使用的知识库。

## 本地模型说明

项目统一使用本地 Ollama：

- 生成模型：`OLLAMA_MODEL`
- 嵌入模型：`OLLAMA_EMBEDDING_MODEL`
- 重排模型：`OLLAMA_RERANKER_MODEL`

如果 Ollama 嵌入接口暂时不可用，向量模块会使用本地 fallback 嵌入，保证基础流程不直接崩溃；但正式问答建议保证 Ollama 服务和模型可用。

示例模型配置见 `.env.example`：

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_EMBEDDING_MODEL=bge-m3
OLLAMA_RERANKER_MODEL=qwen2.5:7b
USE_OLLAMA=true
USE_RERANKER=true
```

## 项目结构

```text
app.py                         Flask 主入口，页面路由和 API 路由
run.py                         启动脚本，检查数据库并启动服务
config.py                      配置读取
init_admin.py                  初始化默认管理员
app/models/                    数据库模型
app/routes/                    认证相关蓝图
app/services/                  AI、认证、知识库服务
app/utils/                     文件解析、文本切分、向量服务、兼容补丁
templates/                     页面模板
static/                        CSS / JS / 静态资源
uploads/                       运行时上传目录，已忽略提交
knowledge_base_*_langchain_faiss/ 运行时向量库目录，已忽略提交
```

## 安装与启动

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 准备环境变量

```bash
copy .env.example .env
```

至少配置：

- `SECRET_KEY`
- `MYSQL_HOST`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DB`
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- `OLLAMA_EMBEDDING_MODEL`

如果使用邮箱验证码和密码重置，还需要配置：

- `QQ_EMAIL`
- `QQ_EMAIL_AUTH_CODE`

### 3. 准备 MySQL

确保 MySQL 已启动，并且 `.env` 中配置的数据库可访问。

`run.py` 会执行：

- 数据库连接检查
- 创建缺失的数据表
- 执行轻量级运行时字段兼容检查
- 启动 Flask 服务

### 4. 启动项目

```bash
python run.py
```

默认访问：

```text
http://localhost:5000
```

## 默认管理员

`init_admin.py` 会创建或修复默认管理员账号：

| 角色 | 用户名 | 密码 | 邮箱 |
| --- | --- | --- | --- |
| 管理员 | `admin` | `Admin@2026` | `admin@zhixueban.com` |

首次部署到真实环境后，请尽快修改默认密码。

教师和学生账号可在管理员后台 `/admin` 中创建，例如：

| 角色 | 示例用户名 | 示例密码 | 说明 |
| --- | --- | --- | --- |
| 教师 | `teacher` | `Teacher@2026` | 可发布班级知识库 |
| 学生 | `student` | `Student@2026` | 可创建个人知识库，读取班级知识库 |

## 常用页面

- `/`：首页问答
- `/knowledge-base`：知识库管理
- `/records`：学习记录
- `/profile`：个人中心
- `/admin`：管理员后台
- `/login`：登录
- `/register`：注册

## 使用流程

1. 启动 MySQL、Ollama 和 Flask 项目。
2. 使用管理员账号登录。
3. 在 `/admin` 创建教师和学生用户，或修改已有用户角色。
4. 在 `/admin` 创建班级、选择负责教师，并把学生加入对应班级。
5. 教师登录后进入 `/knowledge-base`，选择创建个人知识库或发布班级知识库。
6. 发布班级知识库时选择指定班级，然后上传 PDF、TXT、DOCX 文档。
7. 学生登录后可以创建个人知识库，也可以在首页选择自己所在班级的知识库问答。
7. 首页提问时会显示“正在检索知识库”“已命中知识片段”等流式状态。
8. 如果回答使用了知识库，回答下方会展示参考来源。

## 运行时文件说明

以下文件不应提交到 Git：

- `.env`
- `uploads/`
- `static/avatars/`
- `knowledge_base_*_langchain_faiss/`
- `knowledge_base_*_vector_store.pkl`
- `knowledge_base_*_faiss.index`

其中 `knowledge_base_*_langchain_faiss/` 是当前 LangChain FAISS 的本地持久化目录。

## 常见问题

### DOCX 上传失败

确认已安装：

```bash
pip install python-docx==0.8.11
```

### PDF OCR 报 NumPy 兼容错误

项目包含 `app/utils/numpy_compat.py`，用于兼容 PaddleOCR 在 NumPy 2.x 下访问 `np.sctypes` 的问题。

### 中文知识库名导致 Faiss 保存失败

向量库目录已改为 ASCII 安全目录名加哈希，页面仍显示原始中文知识库名。

### 页面仍显示旧乱码或旧脚本

浏览器强制刷新：

```text
Ctrl + F5
```

也可以重启 Flask 服务后再刷新。

## 验证命令

```bash
python -m py_compile app.py app/services/ai_service.py app/services/knowledge_service.py app/utils/file_processor.py app/utils/vector_service.py
node --check static/js/shared.js
```
