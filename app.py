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
from app.models import db, User, LearningRecord, ClassRoom, ClassMember, ClassJoinRequest, ClassMemberLog, ClassKnowledgeBase, UserKnowledgeBase, KnowledgeDocument
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


def _can_manage_class_room(class_room):
    if not class_room:
        return False
    if request.current_user.has_role('admin'):
        return True
    return _current_user_role() == 'teacher' and class_room.teacher_id == request.current_user.id


def _is_student_in_class(class_id):
    if not class_id:
        return False
    if request.current_user.has_role('admin'):
        return True
    return ClassMember.query.filter_by(class_id=class_id, student_id=request.current_user.id).first() is not None


def _write_class_member_log(class_id, student_id, action, source='manual', details=None, operator_id=None):
    log = ClassMemberLog(
        class_id=class_id,
        student_id=student_id,
        action=action,
        operator_id=operator_id if operator_id is not None else getattr(request.current_user, 'id', None),
        source=source,
        details=details
    )
    db.session.add(log)
    return log


def _class_knowledge_status(class_room):
    mapping = ClassKnowledgeBase.query.filter_by(class_id=class_room.id).first()
    if not mapping:
        legacy_mapping = ClassKnowledgeBase.query.filter_by(class_name=class_room.name, teacher_id=class_room.teacher_id).first()
        if legacy_mapping:
            mapping = legacy_mapping
            if not mapping.class_id:
                mapping.class_id = class_room.id
                db.session.flush()
    if not mapping:
        return {
            'has_knowledge_base': False,
            'status': 'not_published',
            'status_text': '未发布',
            'knowledge_base': None,
            'display_name': None,
            'document_count': 0,
            'ready_count': 0,
            'failed_count': 0,
            'parsing_count': 0,
            'vector_count': 0
        }

    documents = KnowledgeDocument.query.filter_by(knowledge_base=mapping.knowledge_base).all()
    document_count = len(documents)
    ready_count = sum(1 for item in documents if item.status == 'ready')
    failed_count = sum(1 for item in documents if item.status == 'failed')
    parsing_count = sum(1 for item in documents if item.status in {'pending', 'parsing'})
    vector_count = sum((item.vector_count or 0) for item in documents)

    if document_count == 0:
        status, text = 'empty', '已发布未上传'
    elif parsing_count:
        status, text = 'parsing', '解析中'
    elif ready_count and failed_count:
        status, text = 'partial_failed', '部分失败'
    elif failed_count and not ready_count:
        status, text = 'failed', '解析失败'
    elif ready_count and vector_count > 0:
        status, text = 'ready', '可用'
    else:
        status, text = 'empty', '无可用向量'

    return {
        'has_knowledge_base': True,
        'status': status,
        'status_text': text,
        'knowledge_base': mapping.knowledge_base,
        'display_name': mapping.class_name,
        'teacher_id': mapping.teacher_id,
        'teacher_name': mapping.teacher.username if mapping.teacher else None,
        'document_count': document_count,
        'ready_count': ready_count,
        'failed_count': failed_count,
        'parsing_count': parsing_count,
        'vector_count': vector_count,
        'created_at': mapping.created_at.strftime('%Y-%m-%d %H:%M') if mapping.created_at else None
    }


def _class_detail_payload(class_room):
    members = ClassMember.query.filter_by(class_id=class_room.id).order_by(ClassMember.joined_at.desc()).all()
    pending_count = ClassJoinRequest.query.filter_by(class_id=class_room.id, status='pending').count()
    payload = class_room.to_dict()
    payload.update({
        'member_count': len(members),
        'pending_request_count': pending_count,
        'knowledge_status': _class_knowledge_status(class_room)
    })
    return payload


def _ensure_class_room_for_legacy_mapping(mapping):
    if not mapping or mapping.class_id:
        return mapping.class_room if mapping else None
    class_room = ClassRoom.query.filter_by(name=mapping.class_name, teacher_id=mapping.teacher_id).first()
    if not class_room:
        class_room = ClassRoom(name=mapping.class_name, teacher_id=mapping.teacher_id, description=mapping.description)
        db.session.add(class_room)
        db.session.flush()
    mapping.class_id = class_room.id
    db.session.commit()
    return class_room


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


def _can_view_knowledge_base(knowledge_base):
    if not knowledge_base or knowledge_base == 'default':
        return False
    if _can_manage_knowledge_base(knowledge_base):
        return True
    mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if mapping:
        _ensure_class_room_for_legacy_mapping(mapping)
        return _is_student_in_class(mapping.class_id)
    owner = UserKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if owner:
        return owner.owner_id == request.current_user.id
    return False


def _knowledge_base_item(knowledge_base, class_map=None, owner_map=None):
    class_map = class_map or {}
    owner_map = owner_map or {}
    class_mapping = class_map.get(knowledge_base)
    if class_mapping:
        class_room = _ensure_class_room_for_legacy_mapping(class_mapping)
        can_manage = _can_manage_knowledge_base(knowledge_base)
        owner_name = class_mapping.teacher.username if class_mapping.teacher else None
        return {
            'name': knowledge_base,
            'display_name': class_mapping.class_name,
            'type': 'class',
            'class_id': class_mapping.class_id,
            'class_room_name': class_room.name if class_room else class_mapping.class_name,
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
        if not inspector.has_table('class_rooms'):
            ClassRoom.__table__.create(db.engine)
        if not inspector.has_table('class_members'):
            ClassMember.__table__.create(db.engine)
        if not inspector.has_table('class_join_requests'):
            ClassJoinRequest.__table__.create(db.engine)
        if not inspector.has_table('class_member_logs'):
            ClassMemberLog.__table__.create(db.engine)
        if not inspector.has_table('class_knowledge_bases'):
            ClassKnowledgeBase.__table__.create(db.engine)
        if not inspector.has_table('user_knowledge_bases'):
            UserKnowledgeBase.__table__.create(db.engine)
        if not inspector.has_table('knowledge_documents'):
            KnowledgeDocument.__table__.create(db.engine)
        user_columns = {column['name'] for column in inspector.get_columns('users')}
        class_kb_columns = {column['name'] for column in inspector.get_columns('class_knowledge_bases')} if inspector.has_table('class_knowledge_bases') else set()
        with db.engine.begin() as conn:
            if 'role' not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'student'"))
            conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = TRUE AND (role IS NULL OR role <> 'admin')"))
            if 'class_id' not in class_kb_columns:
                conn.execute(text("ALTER TABLE class_knowledge_bases ADD COLUMN class_id INT NULL"))
                conn.execute(text("CREATE INDEX idx_class_knowledge_bases_class_id ON class_knowledge_bases (class_id)"))
        for mapping in ClassKnowledgeBase.query.filter(ClassKnowledgeBase.class_id.is_(None)).all():
            _ensure_class_room_for_legacy_mapping(mapping)
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
    if not request.current_user.has_role('teacher', 'admin'):
        return jsonify({'code': 403, 'msg': 'Permission denied'}), 403
    return render_template('admin.html')


@app.route('/knowledge-base')
@auth_required
def knowledge_base_page():
    return render_template('knowledge-base.html')


@app.route('/my-classes')
@auth_required
def my_classes_page():
    return render_template('my-classes.html')


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
    KnowledgeDocument.query.filter_by(uploader_id=user_id).update({'uploader_id': request.current_user.id})
    ClassJoinRequest.query.filter_by(student_id=user_id).delete()
    ClassJoinRequest.query.filter_by(reviewer_id=user_id).update({'reviewer_id': request.current_user.id})
    ClassMemberLog.query.filter_by(student_id=user_id).delete()
    ClassMemberLog.query.filter_by(operator_id=user_id).update({'operator_id': request.current_user.id})
    ClassKnowledgeBase.query.filter_by(teacher_id=user_id).delete()
    ClassMember.query.filter_by(student_id=user_id).delete()
    teacher_class_ids = [item.id for item in ClassRoom.query.filter_by(teacher_id=user_id).all()]
    if teacher_class_ids:
        ClassJoinRequest.query.filter(ClassJoinRequest.class_id.in_(teacher_class_ids)).delete(synchronize_session=False)
        ClassMemberLog.query.filter(ClassMemberLog.class_id.in_(teacher_class_ids)).delete(synchronize_session=False)
        ClassMember.query.filter(ClassMember.class_id.in_(teacher_class_ids)).delete(synchronize_session=False)
        ClassRoom.query.filter(ClassRoom.id.in_(teacher_class_ids)).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'deleted'})


@app.route('/api/classes', methods=['GET'])
@token_required
def list_classes():
    role = _current_user_role()
    if request.current_user.has_role('admin'):
        classes = ClassRoom.query.order_by(ClassRoom.created_at.desc()).all()
    elif role == 'teacher':
        classes = ClassRoom.query.filter_by(teacher_id=request.current_user.id).order_by(ClassRoom.created_at.desc()).all()
    else:
        memberships = ClassMember.query.filter_by(student_id=request.current_user.id).all()
        class_ids = [item.class_id for item in memberships]
        classes = ClassRoom.query.filter(ClassRoom.id.in_(class_ids)).order_by(ClassRoom.created_at.desc()).all() if class_ids else []
    return jsonify({'code': 200, 'data': [_class_detail_payload(item) for item in classes]})


@app.route('/api/classes/available', methods=['GET'])
@role_required('student')
def list_available_classes():
    joined_ids = {
        item.class_id for item in ClassMember.query.filter_by(student_id=request.current_user.id).all()
    }
    pending_requests = {
        item.class_id: item
        for item in ClassJoinRequest.query.filter_by(student_id=request.current_user.id, status='pending').all()
    }
    classes = ClassRoom.query.order_by(ClassRoom.created_at.desc()).all()
    data = []
    for class_room in classes:
        item = _class_detail_payload(class_room)
        item['joined'] = class_room.id in joined_ids
        item['pending_request_id'] = pending_requests[class_room.id].id if class_room.id in pending_requests else None
        data.append(item)
    return jsonify({'code': 200, 'data': data})


@app.route('/api/classes', methods=['POST'])
@role_required('teacher', 'admin')
def create_class_room():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip() or None
    teacher_id = data.get('teacher_id') if request.current_user.has_role('admin') else request.current_user.id
    if not name:
        return jsonify({'code': 400, 'msg': 'class name is required'}), 400
    teacher = User.query.get(teacher_id)
    if not teacher or not teacher.has_role('teacher', 'admin'):
        return jsonify({'code': 400, 'msg': 'teacher is invalid'}), 400
    class_room = ClassRoom(name=name, description=description, teacher_id=teacher.id)
    db.session.add(class_room)
    db.session.commit()
    return jsonify({'code': 200, 'data': class_room.to_dict()})


@app.route('/api/classes/<int:class_id>', methods=['GET'])
@token_required
def get_class_room(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    role = _current_user_role()
    if role in {'teacher', 'admin'}:
        if not _can_manage_class_room(class_room):
            return jsonify({'code': 403, 'msg': 'Cannot view this class'}), 403
    elif not _is_student_in_class(class_id):
        return jsonify({'code': 403, 'msg': 'Cannot view this class'}), 403
    return jsonify({'code': 200, 'data': _class_detail_payload(class_room)})


@app.route('/api/classes/<int:class_id>', methods=['DELETE'])
@role_required('teacher', 'admin')
def delete_class_room(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    for mapping in ClassKnowledgeBase.query.filter_by(class_id=class_id).all():
        QAModule(mapping.knowledge_base).delete_knowledge_base()
        KnowledgeDocument.query.filter_by(knowledge_base=mapping.knowledge_base).delete()
        db.session.delete(mapping)
    ClassJoinRequest.query.filter_by(class_id=class_id).delete()
    ClassMemberLog.query.filter_by(class_id=class_id).delete()
    ClassMember.query.filter_by(class_id=class_id).delete()
    db.session.delete(class_room)
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'class deleted'})


@app.route('/api/classes/<int:class_id>/members', methods=['GET'])
@role_required('teacher', 'admin')
def list_class_members(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    members = ClassMember.query.filter_by(class_id=class_id).all()
    return jsonify({'code': 200, 'data': [item.to_dict() for item in members]})


@app.route('/api/classes/<int:class_id>/available-students', methods=['GET'])
@role_required('teacher', 'admin')
def list_available_class_students(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    member_ids = {
        item.student_id for item in ClassMember.query.filter_by(class_id=class_id).all()
    }
    students = User.query.filter_by(role='student').order_by(User.username.asc()).all()
    return jsonify({'code': 200, 'data': [student.to_dict() for student in students if student.id not in member_ids]})


@app.route('/api/classes/<int:class_id>/members', methods=['POST'])
@role_required('teacher', 'admin')
def add_class_member(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    data = request.json or {}
    student_id = data.get('student_id')
    student = User.query.get(student_id)
    if not student or (student.role or 'student') != 'student':
        return jsonify({'code': 400, 'msg': 'student is invalid'}), 400
    membership = ClassMember.query.filter_by(class_id=class_id, student_id=student.id).first()
    if not membership:
        membership = ClassMember(class_id=class_id, student_id=student.id)
        db.session.add(membership)
        _write_class_member_log(class_id, student.id, 'joined', source='manual', details='teacher/admin added student')
        db.session.commit()
    return jsonify({'code': 200, 'data': membership.to_dict()})


@app.route('/api/classes/<int:class_id>/members/batch', methods=['POST'])
@role_required('teacher', 'admin')
def add_class_members_batch(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    data = request.json or {}
    raw_ids = data.get('student_ids') or []
    if not isinstance(raw_ids, list):
        return jsonify({'code': 400, 'msg': 'student_ids must be a list'}), 400
    student_ids = []
    for value in raw_ids:
        try:
            student_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    existing_ids = {
        item.student_id for item in ClassMember.query.filter_by(class_id=class_id).all()
    }
    added = []
    skipped = []
    for student in User.query.filter(User.id.in_(student_ids)).all() if student_ids else []:
        if (student.role or 'student') != 'student':
            skipped.append({'id': student.id, 'reason': 'not_student'})
            continue
        if student.id in existing_ids:
            skipped.append({'id': student.id, 'reason': 'already_member'})
            continue
        membership = ClassMember(class_id=class_id, student_id=student.id)
        db.session.add(membership)
        _write_class_member_log(class_id, student.id, 'joined', source='batch', details='batch added student')
        existing_ids.add(student.id)
        added.append(student.id)
    db.session.commit()
    return jsonify({'code': 200, 'data': {'added_count': len(added), 'added_ids': added, 'skipped': skipped}})


@app.route('/api/classes/<int:class_id>/members/<int:student_id>', methods=['DELETE'])
@role_required('teacher', 'admin')
def remove_class_member(class_id, student_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    ClassMember.query.filter_by(class_id=class_id, student_id=student_id).delete()
    _write_class_member_log(class_id, student_id, 'removed', source='manual', details='teacher/admin removed student')
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'member removed'})


@app.route('/api/classes/<int:class_id>/join-requests', methods=['GET'])
@role_required('teacher', 'admin')
def list_class_join_requests(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    status = (request.args.get('status') or '').strip()
    query = ClassJoinRequest.query.filter_by(class_id=class_id)
    if status:
        query = query.filter_by(status=status)
    requests_list = query.order_by(ClassJoinRequest.created_at.desc()).all()
    return jsonify({'code': 200, 'data': [item.to_dict() for item in requests_list]})


@app.route('/api/classes/<int:class_id>/join-requests', methods=['POST'])
@role_required('student')
def create_class_join_request(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if ClassMember.query.filter_by(class_id=class_id, student_id=request.current_user.id).first():
        return jsonify({'code': 400, 'msg': 'already joined this class'}), 400
    pending = ClassJoinRequest.query.filter_by(class_id=class_id, student_id=request.current_user.id, status='pending').first()
    if pending:
        return jsonify({'code': 400, 'msg': 'request already pending', 'data': pending.to_dict()}), 400
    data = request.json or {}
    join_request = ClassJoinRequest(
        class_id=class_id,
        student_id=request.current_user.id,
        reason=(data.get('reason') or '').strip() or None
    )
    db.session.add(join_request)
    db.session.commit()
    return jsonify({'code': 200, 'data': join_request.to_dict()})


@app.route('/api/my/class-requests', methods=['GET'])
@role_required('student')
def list_my_class_requests():
    requests_list = ClassJoinRequest.query.filter_by(student_id=request.current_user.id).order_by(ClassJoinRequest.created_at.desc()).all()
    return jsonify({'code': 200, 'data': [item.to_dict() for item in requests_list]})


@app.route('/api/class-requests/<int:request_id>/cancel', methods=['POST'])
@role_required('student')
def cancel_class_join_request(request_id):
    join_request = ClassJoinRequest.query.get(request_id)
    if not join_request or join_request.student_id != request.current_user.id:
        return jsonify({'code': 404, 'msg': 'request not found'}), 404
    if join_request.status != 'pending':
        return jsonify({'code': 400, 'msg': 'only pending request can be cancelled'}), 400
    join_request.status = 'cancelled'
    join_request.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'code': 200, 'data': join_request.to_dict()})


@app.route('/api/class-requests/<int:request_id>/review', methods=['POST'])
@role_required('teacher', 'admin')
def review_class_join_request(request_id):
    join_request = ClassJoinRequest.query.get(request_id)
    if not join_request:
        return jsonify({'code': 404, 'msg': 'request not found'}), 404
    class_room = join_request.class_room
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot review this request'}), 403
    if join_request.status != 'pending':
        return jsonify({'code': 400, 'msg': 'only pending request can be reviewed'}), 400
    data = request.json or {}
    decision = (data.get('status') or '').strip()
    if decision not in {'approved', 'rejected'}:
        return jsonify({'code': 400, 'msg': 'status must be approved or rejected'}), 400
    join_request.status = decision
    join_request.review_message = (data.get('review_message') or '').strip() or None
    join_request.reviewer_id = request.current_user.id
    join_request.reviewed_at = datetime.utcnow()
    if decision == 'approved':
        membership = ClassMember.query.filter_by(class_id=join_request.class_id, student_id=join_request.student_id).first()
        if not membership:
            db.session.add(ClassMember(class_id=join_request.class_id, student_id=join_request.student_id))
            _write_class_member_log(join_request.class_id, join_request.student_id, 'approved', source='request', details='join request approved')
    db.session.commit()
    return jsonify({'code': 200, 'data': join_request.to_dict()})


@app.route('/api/my/classes/<int:class_id>', methods=['DELETE'])
@role_required('student')
def leave_my_class(class_id):
    membership = ClassMember.query.filter_by(class_id=class_id, student_id=request.current_user.id).first()
    if not membership:
        return jsonify({'code': 404, 'msg': 'membership not found'}), 404
    db.session.delete(membership)
    _write_class_member_log(class_id, request.current_user.id, 'left', source='self_leave', details='student left class')
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'left class'})


@app.route('/api/classes/<int:class_id>/knowledge-status', methods=['GET'])
@token_required
def get_class_knowledge_status(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    role = _current_user_role()
    if role in {'teacher', 'admin'}:
        if not _can_manage_class_room(class_room):
            return jsonify({'code': 403, 'msg': 'Cannot view this class'}), 403
    elif not _is_student_in_class(class_id):
        return jsonify({'code': 403, 'msg': 'Cannot view this class'}), 403
    return jsonify({'code': 200, 'data': _class_knowledge_status(class_room)})


@app.route('/api/classes/<int:class_id>/member-logs', methods=['GET'])
@role_required('teacher', 'admin')
def list_class_member_logs(class_id):
    class_room = ClassRoom.query.get(class_id)
    if not class_room:
        return jsonify({'code': 404, 'msg': 'class not found'}), 404
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot manage this class'}), 403
    logs = ClassMemberLog.query.filter_by(class_id=class_id).order_by(ClassMemberLog.created_at.desc()).limit(100).all()
    return jsonify({'code': 200, 'data': [item.to_dict() for item in logs]})


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
        class_room = ClassRoom.query.filter_by(name=class_name, teacher_id=request.current_user.id).first()
        if not class_room:
            class_room = ClassRoom(name=class_name, teacher_id=request.current_user.id, description=description)
            db.session.add(class_room)
            db.session.flush()
        knowledge_base = _class_knowledge_base_name(class_name, request.current_user.id)
        mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
        if not mapping:
            mapping = ClassKnowledgeBase(
                class_id=class_room.id,
                class_name=class_name,
                knowledge_base=knowledge_base,
                teacher_id=request.current_user.id,
                description=description
            )
            db.session.add(mapping)
            db.session.commit()
        elif not mapping.class_id:
            mapping.class_id = class_room.id
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
    document = KnowledgeDocument(
        knowledge_base=knowledge_base,
        filename=file.filename,
        stored_filename=saved_filename,
        file_path=filename,
        file_type=file_ext.lstrip('.'),
        file_size=os.path.getsize(filename),
        uploader_id=request.current_user.id,
        uploader_role=current_role,
        status='parsing'
    )
    db.session.add(document)
    db.session.commit()
    kb_instance = QAModule(knowledge_base)
    result = kb_instance.add_document(filename, metadata={
        'document_id': document.id,
        'file_name': file.filename,
        'uploaded_by': request.current_user.id,
        'uploader_role': current_role,
        'class_name': class_name,
        'knowledge_base': knowledge_base
    })
    success = result.get('success') if isinstance(result, dict) else bool(result)
    if not success:
        document.status = 'failed'
        document.parse_error = (result.get('error') if isinstance(result, dict) else None) or getattr(kb_instance, 'last_error', '') or 'unknown error'
        document.vector_count = 0
        db.session.commit()
        detail = getattr(kb_instance, 'last_error', '') or '未知错误'
        if 'No module named' in detail and 'docx' in detail:
            detail = '服务器缺少 python-docx 依赖，无法解析 Word 文档。请执行 pip install python-docx 后重启服务。'
        return jsonify({'code': 500, 'msg': f'添加文件到知识库失败：{detail}'}), 500
    document.status = 'ready'
    document.parse_error = None
    document.vector_count = result.get('vector_count', 0) if isinstance(result, dict) else 0
    document.parsed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'file added to knowledge base', 'data': document.to_dict(can_manage=True)})


@app.route('/api/knowledge-base/stats', methods=['GET'])
@token_required
def get_knowledge_base_stats():
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    if not _can_view_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot view this knowledge base'}), 403
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
    KnowledgeDocument.query.filter_by(knowledge_base=knowledge_base).delete()
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
    KnowledgeDocument.query.filter_by(knowledge_base=old_name).update({'knowledge_base': new_name})
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
    class_id = data.get('class_id')
    class_name = (data.get('class_name') or '').strip()
    description = (data.get('description') or '').strip() or None
    class_room = ClassRoom.query.get(class_id) if class_id else None
    if not class_room and class_name:
        class_room = ClassRoom(name=class_name, teacher_id=request.current_user.id, description=description)
        db.session.add(class_room)
        db.session.flush()
    if not class_room:
        return jsonify({'code': 400, 'msg': 'class is required'}), 400
    if not _can_manage_class_room(class_room):
        return jsonify({'code': 403, 'msg': 'Cannot publish to this class'}), 403
    class_name = class_room.name
    knowledge_base = _class_knowledge_base_name(class_name, class_room.teacher_id)
    mapping = ClassKnowledgeBase.query.filter_by(knowledge_base=knowledge_base).first()
    if not mapping:
        mapping = ClassKnowledgeBase(
            class_id=class_room.id,
            class_name=class_name,
            knowledge_base=knowledge_base,
            teacher_id=class_room.teacher_id,
            description=description or class_room.description
        )
        db.session.add(mapping)
        db.session.commit()
    elif not mapping.class_id:
        mapping.class_id = class_room.id
        db.session.commit()
    QAModule(knowledge_base)
    return jsonify({'code': 200, 'msg': 'class knowledge base created', 'data': mapping.to_dict()})


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
    all_class_kbs = ClassKnowledgeBase.query.order_by(ClassKnowledgeBase.created_at.desc()).all()
    for mapping in all_class_kbs:
        _ensure_class_room_for_legacy_mapping(mapping)
    role = _current_user_role()
    if request.current_user.has_role('admin'):
        class_kbs = all_class_kbs
    elif role == 'teacher':
        class_kbs = [item for item in all_class_kbs if item.teacher_id == request.current_user.id]
    else:
        member_class_ids = {
            item.class_id
            for item in ClassMember.query.filter_by(student_id=request.current_user.id).all()
        }
        class_kbs = [item for item in all_class_kbs if item.class_id in member_class_ids]
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
    if not _can_view_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot view this knowledge base'}), 403
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
    if not _can_view_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot view this knowledge base'}), 403
    return jsonify({'code': 200, 'data': QAModule(knowledge_base).get_file_sources()})


@app.route('/api/knowledge-base/documents', methods=['GET'])
@token_required
def get_knowledge_base_documents():
    knowledge_base = (request.args.get('knowledge_base') or '').strip()
    if not knowledge_base:
        return jsonify({'code': 400, 'msg': 'knowledge base name is required'}), 400
    if not _can_view_knowledge_base(knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot view this knowledge base'}), 403
    can_manage = _can_manage_knowledge_base(knowledge_base)
    documents = KnowledgeDocument.query.filter_by(knowledge_base=knowledge_base).order_by(KnowledgeDocument.created_at.desc()).all()
    return jsonify({'code': 200, 'data': [item.to_dict(can_manage=can_manage) for item in documents]})


@app.route('/api/knowledge-base/documents/<int:document_id>', methods=['DELETE'])
@token_required
def delete_knowledge_document(document_id):
    document = KnowledgeDocument.query.get(document_id)
    if not document:
        return jsonify({'code': 404, 'msg': 'document not found'}), 404
    if not _can_manage_knowledge_base(document.knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot delete this document'}), 403
    deleted_count = QAModule(document.knowledge_base).delete_by_document_id(document.id)
    if os.path.exists(document.file_path):
        try:
            os.remove(document.file_path)
        except OSError as exc:
            return jsonify({'code': 500, 'msg': f'failed to delete source file: {exc}'}), 500
    db.session.delete(document)
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'document deleted', 'data': {'deleted_count': deleted_count}})


@app.route('/api/knowledge-base/documents/<int:document_id>/reparse', methods=['POST'])
@token_required
def reparse_knowledge_document(document_id):
    document = KnowledgeDocument.query.get(document_id)
    if not document:
        return jsonify({'code': 404, 'msg': 'document not found'}), 404
    if not _can_manage_knowledge_base(document.knowledge_base):
        return jsonify({'code': 403, 'msg': 'Cannot reparse this document'}), 403
    if not os.path.exists(document.file_path):
        document.status = 'failed'
        document.parse_error = 'source file not found'
        document.vector_count = 0
        db.session.commit()
        return jsonify({'code': 404, 'msg': 'source file not found'}), 404

    document.status = 'parsing'
    document.parse_error = None
    db.session.commit()
    kb_instance = QAModule(document.knowledge_base)
    result = kb_instance.add_document(document.file_path, metadata={
        'document_id': document.id,
        'file_name': document.filename,
        'uploaded_by': document.uploader_id,
        'uploader_role': document.uploader_role,
        'knowledge_base': document.knowledge_base
    })
    success = result.get('success') if isinstance(result, dict) else bool(result)
    if not success:
        document.status = 'failed'
        document.parse_error = (result.get('error') if isinstance(result, dict) else None) or getattr(kb_instance, 'last_error', '') or 'unknown error'
        document.vector_count = 0
        db.session.commit()
        return jsonify({'code': 500, 'msg': document.parse_error, 'data': document.to_dict(can_manage=True)}), 500
    document.status = 'ready'
    document.parse_error = None
    document.vector_count = result.get('vector_count', 0) if isinstance(result, dict) else 0
    document.parsed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'code': 200, 'msg': 'document reparsed', 'data': document.to_dict(can_manage=True)})


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
    KnowledgeDocument.query.filter_by(knowledge_base=knowledge_base, file_path=file_path).delete()
    KnowledgeDocument.query.filter_by(knowledge_base=knowledge_base, stored_filename=os.path.basename(file_path)).delete()
    db.session.commit()
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
@token_required
def ask_question_with_kb():
    data = request.json or {}
    question = (data.get('question') or '').strip()
    user_id = request.current_user.id
    model_type = 'ollama'
    knowledge_base = (data.get('knowledge_base') or '').strip()
    if not question:
        return jsonify({'code': 400, 'msg': 'knowledge base deleted'}), 400
    session_id = data.get('session_id') or str(uuid.uuid4())
    user = request.current_user
    sources = []
    kb_used = False
    model_used = 'unknown'
    if knowledge_base:
        if not _can_view_knowledge_base(knowledge_base):
            return jsonify({'code': 403, 'msg': 'Cannot view this knowledge base'}), 403
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
@token_required
def ask_question_with_kb_stream():
    data = request.json or {}
    question = (data.get('question') or '').strip()
    user_id = request.current_user.id
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
        user = request.current_user
        try:
            yield send_event({'type': 'start'})
            if knowledge_base_enabled and knowledge_base:
                if not _can_view_knowledge_base(knowledge_base):
                    yield send_event({'type': 'error', 'msg': 'Cannot view this knowledge base'})
                    return
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
