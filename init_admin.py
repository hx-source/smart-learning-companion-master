"""
初始化管理员账户脚本
"""
import os
import sys
from datetime import datetime
import bcrypt
import re

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接导入必要的模块，避免初始化不需要的组件
from config import Config
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# 创建临时Flask应用
app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

# 定义User模型（简化版，只需要必要字段）
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    email_verified = db.Column(db.Boolean, default=False)
    major = db.Column(db.String(100))
    grade = db.Column(db.String(20))
    avatar = db.Column(db.String(255), default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    use_ollama = db.Column(db.Boolean, default=True)
    ollama_model = db.Column(db.String(50), default='qwen2.5:7b')
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    @staticmethod
    def get_by_username(username):
        return User.query.filter_by(username=username).first()

def hash_password(password):
    """密码哈希"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def validate_password(password):
    """验证密码安全策略"""
    if len(password) < 8 or len(password) > 20:
        return False, "密码长度需为8-20个字符"
    if not re.search(r'[A-Z]', password):
        return False, "密码需包含大写字母"
    if not re.search(r'[a-z]', password):
        return False, "密码需包含小写字母"
    if not re.search(r'\d', password):
        return False, "密码需包含数字"
    return True, "密码验证通过"

def update_database_schema():
    """更新数据库表结构，添加is_admin列"""
    with app.app_context():
        try:
            # 尝试添加is_admin列
            with db.engine.connect() as conn:
                # 检查列是否已存在
                result = conn.execute(db.text("SHOW COLUMNS FROM users LIKE 'is_admin'"))
                column_exists = result.fetchone() is not None
                
                if not column_exists:
                    print("正在添加is_admin列到users表...")
                    conn.execute(db.text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
                    conn.commit()
                    print("is_admin列添加成功!")
                else:
                    print("is_admin列已存在，无需添加")
        except Exception as e:
            print(f"更新数据库结构时出错: {e}")
            print("尝试继续执行...")

def init_admin():
    """初始化管理员账户"""
    with app.app_context():
        # 首先更新数据库表结构
        update_database_schema()
        
        # 检查admin账户是否已存在
        admin_user = User.get_by_username('admin')
        if admin_user:
            print("\n管理员账户已存在!")
            print(f"用户名: {admin_user.username}")
            print(f"邮箱: {admin_user.email}")
            print(f"管理员权限: {admin_user.is_admin}")
            
            # 确保admin用户有管理员权限
            if not admin_user.is_admin:
                print("\n正在为admin用户添加管理员权限...")
                admin_user.is_admin = True
                db.session.commit()
                print("管理员权限已添加!")
            return
        
        # 配置管理员账户信息
        username = 'admin'
        email = 'admin@zhixueban.com'
        password = 'Admin@2026'  # 符合安全策略的密码：8-20个字符，含大小写字母、数字
        
        # 验证密码
        is_valid, msg = validate_password(password)
        if not is_valid:
            print(f"密码验证失败: {msg}")
            return
        
        # 创建admin用户
        admin_user = User(
            username=username,
            email=email,
            password=hash_password(password),
            email_verified=True,
            is_admin=True,
            major='系统管理员',
            grade='管理员'
        )
        
        # 保存到数据库
        db.session.add(admin_user)
        db.session.commit()
        
        print("\n" + "=" * 60)
        print("管理员账户创建成功!")
        print("=" * 60)
        print(f"用户名: {username}")
        print(f"密码: {password}")
        print(f"邮箱: {email}")
        print(f"管理员权限: {admin_user.is_admin}")
        print("=" * 60)
        print("请妥善保管这些信息!")
        print("=" * 60)

def verify_admin():
    """验证管理员账户"""
    with app.app_context():
        admin_user = User.get_by_username('admin')
        if not admin_user:
            print("管理员账户不存在!")
            return False
        
        print("\n验证管理员账户...")
        print(f"用户ID: {admin_user.id}")
        print(f"用户名: {admin_user.username}")
        print(f"邮箱: {admin_user.email}")
        print(f"邮箱已验证: {admin_user.email_verified}")
        print(f"管理员权限: {admin_user.is_admin}")
        print(f"创建时间: {admin_user.created_at}")
        print("\n账户验证成功!")
        return True

if __name__ == '__main__':
    print("正在初始化管理员账户...\n")
    init_admin()
    print("\n")
    verify_admin()
