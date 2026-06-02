"""
工具函数模块
"""
import hashlib
import uuid
from datetime import datetime

def generate_session_id():
    """生成会话ID"""
    return str(uuid.uuid4())

def hash_password(password):
    """密码哈希（简化版，实际应用应使用更安全的方式）"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    """验证密码"""
    return hash_password(password) == hashed

def get_time_ago(dt):
    """获取相对时间"""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    
    now = datetime.utcnow()
    diff = now - dt
    
    if diff.days > 0:
        return f"{diff.days}天前"
    elif diff.seconds >= 3600:
        return f"{diff.seconds // 3600}小时前"
    elif diff.seconds >= 60:
        return f"{diff.seconds // 60}分钟前"
    else:
        return "刚刚"
