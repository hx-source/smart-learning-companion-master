"""
数据库模型模块
包含用户、学习记录、反馈和日志模型
"""
from datetime import datetime
from app.models import db

class User(db.Model):
    """用户表。

    保存登录信息、个人资料、邮箱验证状态、账号锁定状态，
    以及用户自己的 AI 服务配置。敏感字段如密码和 API Key 不应暴露给前端。
    """
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    email_verified = db.Column(db.Boolean, default=False)
    verification_code = db.Column(db.String(6))
    verification_expire = db.Column(db.DateTime)
    major = db.Column(db.String(100))
    grade = db.Column(db.String(20))
    avatar = db.Column(db.String(255), default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(45), nullable=True)

    reset_token = db.Column(db.String(255), nullable=True)
    reset_token_expire = db.Column(db.DateTime, nullable=True)

    # Legacy API key columns kept for old databases; current runtime uses local Ollama only.
    deepseek_api_key = db.Column(db.String(255), nullable=True)
    kimi_api_key = db.Column(db.String(255), nullable=True)
    zhipu_api_key = db.Column(db.String(255), nullable=True)
    use_ollama = db.Column(db.Boolean, default=True)
    ollama_model = db.Column(db.String(50), default='qwen2.5:7b')
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    role = db.Column(db.String(20), default='student', nullable=False, index=True)

    records = db.relationship('LearningRecord', backref='user', lazy='dynamic')

    def to_dict(self):
        """返回可安全发送给前端的用户信息，不包含密码、验证码和 API Key。"""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'email_verified': self.email_verified,
            'major': self.major,
            'grade': self.grade,
            'avatar': self.avatar,
            'created_at': self.created_at.strftime('%Y-%m-%d') if self.created_at else None,
            'use_ollama': self.use_ollama,
            'ollama_model': self.ollama_model,
            'is_admin': self.is_admin,
            'role': self.role or ('admin' if self.is_admin else 'student')
        }

    def has_role(self, *roles):
        role = self.role or ('admin' if self.is_admin else 'student')
        return role in roles or self.is_admin

    @staticmethod
    def get_by_id(user_id):
        return User.query.get(user_id)

    @staticmethod
    def get_by_username(username):
        return User.query.filter_by(username=username).first()

    @staticmethod
    def get_by_email(email):
        return User.query.filter_by(email=email).first()

    def save(self):
        """提交当前模型对象上的修改。"""
        db.session.commit()

class LearningRecord(db.Model):
    """学习记录表。

    每次用户提问后写入一条记录，用于历史会话、反馈统计和仪表盘展示。
    """
    __tablename__ = 'learning_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    ai_answer = db.Column(db.Text, nullable=False)
    sources = db.Column(db.Text)  # 参考知识ID列表，JSON格式
    session_id = db.Column(db.String(50), index=True)  # 会话ID
    is_helpful = db.Column(db.Integer, default=0)  # 0未知 1有用 2无用
    category = db.Column(db.String(50))  # 问题分类
    knowledge_base = db.Column(db.String(100))  # 使用的知识库名称
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # 复合索引：历史记录常按用户、时间、会话查询，提前建索引提升列表页性能。
    __table_args__ = (
        db.Index('idx_user_created', 'user_id', 'created_at'),
        db.Index('idx_session_user', 'session_id', 'user_id'),
    )
    
    def to_dict(self):
        """转换成前端历史记录列表需要的轻量结构。"""
        return {
            'id': self.id,
            'question': self.question,
            'ai_answer': self.ai_answer,
            'is_helpful': self.is_helpful,
            'category': self.category or '其他',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M')
            # 移除time_ago计算，减少CPU开销
        }
    
    def _get_time_ago(self):
        """获取相对时间"""
        now = datetime.utcnow()
        diff = now - self.created_at
        
        if diff.days > 0:
            return f"{diff.days}天前"
        elif diff.seconds >= 3600:
            return f"{diff.seconds // 3600}小时前"
        elif diff.seconds >= 60:
            return f"{diff.seconds // 60}分钟前"
        else:
            return "刚刚"



class ClassRoom(db.Model):
    """A teaching class/course group managed by a teacher."""
    __tablename__ = 'class_rooms'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship('User', backref=db.backref('class_rooms', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'teacher_id': self.teacher_id,
            'teacher_name': self.teacher.username if self.teacher else None,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None
        }


class ClassMember(db.Model):
    """Student membership in a class."""
    __tablename__ = 'class_members'

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class_rooms.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    class_room = db.relationship('ClassRoom', backref=db.backref('memberships', lazy='dynamic', cascade='all, delete-orphan'))
    student = db.relationship('User', backref=db.backref('class_memberships', lazy='dynamic', cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('class_id', 'student_id', name='uq_class_member_student'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'class_id': self.class_id,
            'class_name': self.class_room.name if self.class_room else None,
            'student_id': self.student_id,
            'student_name': self.student.username if self.student else None,
            'joined_at': self.joined_at.strftime('%Y-%m-%d %H:%M') if self.joined_at else None
        }


class ClassKnowledgeBase(db.Model):
    """Knowledge base owned by a teacher and attached to a class."""
    __tablename__ = 'class_knowledge_bases'

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class_rooms.id'), nullable=True, index=True)
    class_name = db.Column(db.String(100), nullable=False, index=True)
    knowledge_base = db.Column(db.String(100), unique=True, nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship('User', backref=db.backref('class_knowledge_bases', lazy='dynamic'))
    class_room = db.relationship('ClassRoom', backref=db.backref('knowledge_bases', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'class_id': self.class_id,
            'class_name': self.class_name,
            'knowledge_base': self.knowledge_base,
            'teacher_id': self.teacher_id,
            'teacher_name': self.teacher.username if self.teacher else None,
            'description': self.description,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None
        }


class UserKnowledgeBase(db.Model):
    """Knowledge base created by a user outside the teacher class publishing flow."""
    __tablename__ = 'user_knowledge_bases'

    id = db.Column(db.Integer, primary_key=True)
    knowledge_base = db.Column(db.String(100), unique=True, nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    owner_role = db.Column(db.String(20), default='student', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship('User', backref=db.backref('user_knowledge_bases', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'knowledge_base': self.knowledge_base,
            'owner_id': self.owner_id,
            'owner_name': self.owner.username if self.owner else None,
            'owner_role': self.owner_role,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None
        }


class UserLog(db.Model):
    """用户操作日志表。

    记录登录、登出、找回密码等安全相关行为，便于排查异常操作。
    """
    __tablename__ = 'user_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(50), nullable=False)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    details = db.Column(db.Text)
    status = db.Column(db.String(20), default='success')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def log(user_id, action, ip_address=None, user_agent=None, details=None, status='success'):
        """写入一条用户行为日志。"""
        log_entry = UserLog(
            user_id=user_id,
            action=action,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
            status=status
        )
        db.session.add(log_entry)
        db.session.commit()
        return log_entry

    LOGIN = 'login'
    LOGIN_FAILED = 'login_failed'
    LOGOUT = 'logout'
    REGISTER = 'register'
    EMAIL_VERIFY = 'email_verify'
    PASSWORD_RESET_REQUEST = 'password_reset_request'
    PASSWORD_RESET = 'password_reset'
    PASSWORD_CHANGE = 'password_change'
