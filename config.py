import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask配置
    SECRET_KEY = os.getenv('SECRET_KEY', 'change-me-in-env')
    DEBUG = True
    
    # 数据库配置（MySQL）
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_DB = os.getenv('MYSQL_DB', 'zhixueban')
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}/{MYSQL_DB}?charset=utf8mb4"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # 数据库连接池配置
    SQLALCHEMY_POOL_SIZE = 10
    SQLALCHEMY_POOL_TIMEOUT = 30
    SQLALCHEMY_POOL_RECYCLE = 280
    SQLALCHEMY_MAX_OVERFLOW = 5
    
    # DeepSeek API配置
    DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
    DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
    DEEPSEEK_MODEL = 'deepseek-chat'
    
    # Kimi API配置
    KIMI_API_KEY = os.getenv('KIMI_API_KEY')
    KIMI_BASE_URL = 'https://api.moonshot.cn'
    
    # 智谱AI配置
    ZHIPU_API_KEY = os.getenv('ZHIPU_API_KEY')

    # Ollama 本地模型配置
    OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')  # 生成模型
    OLLAMA_EMBEDDING_MODEL = os.getenv('OLLAMA_EMBEDDING_MODEL', 'bge-m3')  # 嵌入模型
    OLLAMA_RERANKER_MODEL = os.getenv('OLLAMA_RERANKER_MODEL', 'qwen2.5:7b')  # 重排模型
    USE_OLLAMA = os.getenv('USE_OLLAMA', 'true').lower() == 'true'
    USE_RERANKER = os.getenv('USE_RERANKER', 'true').lower() == 'true'  # 是否启用重排





    # QQ邮箱配置
    QQ_EMAIL = os.getenv('QQ_EMAIL', '')
    QQ_EMAIL_AUTH_CODE = os.getenv('QQ_EMAIL_AUTH_CODE', '')
    
    # 上传文件配置
    AVATAR_UPLOAD_FOLDER = os.getenv('AVATAR_UPLOAD_FOLDER', 'static/avatars')
    DOCUMENT_UPLOAD_FOLDER = os.getenv('DOCUMENT_UPLOAD_FOLDER', 'uploads')
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB
