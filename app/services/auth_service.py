"""认证与访问控制服务。

这里集中处理密码哈希、邮箱/密码格式校验、JWT 生成与解析、
登录失败锁定、CSRF 校验，以及页面/API 路由的认证装饰器。
"""
import re
import bcrypt
import jwt
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, current_app
from app.models import User, UserLog

class AuthService:
    """无状态认证工具类。

    类方法本身不保存用户会话；会话状态放在 JWT、Cookie 和数据库字段中。
    """
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION = 15
    PASSWORD_MIN_LENGTH = 8

    @staticmethod
    def hash_password(password):
        """使用 bcrypt 哈希密码，数据库只保存哈希后的字符串。"""
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @staticmethod
    def verify_password(password, hashed):
        """校验明文密码和数据库中的 bcrypt 哈希是否匹配。"""
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

    @staticmethod
    def validate_email(email):
        """做基础邮箱格式校验，避免明显错误的数据进入注册流程。"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

    @staticmethod
    def validate_password(password):
        """校验密码强度：长度、大小写字母和数字都必须满足。"""
        if len(password) < AuthService.PASSWORD_MIN_LENGTH or len(password) > 20:
            return False, "密码长度需为8-20个字符"
        if not re.search(r'[A-Z]', password):
            return False, "密码需包含大写字母"
        if not re.search(r'[a-z]', password):
            return False, "密码需包含小写字母"
        if not re.search(r'\d', password):
            return False, "密码需包含数字"
        return True, "密码验证通过"

    @staticmethod
    def generate_token(user_id, remember=False):
        """生成 JWT 登录令牌。

        `remember=True` 时延长有效期；`jti` 用于给每个令牌一个唯一编号。
        """
        expiration = timedelta(days=7) if remember else timedelta(hours=24)
        payload = {
            'user_id': user_id,
            'exp': datetime.utcnow() + expiration,
            'iat': datetime.utcnow(),
            'jti': secrets.token_hex(16)
        }
        return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

    @staticmethod
    def decode_token(token):
        """解析并校验 JWT，过期或签名不合法时返回 None。"""
        try:
            payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    @staticmethod
    def generate_verification_code():
        """生成 6 位数字验证码，用于邮箱验证和找回密码。"""
        return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

    @staticmethod
    def generate_csrf_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def generate_reset_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def is_account_locked(user):
        """判断账户是否仍处于登录失败后的锁定时间内。"""
        if user.locked_until and user.locked_until > datetime.utcnow():
            remaining = (user.locked_until - datetime.utcnow()).seconds
            return True, remaining
        return False, 0

    @staticmethod
    def record_failed_login(user):
        """记录一次登录失败；超过阈值后锁定账号并写入安全日志。"""
        user.login_attempts += 1
        if user.login_attempts >= AuthService.MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=AuthService.LOCKOUT_DURATION)
            UserLog.log(
                user.id, UserLog.LOGIN_FAILED,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent'),
                details=f"账户已锁定，尝试次数: {user.login_attempts}",
                status='locked'
            )
        else:
            UserLog.log(
                user.id, UserLog.LOGIN_FAILED,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent'),
                details=f"登录失败，剩余尝试次数: {AuthService.MAX_LOGIN_ATTEMPTS - user.login_attempts}",
                status='failed'
            )
        user.save()

    @staticmethod
    def reset_login_attempts(user):
        user.login_attempts = 0
        user.locked_until = None
        user.save()


def token_required(f):
    """API 路由认证装饰器。

    前端需要在 Authorization 头中传入 `Bearer <token>`。
    校验通过后把当前用户挂到 `request.current_user`，供视图函数直接使用。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            return jsonify({'error': '缺少认证令牌'}), 401

        payload = AuthService.decode_token(token)
        if not payload:
            return jsonify({'error': '无效或已过期的令牌'}), 401

        user = User.get_by_id(payload['user_id'])
        if not user:
            return jsonify({'error': '用户不存在'}), 401

        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def csrf_protect(f):
    """写操作 CSRF 防护装饰器。

    对 POST/PUT/DELETE/PATCH 请求，要求请求头中的 X-CSRF-Token 与 Cookie 匹配。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
                csrf_token = request.headers.get('X-CSRF-Token')
                session_token = request.cookies.get('csrf_token')
                if not csrf_token or csrf_token != session_token:
                    return jsonify({'error': 'CSRF验证失败'}), 403
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'error': f'CSRF处理错误: {str(e)}'}), 500
    return decorated


def auth_required(f):
    """页面路由认证装饰器。

    页面访问使用 Cookie 中的 token；未登录或令牌失效时跳转到登录页。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import redirect, url_for
        token = request.cookies.get('token')
        if not token:
            return redirect('/login')
        
        payload = AuthService.decode_token(token)
        if not payload:
            return redirect('/login')
        
        user = User.get_by_id(payload['user_id'])
        if not user:
            return redirect('/login')
        
        request.current_user = user
        return f(*args, **kwargs)
    return decorated
