"""项目配置中心。

这个文件集中读取 `.env` 和环境变量，供 Flask、数据库、AI 模型、
文件上传等模块统一使用。运行环境有变化时，优先改 `.env`，
尽量不要把真实密钥直接写进源码。
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask 基础配置：SECRET_KEY 用于签名 Cookie、JWT 和 CSRF 等敏感数据。
    SECRET_KEY = os.getenv('SECRET_KEY', 'change-me-in-env')
    DEBUG = True
    
    # 数据库配置（MySQL）：通过环境变量切换本地/服务器数据库。
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_DB = os.getenv('MYSQL_DB', 'zhixueban')
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}/{MYSQL_DB}?charset=utf8mb4"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # 数据库连接池配置：避免每次请求都重新创建连接，提高并发访问稳定性。
    SQLALCHEMY_POOL_SIZE = 10
    SQLALCHEMY_POOL_TIMEOUT = 30
    SQLALCHEMY_POOL_RECYCLE = 280
    SQLALCHEMY_MAX_OVERFLOW = 5
    
    # Ollama 本地模型配置：用于本地问答、嵌入向量生成和检索结果重排。
    OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')  # 生成模型
    OLLAMA_EMBEDDING_MODEL = os.getenv('OLLAMA_EMBEDDING_MODEL', 'bge-m3')  # 嵌入模型
    OLLAMA_RERANKER_MODEL = os.getenv('OLLAMA_RERANKER_MODEL', 'qwen2.5:7b')  # 重排模型
    USE_OLLAMA = os.getenv('USE_OLLAMA', 'true').lower() == 'true'
    USE_RERANKER = os.getenv('USE_RERANKER', 'true').lower() == 'true'  # 是否启用重排





    # QQ 邮箱配置：用于发送注册验证码、找回密码验证码等邮件。
    QQ_EMAIL = os.getenv('QQ_EMAIL', '')
    QQ_EMAIL_AUTH_CODE = os.getenv('QQ_EMAIL_AUTH_CODE', '')
    
    # 上传文件配置：头像和知识库文档都会写入本地运行时目录。
    AVATAR_UPLOAD_FOLDER = os.getenv('AVATAR_UPLOAD_FOLDER', 'static/avatars')
    DOCUMENT_UPLOAD_FOLDER = os.getenv('DOCUMENT_UPLOAD_FOLDER', 'uploads')
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB
