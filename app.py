"""Flask application entry for Smart Learning Companion."""

from collections import defaultdict
from datetime import datetime
import json
import os
import uuid

import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS
from sqlalchemy import inspect, text
from werkzeug.utils import secure_filename

from config import Config
from app.models import db, User, LearningRecord, ClassKnowledgeBase, UserKnowledgeBase
from app.services.ai_service import AIService
from app.services.auth_service import AuthService, auth_required, token_required, admin_required, role_required
from app.services.knowledge_service import QAModule

api_cache = defaultdict(dict)
ai_service = AIService()
VALID_ROLES = {'student', 'teacher', 'admin'}


def create_app():
    app_root = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(app_root, 'templates'),
        static_folder=os.path.join(app_root, 'static')
    )
    app.config.from_object(Config)
    CORS(app, resources={r'/api/*': {'origins': '*'}})
    db.init_app(app)

    from app.routes.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    os.makedirs(os.path.join(app_root, app.config['AVATAR_UPLOAD_FOLDER']), exist_ok=True)
    os.makedirs(os.path.join(app_root, app.config['DOCUMENT_UPLOAD_FOLDER']), exist_ok=True)

    @app.errorhandler(413)
    def request_entity_too_large(error):
        max_size_mb = app.config.get('MAX_CONTENT_LENGTH', 100 * 1024 * 1024) // (1024 * 1024)
        return jsonify({'code': 413, 'msg': f'????????? {max_size_mb}MB'}), 413

    return app


app = create_app()
with app.app_context():
    ai_service.init_config()


def _current_user_role():
    user = getattr(request, 'current_user', None)
    if not user:
        return 'anonymous'
    return user.role or ('admin' if user.is_admin else 'student')


def _sanitize_knowledge_base_name(value):
    raw = (value or '').strip().replace(' ', '_')
    safe = ''.join(ch for ch in raw if ch.isalnum() or ch in {'_', '-'})
    return (safe or 'class')[:100]


def _safe_upload_filename(filename):
    original = os.path.basename(filename or '').strip()
    stem, ext = os.path.splitext(original)
    ext = ext.lower()
    safe_stem = ''.join(ch if ch.isalnum() or ch in {'_', '-', ' ', '(', ')', '（', '）'} else '_' for ch in stem).strip()
    safe_stem = safe_stem.replace(' ', '_')[:120] or f'document_{uuid.uuid4().hex[:8]}'
    return f'{safe_stem}{ext}'


def _class_knowledge_base_name(class_name, teacher_id):
    return f'class_{teacher_id}_{_sanitize_knowledge_base_name(class_name)}'


def _is_class_knowledge_base(knowledge_base):
    if not knowledge_base:
        return False
    mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    return mapping is not None or knowledge_base.startswith('class_')


def _ensure_user_knowledge_base(knowledge_base):
    if not knowledge_base or knowledge_base == 'default' or _is_class_knowledge_base(knowledge_base):
        return None
    owner = UserKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if not owner:
        owner = UserKnowledgeBase(
            knowledge_base=knowledge_base,
            owner_id=request.current_user.id,
            owner_role=_current_user_role()
        )
        db.session.add(owner)
        db.session.commit()
    return owner


def _can_manage_knowledge_base(knowledge_base):
    if not knowledge_base or knowledge_base == 'default':
        return False
    user = request.current_user
    if user.is_admin or _current_user_role() == 'admin':
        return True
    mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if mapping:
        return _current_user_role() == 'teacher' and mapping.teacher_id == user.id
    if knowledge_base.startswith('class_'):
        return _current_user_role() == 'teacher' and knowledge_base.startswith(f'class_{user.id}_')
    owner = UserKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if owner:
        return owner.owner_id == user.id
    return True


def _knowledge_base_item(knowledge_base, class_map=None, owner_map=None):
    class_map = class_map or {}
    owner_map = owner_map or {}
    class_mapping = class_map.get(knowledge_base)
    if class_mapping:
        can_manage = _can_manage_knowledge_base(knowledge_base)
        owner_name = class_mapping.teacher.username if class_mapping.teacher else None
        return {
            'name': knowledge_base,
            'display_name': class_mapping.class_name,
            'type': 'class',
            'description': class_mapping.description or '',
            'teacher_id': class_mapping.teacher_id,
            'teacher_name': owner_name,
            'owner_id': class_mapping.teacher_id,
            'owner_name': owner_name,
            'can_manage': can_manage,
            'can_upload': can_manage,
            'can_rename': can_manage,
            'can_delete': can_manage
        }

    owner = owner_map.get(knowledge_base)
    if owner:
        can_manage = _can_manage_knowledge_base(knowledge_base)
        return {
            'name': knowledge_base,
            'display_name': knowledge_base,
            'type': 'personal',
            'owner_id': owner.owner_id,
            'owner_name': owner.owner.username if owner.owner else None,
            'owner_role': owner.owner_role,
            'can_manage': can_manage,
            'can_upload': can_manage,
            'can_rename': can_manage,
            'can_delete': can_manage
        }

    can_manage = _can_manage_knowledge_base(knowledge_base)
    return {
        'name': knowledge_base,
        'display_name': knowledge_base,
        'type': 'personal',
        'owner_id': request.current_user.id if can_manage else None,
        'owner_name': request.current_user.username if can_manage else None,
        'owner_role': _current_user_role() if can_manage else None,
        'can_manage': can_manage,
        'can_upload': can_manage,
        'can_rename': can_manage,
        'can_delete': can_manage
    }


def _knowledge_base_display_name(knowledge_base):
    if not knowledge_base:
        return ''
    mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if mapping:
        return mapping.class_name
    return knowledge_base


def _format_answer_sources(sources, knowledge_base):
    display_name = _knowledge_base_display_name(knowledge_base)
    formatted = []
    for index, source in enumerate(sources or [], start=1):
        metadata = source.get('metadata') or {}
        file_path = metadata.get('file_path') or source.get('file_path') or source.get('source') or ''
        item_knowledge_base = metadata.get('knowledge_base') or knowledge_base
        formatted.append({
            'index': index,
            'file_name': os.path.basename(file_path) if file_path else '未知文件',
            'file_path': file_path,
            'knowledge_base': item_knowledge_base,
            'knowledge_base_name': _knowledge_base_display_name(item_knowledge_base) or display_name,
            'chunk_index': metadata.get('chunk_index'),
            'content': source.get('content') or '',
            'relevance': source.get('relevance'),
            'rerank_score': source.get('rerank_score')
        })
    return formatted


def _ensure_runtime_schema():
    try:
        inspector = inspect(db.engine)
        if not inspector.has_table('users'):
            return
        if not inspector.has_table('class_knowledge_bases'):
            ClassKnowledgeBase.__table__.create(db.engine)
        if not inspector.has_table('user_knowledge_bases'):
            UserKnowledgeBase.__table__.create(db.engine)
        user_columns = {column['name'] for column in inspector.get_columns('users')}
        with db.engine.begin() as conn:
            if 'role' not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'student'"))
            conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = TRUE AND (role IS NULL OR role <> 'admin')"))
    except Exception as exc:
        print(f'Runtime schema check skipped: {exc}')


with app.app_context():
    _ensure_runtime_schema()


@app.route('/')
@auth_required
def index():
    return render_template('index.html')


@app.route('/records')
@auth_required
def records():
    return render_template('records.html')


@app.route('/profile')
@auth_required
def profile():
    return render_template('profile.html')


@app.route('/admin')
@auth_required
def admin_console():
    if not request.current_user.has_role('admin'):
        return jsonify({'code': 403, 'msg': 'Permission denied'}), 403
    return render_template('admin.html')


@app.route('/knowledge-base')
@auth_required
def knowledge_base_page():
    return render_template('knowledge-base.html')


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/register')
def register_page():
    return render_template('register.html')


@app.route('/forgot-password')
def forgot_password():
    return render_template('forgot-password.html')


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_list_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({'code': 200, 'data': [user.to_dict() for user in users]})


@app.route('/api/admin/users', methods=['POST'])
@admin_required
def admin_create_user():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = data.get('role') or 'student'
    email = (data.get('email') or '').strip() or None
    if role not in VALID_ROLES:
        return jsonify({'code': 400, 'msg': 'invalid role'}), 400
    if not username or not password:
        return jsonify({'code': 400, 'msg': 'username and password are required'}), 400
    if User.get_by_username(username):
        return jsonify({'code': 400, 'msg': 'username exists'}), 400
    if email and User.get_by_email(email):
        return jsonify({'code': 400, 'msg': 'email exists'}), 400
    valid, msg = AuthService.validate_password(password)
    if not valid:
        return jsonify({'code': 400, 'msg': msg}), 400
    user = User(
        username=username,
        password=AuthService.hash_password(password),
        email=email,
        email_verified=bool(email),
        role=role,
        is_admin=role == 'admin'
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({'code': 200, 'data': user.to_dict()})


@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
@admin_required
def admin_update_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'code': 404, 'msg': 'user not found'}), 404
    data = request.json or {}
    if 'username' in data:
        user.username = (data.get('username') or '').strip()
    if 'email' in data:
        user.email = (data.get('email') or '').strip() or None
    if 'role' in data:
        role = data.get('role')
        if role not in VALID_ROLES:
            return jsonify({'code': 400, 'msg': 'invalid role'}), 400
        user.role = role
        user.is_admin = role == 'admin'
    if data.get('password'):
        valid, msg = AuthService.validate_password(data['password'])
        if not valid:
            return jsonify({'code': 400, 'msg': msg}), 400
        user.password = AuthService.hash_password(data['password'])
    db.session.commit()
    return jsonify({'code': 200, 'data': user.to_dict()})


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    if user_id == request.current_user.id:
        return jsonify({'code': 400, 'msg': 'cannot delete current admin'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'code': 404, 'msg': 'user not found'}), 404
    LearningRecord.query.filter_by(user_id=user_id).delete()
    ClassKnowledgeBase.query.filter_by(teacher_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'deleted'})


@app.route('/api/admin/class-knowledge-bases', methods=['GET'])
@admin_required
def admin_list_class_knowledge_bases():
    items = ClassKnowledgeBase.query.order_by(ClassKnowledgeBase.created_at.desc()).all()
    return jsonify({'code': 200, 'data': [item.to_dict() for item in items]})


@app.route('/api/teacher/class-knowledge-bases', methods=['GET'])
@role_required('teacher', 'admin')
def teacher_list_class_knowledge_bases():
    query = ClassKnowledgeBase.query
    if _current_user_role() == 'teacher' and not request.current_user.is_admin:
        query = query.filter_by(teacher_id=request.current_user.id)
    items = query.order_by(ClassKnowledgeBase.created_at.desc()).all()
    return jsonify({'code': 200, 'data': [item.to_dict() for item in items]})


@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    data = request.json or {}
    record = LearningRecord.query.get(data.get('record_id'))
    if not record:
        return jsonify({'code': 404, 'msg': '?????'}), 404
    record.is_helpful = data.get('is_helpful')
    db.session.commit()
    api_cache.clear()
    return jsonify({'code': 200, 'msg': '????'})


@app.route('/api/records/sessions', methods=['GET'])
def get_sessions():
    user_id = request.args.get('user_id', 1, type=int)
    records = LearningRecord.query.filter_by(user_id=user_id).order_by(LearningRecord.created_at.desc()).limit(100).all()
    sessions = {}
    for record in records:
        sid = record.session_id or f'legacy_{record.id}'
        if sid not in sessions:
            sessions[sid] = {
                'session_id': sid,
                'first_question': record.question[:50] + '...' if len(record.question) > 50 else record.question,
                'count': 0,
                'created_at': record.created_at.strftime('%Y-%m-%d %H:%M'),
                'time_ago': record._get_time_ago()
            }
        sessions[sid]['count'] += 1
    return jsonify({'code': 200, 'data': list(sessions.values())[:20]})


@app.route('/api/records/session/<session_id>', methods=['GET'])
def get_session_detail(session_id):
    user_id = request.args.get('user_id', 1, type=int)
    if session_id.startswith('legacy_'):
        records = LearningRecord.query.filter_by(id=int(session_id.replace('legacy_', '')), user_id=user_id).all()
    else:
        records = LearningRecord.query.filter_by(session_id=session_id, user_id=user_id).order_by(LearningRecord.created_at.asc()).all()
    return jsonify({'code': 200, 'data': [record.to_dict() for record in records]})


@app.route('/api/records/session/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    data = request.json or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'code': 400, 'msg': '????ID'}), 400
    LearningRecord.query.filter_by(user_id=user_id, session_id=session_id).delete()
    db.session.commit()
    api_cache.clear()
    return jsonify({'code': 200, 'msg': '????'})


@app.route('/api/records', methods=['GET'])
def get_records():
    user_id = request.args.get('user_id', 1, type=int)
    page = request.args.get('page', 1, type=int)
    pagination = LearningRecord.query.filter_by(user_id=user_id).order_by(LearningRecord.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return jsonify({'code': 200, 'data': {
        'items': [record.to_dict() for record in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    }})


@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    data = request.json or {}
    user_id = data.get('user_id')
    record = LearningRecord.query.filter_by(id=record_id, user_id=user_id).first()
    if not record:
        return jsonify({'code': 404, 'msg': '?????'}), 404
    db.session.delete(record)
    db.session.commit()
    api_cache.clear()
    return jsonify({'code': 200, 'msg': '????'})


@app.route('/api/ollama/status', methods=['GET'])
def ollama_status():
    base_url = Config.OLLAMA_BASE_URL
    status = {
        'enabled': Config.USE_OLLAMA,
        'base_url': base_url,
        'configured_model': Config.OLLAMA_MODEL,
        'connected': False,
        'available_models': []
    }
    if Config.USE_OLLAMA:
        try:
            response = requests.get(f'{base_url}/api/tags', timeout=2)
            if response.status_code == 200:
                data = response.json()
                status['connected'] = True
                status['available_models'] = [m['name'] for m in data.get('models', [])]
        except Exception:
            pass
    return jsonify({'code': 200, 'data': status})


@app.route('/api/user/profile', methods=['GET'])
@token_required
def get_profile():
    return jsonify({'code': 200, 'data': request.current_user.to_dict()})


@app.route('/api/dashboard', methods=['GET'])
@token_required
def dashboard():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'code': 400, 'msg': '????ID'}), 400
    if request.current_user.id != user_id and not request.current_user.has_role('admin'):
        return jsonify({'code': 403, 'msg': '??????????'}), 403
    user = User.query.get(user_id)
    if not user:
        return jsonify({'code': 404, 'msg': '?????'}), 404
    model_status = ollama_status().get_json()['data']
    return jsonify({'code': 200, 'data': {'user': user.to_dict(), 'model_status': model_status}})


@app.route('/api/user/profile', methods=['PUT'])
@token_required
def update_profile():
    user_id = request.form.get('user_id')
    if not user_id:
        return jsonify({'code': 400, 'msg': '????ID'}), 400
    user_id = int(user_id)
    if request.current_user.id != user_id and not request.current_user.has_role('admin'):
        return jsonify({'code': 403, 'msg': '?????????'}), 403
    user = User.query.get(user_id)
    if not user:
        return jsonify({'code': 404, 'msg': '?????'}), 404
    for field in ['username', 'email', 'major', 'grade']:
        if field in request.form:
            setattr(user, field, request.form[field])
    if 'avatar' in request.files and request.files['avatar'].filename:
        avatar_file = request.files['avatar']
        filename = secure_filename(f'{uuid.uuid4()}_{avatar_file.filename}')
        upload_dir = os.path.join(app.root_path, app.config['AVATAR_UPLOAD_FOLDER'])
        os.makedirs(upload_dir, exist_ok=True)
        avatar_file.save(os.path.join(upload_dir, filename))
        user.avatar = filename
    db.session.commit()
    return jsonify({'code': 200, 'data': user.to_dict()})


@app.route('/api/knowledge-base/upload', methods=['POST'])
@token_required
def upload_knowledge_file():
    if 'file' not in request.files:
        return jsonify({'code': 400, 'msg': '?????'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'code': 400, 'msg': '?????'}), 400
    knowledge_base = (request.form.get('knowledge_base') or '').strip()
    class_name = (request.form.get('class_name') or '').strip()
    description = (request.form.get('description') or '').strip() or None
    current_role = _current_user_role()
    if not knowledge_base and not class_name:
        return jsonify({'code': 400, 'msg': '?????????????'}), 400
    if class_name:
        if current_role not in {'teacher', 'admin'}:
            return jsonify({'code': 403, 'msg': 'Only teachers and admins can publish class knowledge bases'}), 403
        knowledge_base = _class_knowledge_base_name(class_name, request.current_user.id)
        mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
        if not mapping:
            mapping = ClassKnowledgeBase(class_name=class_name, knowledge_base=knowledge_base, teacher_id=request.current_user.id, description=description)
            db.session.add(mapping)
            db.session.commit()
        elif request.current_user.id != mapping.teacher_id and current_role != 'admin':
            return jsonify({'code': 403, 'msg': 'Cannot upload to another teacher class knowledge base'}), 403
    elif not _can_manage_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot upload to this knowledge base'}), 403
    else:
        _ensure_user_knowledge_base(knowledge_base)

    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in {'.pdf', '.txt', '.docx'}:
        return jsonify({'code': 400, 'msg': 'unsupported file type, only PDF/TXT/DOCX allowed'}), 400
    upload_dir = os.path.join(app.root_path, app.config['DOCUMENT_UPLOAD_FOLDER'], knowledge_base)
    os.makedirs(upload_dir, exist_ok=True)
    saved_filename = _safe_upload_filename(file.filename)
    filename = os.path.join(upload_dir, saved_filename)
    file.save(filename)
    kb_instance = QAModule(knowledge_base)
    success = kb_instance.add_document(filename, metadata={
        'uploaded_by': request.current_user.id,
        'uploader_role': current_role,
        'class_name': class_name,
        'knowledge_base': knowledge_base
    })
    if not success:
        detail = getattr(kb_instance, 'last_error', '') or '未知错误'
        if 'No module named' in detail and 'docx' in detail:
            detail = '服务器缺少 python-docx 依赖，无法解析 Word 文档。请执行 pip install python-docx 后重启服务。'
        return jsonify({'code': 500, 'msg': f'添加文件到知识库失败：{detail}'}), 500
    return jsonify({'code': 200, 'msg': 'file added to knowledge base', 'data': {'filename': saved_filename, 'knowledge_base': knowledge_base}})


@app.route('/api/knowledge-base/stats', methods=['GET'])
@token_required
def get_knowledge_base_stats():
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    return jsonify({'code': 200, 'data': QAModule(knowledge_base).get_stats()})


@app.route('/api/knowledge-base/delete', methods=['DELETE'])
@token_required
def delete_knowledge_base():
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    if not _can_manage_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot delete this knowledge base'}), 403
    QAModule(knowledge_base).delete_knowledge_base()
    ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).delete()
    UserKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).delete()
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'knowledge base deleted'})


@app.route('/api/knowledge-base/rename', methods=['POST'])
@token_required
def rename_knowledge_base():
    data = request.json or {}
    old_name = data.get('old_name', '')
    new_name = data.get('new_name', '')
    if not old_name or not new_name:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    if '/' in new_name or '\\' in new_name or '.' in new_name:
        return jsonify({'code': 400, 'msg': 'knowledge base name cannot contain slash or dot'}), 400
    if not _can_manage_knowledge_base(old_name):
        return jsonify({'code': 403, 'msg': 'Cannot rename this knowledge base'}), 403
    QAModule(old_name).rename_knowledge_base(new_name)
    class_mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=old_name).first()
    if class_mapping:
        class_mapping.knowledge_base = new_name
        class_mapping.class_name = new_name
    user_mapping = UserKnowledgeBase.query.filter_by(knowledge_base=old_name).first()
    if user_mapping:
        user_mapping.knowledge_base = new_name
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'knowledge base renamed'})


@app.route('/api/knowledge-base/create', methods=['POST'])
@token_required
def create_knowledge_base():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    if '/' in name or '\\' in name or '.' in name:
        return jsonify({'code': 400, 'msg': 'knowledge base name cannot contain slash or dot'}), 400
    if not _can_manage_knowledge_base(name):
        return jsonify({'code': 403, 'msg': 'Cannot create or modify this knowledge base'}), 403
    QAModule(name)
    _ensure_user_knowledge_base(name)
    return jsonify({'code': 200, 'msg': 'knowledge base created'})


@app.route('/api/knowledge-base/class/create', methods=['POST'])
@role_required('teacher', 'admin')
def create_class_knowledge_base():
    data = request.json or {}
    class_name = (data.get('class_name') or '').strip()
    description = (data.get('description') or '').strip() or None
    if not class_name:
        return jsonify({'code': 400, 'msg': 'knowledge base deleted'}), 400
    knowledge_base = _class_knowledge_base_name(class_name, request.current_user.id)
    mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if not mapping:
        mapping = ClassKnowledgeBase(class_name=class_name, knowledge_base=knowledge_base, teacher_id=request.current_user.id, description=description)
        db.session.add(mapping)
        db.session.commit()
    QAModule(knowledge_base)
    return jsonify({'code': 200, 'msg': '????????', 'data': mapping.to_dict()})


@app.route('/api/knowledge-base/list', methods=['GET'])
@token_required
def get_knowledge_base_list():
    knowledge_bases = QAModule.get_all_instances()
    for file in os.listdir('.'):
        if file.endswith('_vector_store.pkl'):
            kb_name = file.replace('knowledge_base_', '').replace('_vector_store.pkl', '')
            if kb_name != 'default' and kb_name not in knowledge_bases:
                knowledge_bases.append(kb_name)
                QAModule(kb_name)
    owned_kbs = UserKnowledgeBase.query.all()
    owned_by_name = {item.knowledge_base: item for item in owned_kbs}
    class_kbs = ClassKnowledgeBase.query.order_by(ClassKnowledgeBase.created_at.desc()).all()
    class_by_name = {item.knowledge_base: item for item in class_kbs}
    visible = []
    for kb_name in knowledge_bases:
        if kb_name == 'default':
            continue
        owner = owned_by_name.get(kb_name)
        if owner and owner.owner_id != request.current_user.id and not request.current_user.has_role('admin'):
            continue
        if kb_name.startswith('class_') and kb_name not in class_by_name:
            continue
        visible.append(kb_name)
    for item in class_kbs:
        if item.knowledge_base not in visible:
            visible.append(item.knowledge_base)
    items = [_knowledge_base_item(name, class_by_name, owned_by_name) for name in visible if name != 'default']
    return jsonify({
        'code': 200,
        'data': [item['name'] for item in items],
        'items': items,
        'class_knowledge_bases': [item.to_dict() for item in class_kbs],
        'user_knowledge_bases': [item.to_dict() for item in owned_kbs if item.owner_id == request.current_user.id or request.current_user.has_role('admin')]
    })


@app.route('/api/knowledge-base/vectors', methods=['GET'])
@token_required
def get_knowledge_base_vectors():
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    vectors = QAModule(knowledge_base).get_vectors(
        page=request.args.get('page', 1, type=int),
        page_size=request.args.get('page_size', 10, type=int),
        source=request.args.get('source')
    )
    return jsonify({'code': 200, 'data': vectors})


@app.route('/api/knowledge-base/sources', methods=['GET'])
@token_required
def get_knowledge_base_sources():
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    return jsonify({'code': 200, 'data': QAModule(knowledge_base).get_file_sources()})


@app.route('/api/knowledge-base/sources/<path:file_path>', methods=['DELETE'])
@token_required
def delete_knowledge_base_source(file_path):
    import urllib.parse
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    if not _can_manage_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot delete from this knowledge base'}), 403
    file_path = urllib.parse.unquote(file_path)
    deleted_count = QAModule(knowledge_base).delete_by_source(file_path)
    upload_file = os.path.join(app.root_path, app.config['DOCUMENT_UPLOAD_FOLDER'], knowledge_base, os.path.basename(file_path))
    if os.path.exists(upload_file):
        os.remove(upload_file)
    return jsonify({'code': 200, 'msg': f'???? {deleted_count} ???', 'data': {'deleted_count': deleted_count}})


def _save_learning_record(user_id, question, answer, sources, session_id, model_used, kb_used, knowledge_base):
    record = LearningRecord(
        user_id=user_id,
        question=question,
        ai_answer=answer,
        sources=str(sources),
        session_id=session_id,
        category='??',
        knowledge_base=knowledge_base or None
    )
    db.session.add(record)
    db.session.commit()
    api_cache.clear()
    return record


@app.route('/api/ask-with-kb', methods=['POST'])
def ask_question_with_kb():
    data = request.json or {}
    question = (data.get('question') or '').strip()
    user_id = data.get('user_id', 1)
    model_type = 'ollama'
    knowledge_base = (data.get('knowledge_base') or '').strip()
    if not question:
        return jsonify({'code': 400, 'msg': 'knowledge base deleted'}), 400
    session_id = data.get('session_id') or str(uuid.uuid4())
    user = User.get_by_id(user_id)
    sources = []
    kb_used = False
    model_used = 'unknown'
    if knowledge_base:
        kb_result = QAModule(knowledge_base, model_type).query_with_knowledge(question, model_type=model_type, user=user)
        if kb_result['context_used'] and kb_result['sources']:
            answer = kb_result['answer']
            sources = _format_answer_sources(kb_result['sources'], knowledge_base)
            kb_used = True
            model_used = 'knowledge_base'
        else:
            result = ai_service.ask_question(question, user=user, user_id=user_id, model_type=model_type)
            answer = result['answer']
            model_used = result.get('model_used', 'unknown')
    else:
        result = ai_service.ask_question(question, user=user, user_id=user_id, model_type=model_type)
        answer = result['answer']
        model_used = result.get('model_used', 'unknown')
    record = _save_learning_record(user_id, question, answer, sources, session_id, model_used, kb_used, knowledge_base)
    return jsonify({'code': 200, 'data': {
        'answer': answer,
        'sources': sources,
        'record_id': record.id,
        'session_id': session_id,
        'model_used': model_used,
        'knowledge_base_used': kb_used,
        'knowledge_base_name': knowledge_base
    }})


@app.route('/api/ask-with-kb/stream', methods=['POST'])
def ask_question_with_kb_stream():
    data = request.json or {}
    question = (data.get('question') or '').strip()
    user_id = data.get('user_id', 1)
    model_type = 'ollama'
    knowledge_base = (data.get('knowledge_base') or '').strip()
    knowledge_base_enabled = data.get('knowledge_base_enabled', True)
    session_id = data.get('session_id') or str(uuid.uuid4())
    if not question:
        return jsonify({'code': 400, 'msg': 'knowledge base deleted'}), 400

    def send_event(event):
        return json.dumps(event, ensure_ascii=False) + '\n'

    @stream_with_context
    def generate():
        answer_parts = []
        sources = []
        kb_used = False
        model_used = 'unknown'
        user = User.get_by_id(user_id)
        try:
            yield send_event({'type': 'start'})
            if knowledge_base_enabled and knowledge_base:
                yield send_event({'type': 'status', 'content': '正在检索知识库...'})
                chunk_stream, sources, context_used = QAModule(knowledge_base, model_type).stream_with_knowledge(question, model_type=model_type, user=user)
                sources = _format_answer_sources(sources, knowledge_base)
                if context_used and sources:
                    kb_used = True
                    model_used = 'knowledge_base'
                    yield send_event({'type': 'status', 'content': f'已命中 {len(sources)} 个知识片段，正在生成回答...'})
                    for chunk in chunk_stream:
                        answer_parts.append(chunk)
                        yield send_event({'type': 'token', 'content': chunk})
                else:
                    yield send_event({'type': 'status', 'content': '知识库未命中，正在使用本地模型回答...'})
                    chunk_stream, model_used = ai_service.stream_question(question, user=user, user_id=user_id, model_type=model_type)
                    sources = []
                    for chunk in chunk_stream:
                        answer_parts.append(chunk)
                        yield send_event({'type': 'token', 'content': chunk})
            else:
                yield send_event({'type': 'status', 'content': '正在调用本地模型...'})
                chunk_stream, model_used = ai_service.stream_question(question, user=user, user_id=user_id, model_type=model_type)
                for chunk in chunk_stream:
                    answer_parts.append(chunk)
                    yield send_event({'type': 'token', 'content': chunk})
            answer = ''.join(answer_parts)
            record = _save_learning_record(user_id, question, answer, sources, session_id, model_used, kb_used, knowledge_base)
            yield send_event({'type': 'done', 'data': {
                'answer': answer,
                'sources': sources,
                'record_id': record.id,
                'session_id': session_id,
                'model_used': model_used,
                'knowledge_base_used': kb_used,
                'knowledge_base_name': knowledge_base
            }})
        except Exception as exc:
            yield send_event({'type': 'error', 'msg': f'?????: {str(exc)}'})

    response = Response(generate(), mimetype='application/x-ndjson; charset=utf-8')
    response.headers['Cache-Control'] = 'no-cache, no-transform'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
