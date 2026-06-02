"""
认证路由
"""
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, make_response, current_app
from app.models import db, User, UserLog
from app.services.auth_service import AuthService, token_required, csrf_protect
from app.services.email_service import EmailService
import jwt

bp = Blueprint('auth', __name__, url_prefix='/api/auth')

def generate_register_token(username, email):
    payload = {
        'username': username,
        'email': email,
        'purpose': 'register',
        'exp': datetime.utcnow() + timedelta(minutes=10),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

@bp.route('/csrf-token', methods=['GET'])
def get_csrf_token():
    csrf_token = AuthService.generate_csrf_token()
    response = make_response(jsonify({'csrf_token': csrf_token}))
    response.set_cookie('csrf_token', csrf_token, httponly=False, samesite='Lax')
    return response

@bp.route('/send-verification-code', methods=['POST'])
@csrf_protect
def send_verification_code():
    data = request.get_json()
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()

    if not username or not email:
        return jsonify({'error': '用户名和邮箱不能为空'}), 400

    if len(username) < 3 or len(username) > 50:
        return jsonify({'error': '用户名需在3-50个字符之间'}), 400

    if not username.isalnum() and '_' not in username:
        return jsonify({'error': '用户名只能包含字母、数字和下划线'}), 400

    if not AuthService.validate_email(email):
        return jsonify({'error': '邮箱格式不正确'}), 400

    existing_user_by_name = User.get_by_username(username)
    if existing_user_by_name and existing_user_by_name.email_verified:
        return jsonify({'error': '用户名已存在'}), 409

    existing_user_by_email = User.get_by_email(email)
    if existing_user_by_email and existing_user_by_email.email_verified:
        return jsonify({'error': '该邮箱已被注册'}), 409

    code = AuthService.generate_verification_code()

    base_url = request.host_url.rstrip('/')
    success, message = EmailService.send_verification_email(email, username, code, base_url)

    if not success:
        return jsonify({'error': f'验证码发送失败: {message}'}), 500

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        existing_user.username = username
        existing_user.verification_code = code
        existing_user.verification_expire = datetime.utcnow() + timedelta(minutes=10)
        existing_user.email_verified = False
    else:
        temp_user = User(
            username=username,
            email=email,
            password='',
            verification_code=code,
            verification_expire=datetime.utcnow() + timedelta(minutes=10),
            email_verified=False
        )
        db.session.add(temp_user)
    db.session.commit()

    register_token = generate_register_token(username, email)

    return jsonify({
        'message': '验证码已发送，请查收邮件',
        'register_token': register_token,
        'expires_in': 600
    })


@bp.route('/register', methods=['POST'])
@csrf_protect
def register():
    data = request.get_json()
    register_token = data.get('register_token')
    verification_code = data.get('verification_code', '').strip()
    password = data.get('password')
    confirm_password = data.get('confirm_password')

    if not register_token or not verification_code or not password:
        return jsonify({'error': '缺少必要参数'}), 400

    try:
        payload = jwt.decode(register_token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
        if payload.get('purpose') != 'register':
            return jsonify({'error': '无效的注册令牌'}), 400
    except jwt.ExpiredSignatureError:
        return jsonify({'error': '注册已超时，请重新获取验证码'}), 400
    except jwt.InvalidTokenError:
        return jsonify({'error': '无效的注册令牌'}), 400

    username = payload.get('username')
    email = payload.get('email')

    existing_by_username = User.get_by_username(username)
    if existing_by_username and existing_by_username.email_verified:
        return jsonify({'error': '用户名已存在'}), 409

    existing_by_email = User.get_by_email(email)
    if existing_by_email and existing_by_email.email_verified:
        return jsonify({'error': '该邮箱已被注册'}), 409

    if not verification_code:
        return jsonify({'error': '请输入验证码'}), 400

    if not verification_code.isdigit() or len(verification_code) != 6:
        return jsonify({'error': '验证码格式错误'}), 400

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        if existing_user.verification_code != verification_code:
            return jsonify({'error': '验证码错误'}), 400
        if existing_user.verification_expire < datetime.utcnow():
            return jsonify({'error': '验证码已过期，请重新获取'}), 400
        user = existing_user
    else:
        return jsonify({'error': '请先获取验证码'}), 400

    valid, msg = AuthService.validate_password(password)
    if not valid:
        return jsonify({'error': msg}), 400

    if password != confirm_password:
        return jsonify({'error': '两次密码输入不一致'}), 400

    user.password = AuthService.hash_password(password)
    user.email_verified = True
    user.verification_code = None
    user.verification_expire = None
    db.session.commit()

    UserLog.log(
        user.id, UserLog.REGISTER,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details=f"新用户注册: {username}",
        status='success'
    )

    return jsonify({
        'message': '注册成功',
        'email': email
    }), 201


@bp.route('/verify-email', methods=['GET'])
def verify_email():
    code = request.args.get('code')
    if not code:
        return jsonify({'error': '缺少验证码'}), 400

    user = User.query.filter_by(verification_code=code).first()
    if not user:
        return jsonify({'error': '无效的验证码'}), 400

    if user.email_verified:
        return jsonify({'error': '邮箱已验证'}), 400

    if user.verification_expire < datetime.utcnow():
        return jsonify({'error': '验证码已过期，请重新注册'}), 400

    user.email_verified = True
    user.verification_code = None
    user.verification_expire = None
    db.session.commit()

    UserLog.log(
        user.id, UserLog.EMAIL_VERIFY,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details="邮箱验证成功",
        status='success'
    )

    return jsonify({'message': '邮箱验证成功'})


@bp.route('/login', methods=['POST'])
@csrf_protect
def login():
    data = request.get_json()
    username_or_email = data.get('username', '').strip()
    password = data.get('password')
    remember = data.get('remember', False)

    if not username_or_email or not password:
        return jsonify({'error': '用户名/邮箱和密码不能为空'}), 400

    user = User.get_by_username(username_or_email)
    if not user:
        user = User.get_by_email(username_or_email)

    if not user:
        UserLog.log(
            None, UserLog.LOGIN_FAILED,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent'),
            details=f"不存在的用户尝试登录: {username_or_email}",
            status='failed'
        )
        return jsonify({'error': '用户名/邮箱或密码错误'}), 401

    locked, remaining = AuthService.is_account_locked(user)
    if locked:
        UserLog.log(
            user.id, UserLog.LOGIN_FAILED,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent'),
            details=f"账户已锁定，剩余锁定时间: {remaining}秒",
            status='locked'
        )
        return jsonify({
            'error': f'账户已锁定，请{remaining}秒后再试'
        }), 423

    if not AuthService.verify_password(password, user.password):
        AuthService.record_failed_login(user)
        return jsonify({
            'error': '用户名/邮箱或密码错误',
            'remaining_attempts': AuthService.MAX_LOGIN_ATTEMPTS - user.login_attempts
        }), 401

    AuthService.reset_login_attempts(user)

    user.last_login = datetime.utcnow()
    user.last_login_ip = request.remote_addr
    db.session.commit()

    token = AuthService.generate_token(user.id, remember)

    UserLog.log(
        user.id, UserLog.LOGIN,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details=f"用户登录，记住我: {remember}",
        status='success'
    )

    response = make_response(jsonify({
        'message': '登录成功',
        'token': token,
        'user': user.to_dict()
    }))

    response.set_cookie('token', token, httponly=True, samesite='Lax',
                        max_age=7 * 24 * 60 * 60 if remember else 24 * 60 * 60)

    csrf_token = AuthService.generate_csrf_token()
    response.set_cookie('csrf_token', csrf_token, httponly=False, samesite='Lax')

    return response


@bp.route('/logout', methods=['POST'])
@token_required
def logout():
    UserLog.log(
        request.current_user.id, UserLog.LOGOUT,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details="用户登出",
        status='success'
    )

    response = make_response(jsonify({'message': '登出成功'}))
    response.delete_cookie('token')
    response.delete_cookie('csrf_token')
    return response


@bp.route('/me', methods=['GET'])
@token_required
def get_current_user():
    return jsonify(request.current_user.to_dict())


@bp.route('/password/reset/request', methods=['POST'])
@csrf_protect
def request_password_reset():
    email = request.get_json().get('email', '').strip().lower()

    if not email or not AuthService.validate_email(email):
        return jsonify({'error': '请提供有效的邮箱地址'}), 400

    user = User.get_by_email(email)
    if not user:
        return jsonify({'error': '该邮箱未注册'}), 404

    code = AuthService.generate_verification_code()
    user.verification_code = code
    user.verification_expire = datetime.utcnow() + timedelta(minutes=10)
    user.reset_token = AuthService.generate_reset_token()
    user.reset_token_expire = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    base_url = request.host_url.rstrip('/')
    success, message = EmailService.send_password_reset_code(email, user.username, code)

    if not success:
        UserLog.log(
            user.id, UserLog.PASSWORD_RESET_REQUEST,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent'),
            details=f"密码重置验证码发送失败: {message}",
            status='failed'
        )
        return jsonify({'error': '邮件发送失败，请稍后重试'}), 500

    UserLog.log(
        user.id, UserLog.PASSWORD_RESET_REQUEST,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details="密码重置验证码已发送",
        status='success'
    )

    return jsonify({
        'message': '验证码已发送到您的邮箱',
        'reset_token': user.reset_token
    })


@bp.route('/password/reset/verify', methods=['POST'])
@csrf_protect
def verify_reset_code():
    data = request.get_json()
    reset_token = data.get('reset_token')
    code = data.get('code', '').strip()

    if not reset_token or not code:
        return jsonify({'error': '缺少必要参数'}), 400

    if not code.isdigit() or len(code) != 6:
        return jsonify({'error': '验证码格式错误'}), 400

    user = User.query.filter_by(reset_token=reset_token).first()
    if not user:
        return jsonify({'error': '无效的重置令牌'}), 400

    if user.reset_token_expire < datetime.utcnow():
        return jsonify({'error': '验证码已过期，请重新获取'}), 400

    if user.verification_code != code:
        return jsonify({'error': '验证码错误'}), 400

    return jsonify({'message': '验证码验证成功'})


@bp.route('/password/reset', methods=['POST'])
@csrf_protect
def reset_password():
    data = request.get_json()
    reset_token = data.get('reset_token')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')

    if not reset_token or not new_password:
        return jsonify({'error': '缺少必要参数'}), 400

    valid, msg = AuthService.validate_password(new_password)
    if not valid:
        return jsonify({'error': msg}), 400

    if new_password != confirm_password:
        return jsonify({'error': '两次密码输入不一致'}), 400

    user = User.query.filter_by(reset_token=reset_token).first()

    if not user:
        return jsonify({'error': '无效的重置令牌'}), 400

    if user.reset_token_expire < datetime.utcnow():
        return jsonify({'error': '重置链接已过期，请重新申请'}), 400

    user.password = AuthService.hash_password(new_password)
    user.verification_code = None
    user.reset_token = None
    user.reset_token_expire = None
    AuthService.reset_login_attempts(user)
    db.session.commit()

    UserLog.log(
        user.id, UserLog.PASSWORD_RESET,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details="密码重置成功",
        status='success'
    )

    return jsonify({'message': '密码重置成功'})


@bp.route('/password/change', methods=['POST'])
@token_required
@csrf_protect
def change_password():
    data = request.get_json()
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')

    if not current_password or not new_password:
        return jsonify({'error': '请填写所有密码字段'}), 400

    user = request.current_user

    if not AuthService.verify_password(current_password, user.password):
        return jsonify({'error': '当前密码错误'}), 400

    valid, msg = AuthService.validate_password(new_password)
    if not valid:
        return jsonify({'error': msg}), 400

    if new_password != confirm_password:
        return jsonify({'error': '两次密码输入不一致'}), 400

    if AuthService.verify_password(new_password, user.password):
        return jsonify({'error': '新密码不能与当前密码相同'}), 400

    user.password = AuthService.hash_password(new_password)
    db.session.commit()

    AuthService.reset_login_attempts(user)

    UserLog.log(
        user.id, UserLog.PASSWORD_CHANGE,
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details="密码修改成功",
        status='success'
    )

    return jsonify({'message': '密码修改成功'})


@bp.route('/api-keys', methods=['GET'])
@token_required
def get_api_keys():
    """获取用户的API密钥配置"""
    user = request.current_user
    return jsonify({
        'code': 200,
        'deepseek_api_key': user.deepseek_api_key,
        'kimi_api_key': user.kimi_api_key,
        'zhipu_api_key': user.zhipu_api_key
    })


@bp.route('/api-keys', methods=['POST'])
@token_required
def update_api_keys():
    """更新用户的API密钥配置"""
    data = request.get_json()
    user = request.current_user

    # 更新API密钥
    user.deepseek_api_key = data.get('deepseek_api_key')
    user.kimi_api_key = data.get('kimi_api_key')
    user.zhipu_api_key = data.get('zhipu_api_key')

    db.session.commit()

    UserLog.log(
        user.id, 'API_KEYS_UPDATE',
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
        details="API密钥配置已更新",
        status='success'
    )

    return jsonify({'message': 'API密钥配置已更新'})


@bp.route('/resend-verification', methods=['POST'])
@csrf_protect
def resend_verification():
    email = request.get_json().get('email', '').strip().lower()

    if not email or not AuthService.validate_email(email):
        return jsonify({'error': '请提供有效的邮箱地址'}), 400

    user = User.get_by_email(email)

    if not user:
        return jsonify({'error': '该邮箱未注册'}), 404

    if user.email_verified:
        return jsonify({'error': '该邮箱已验证'}), 400

    verification_code = AuthService.generate_verification_code()
    user.verification_code = verification_code
    user.verification_expire = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()

    base_url = request.host_url.rstrip('/')
    success, message = EmailService.send_verification_email(email, user.username, verification_code, base_url)

    if not success:
        return jsonify({'error': f'邮件发送失败: {message}'}), 500

    return jsonify({'message': '验证邮件已重新发送'})
