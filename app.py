"""
智学伴 - AI个性化学习伴侣系统
Flask应用主文件
"""

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
import os
import uuid
import requests

from config import Config
from app.models import db, User, LearningRecord
from app.services.ai_service import AIService
from app.services.auth_service import AuthService

# 内存缓存 - 使用字典存储，在生产环境中应该使用Redis等持久化缓存
from collections import defaultdict
api_cache = defaultdict(dict)

# 知识库模块
from app.services.knowledge_service import QAModule

# 知识库QA模块（稍后初始化）
knowledge_qa = None

def create_app():
    """创建Flask应用"""
    # 获取应用根目录
    app_root = os.path.dirname(os.path.abspath(__file__))
    # 明确指定模板目录和静态文件目录
    app = Flask(__name__, 
                template_folder=os.path.join(app_root, 'templates'),
                static_folder=os.path.join(app_root, 'static'))
    app.config.from_object(Config)
    
    # 初始化扩展
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)

    # 注册蓝图
    from app.routes.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    # 确保上传目录存在
    os.makedirs(os.path.join(app_root, app.config['AVATAR_UPLOAD_FOLDER']), exist_ok=True)
    os.makedirs(os.path.join(app_root, app.config['DOCUMENT_UPLOAD_FOLDER']), exist_ok=True)
    
    # 413 错误处理器（文件过大）
    @app.errorhandler(413)
    def request_entity_too_large(error):
        max_size_mb = app.config.get('MAX_CONTENT_LENGTH', 100 * 1024 * 1024) // (1024 * 1024)
        return jsonify({
            'code': 413,
            'msg': f'文件太大！最大支持 {max_size_mb}MB，请压缩文件或拆分后再上传'
        }), 413
    
    return app

# 创建应用实例
app = create_app()

# 初始化服务
ai_service = AIService()

# 在应用上下文中初始化服务
with app.app_context():
    ai_service.init_config()

# 初始化知识库QA模块（在Config加载之后）
knowledge_qa = QAModule()



# ==================== 路由定义 ====================

from app.services.auth_service import auth_required, token_required

@app.route('/')
@auth_required
def index():
    """首页"""
    return render_template('index.html')

@app.route('/records')
@auth_required
def records():
    """学习记录页面"""
    return render_template('index.html')

@app.route('/profile')
@auth_required
def profile():
    """个人中心页面"""
    return render_template('profile.html')

@app.route('/knowledge-base')
@auth_required
def knowledge_base():
    """知识库管理页面"""
    return render_template('index.html')

@app.route('/login')
def login():
    """登录页面"""
    return render_template('login.html')

@app.route('/register')
def register_page():
    """注册页面"""
    return render_template('register.html')

@app.route('/forgot-password')
def forgot_password():
    """忘记密码页面"""
    return render_template('forgot-password.html')

# ==================== API接口 ====================

@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    """
    提交反馈
    POST /api/feedback
    {
        "record_id": 1,
        "is_helpful": 1
    }
    """
    data = request.json
    record_id = data.get('record_id')
    is_helpful = data.get('is_helpful')
    
    record = LearningRecord.query.get(record_id)
    if not record:
        return jsonify({'code': 404, 'msg': '记录不存在'}), 404
    
    record.is_helpful = is_helpful
    db.session.commit()
    
    # 清除相关缓存
    if f'sessions_{record.user_id}' in api_cache:
        del api_cache[f'sessions_{record.user_id}']
    if f'records_{record.user_id}_1' in api_cache:
        del api_cache[f'records_{record.user_id}_1']
    
    return jsonify({'code': 200, 'msg': '反馈成功'})


@app.route('/api/records/sessions', methods=['GET'])
def get_sessions():
    """
    获取历史会话列表（按会话分组）
    GET /api/records/sessions?user_id=1
    """
    user_id = request.args.get('user_id', 1, type=int)
    cache_key = f'sessions_{user_id}'
    
    # 检查缓存
    import time
    if cache_key in api_cache:
        cached_data = api_cache[cache_key]
        if time.time() - cached_data['timestamp'] < 600:  # 10分钟缓存
            return jsonify({
                'code': 200,
                'data': cached_data['data']
            })
    
    # 优化：只获取最近100条记录进行处理，提高性能
    records = LearningRecord.query.filter_by(user_id=user_id)\
        .order_by(LearningRecord.created_at.desc())\
        .limit(100).all()
    
    sessions = {}
    for r in records:
        sid = r.session_id or f'legacy_{r.id}'
        if sid not in sessions:
            sessions[sid] = {
                'session_id': sid,
                'first_question': r.question[:50] + '...' if len(r.question) > 50 else r.question,
                'count': 0,
                'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
                'time_ago': r._get_time_ago()
            }
        sessions[sid]['count'] += 1
    
    # 只返回最近的20个会话
    session_list = list(sessions.values())[:20]
    
    # 缓存结果
    api_cache[cache_key] = {
        'data': session_list,
        'timestamp': time.time()
    }
    
    return jsonify({
        'code': 200,
        'data': session_list
    })


@app.route('/api/records/session/<session_id>', methods=['GET'])
def get_session_detail(session_id):
    """
    获取某个会话的详细记录
    GET /api/records/session/{session_id}?user_id=1
    """
    user_id = request.args.get('user_id', 1, type=int)
    cache_key = f'session_{session_id}_{user_id}'
    
    # 检查缓存
    import time
    if cache_key in api_cache:
        cached_data = api_cache[cache_key]
        if time.time() - cached_data['timestamp'] < 600:  # 10分钟缓存
            return jsonify({
                'code': 200,
                'data': cached_data['data']
            })
    
    if session_id.startswith('legacy_'):
        record_id = int(session_id.replace('legacy_', ''))
        records = LearningRecord.query.filter_by(id=record_id, user_id=user_id).all()
    else:
        records = LearningRecord.query.filter_by(session_id=session_id, user_id=user_id)\
            .order_by(LearningRecord.created_at.asc()).all()
    
    result_data = [r.to_dict() for r in records]
    
    # 缓存结果
    api_cache[cache_key] = {
        'data': result_data,
        'timestamp': time.time()
    }
    
    return jsonify({
        'code': 200,
        'data': result_data
    })


@app.route('/api/records/session/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """
    删除会话
    DELETE /api/records/session/{session_id}
    """
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'code': 400, 'msg': '缺少用户ID'}), 400
        
        # 删除该会话的所有记录
        LearningRecord.query.filter_by(
            user_id=user_id, session_id=session_id
        ).delete()
        db.session.commit()
        
        # 清除相关缓存
        if f'sessions_{user_id}' in api_cache:
            del api_cache[f'sessions_{user_id}']
        if f'session_{session_id}_{user_id}' in api_cache:
            del api_cache[f'session_{session_id}_{user_id}']
        
        return jsonify({'code': 200, 'msg': '删除成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': f'删除会话失败: {str(e)}'}), 500


@app.route('/api/records', methods=['GET'])
def get_records():
    """
    获取学习记录
    GET /api/records?user_id=1&page=1
    """
    user_id = request.args.get('user_id', 1, type=int)
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # 检查缓存
    cache_key = f'records_{user_id}_{page}'
    import time
    if cache_key in api_cache:
        cached_data = api_cache[cache_key]
        if time.time() - cached_data['timestamp'] < 600:  # 10分钟缓存
            return jsonify({'code': 200, 'data': cached_data['data']})
    
    pagination = LearningRecord.query.filter_by(user_id=user_id)\
        .order_by(LearningRecord.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    result_data = {
        'items': [r.to_dict() for r in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    }
    
    # 缓存结果
    api_cache[cache_key] = {
        'data': result_data,
        'timestamp': time.time()
    }
    
    return jsonify({
        'code': 200,
        'data': result_data
    })


@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    """
    删除学习记录
    DELETE /api/records/{record_id}
    """
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'code': 400, 'msg': '缺少用户ID'}), 400
        
        # 查找记录
        record = LearningRecord.query.filter_by(id=record_id, user_id=user_id).first()
        if not record:
            # 清除相关缓存，即使记录不存在
            if f'sessions_{user_id}' in api_cache:
                del api_cache[f'sessions_{user_id}']
            if f'records_{user_id}_1' in api_cache:
                del api_cache[f'records_{user_id}_1']
            # 清除所有可能的会话详情缓存
            for cache_key in list(api_cache.keys()):
                if cache_key.startswith(f'session_') and f'_{user_id}' in cache_key:
                    del api_cache[cache_key]
            return jsonify({'code': 404, 'msg': '记录不存在'}), 404
        
        # 删除记录
        db.session.delete(record)
        db.session.commit()
        
        # 清除相关缓存
        if f'sessions_{user_id}' in api_cache:
            del api_cache[f'sessions_{user_id}']
        if f'records_{user_id}_1' in api_cache:
            del api_cache[f'records_{user_id}_1']
        
        return jsonify({'code': 200, 'msg': '删除成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': f'删除记录失败: {str(e)}'}), 500


@app.route('/api/ollama/status', methods=['GET'])
def ollama_status():
    """检查 Ollama 状态和可用模型"""
    from config import Config
    
    # 检查缓存
    cache_key = 'ollama_status'
    import time
    if cache_key in api_cache:
        cached_data = api_cache[cache_key]
        if time.time() - cached_data['timestamp'] < 600:  # 10分钟缓存
            return jsonify({'code': 200, 'data': cached_data['data']})
    
    base_url = Config.OLLAMA_BASE_URL
    enabled = Config.USE_OLLAMA
    # 使用生成模型的名称，而不是嵌入模型的名称
    model = "qwen2.5:7b"
    
    status = {
        'enabled': enabled,
        'base_url': base_url,
        'configured_model': model,
        'connected': False,
        'available_models': []
    }
    
    # 只有在启用Ollama时才尝试连接
    if enabled:
        try:
            # 超时设置为2秒，避免阻塞
            response = requests.get(f'{base_url}/api/tags', timeout=2)
            if response.status_code == 200:
                data = response.json()
                status['connected'] = True
                status['available_models'] = [m['name'] for m in data.get('models', [])]
        except:
            pass
    
    # 缓存结果
    api_cache[cache_key] = {
        'data': status,
        'timestamp': time.time()
    }
    
    return jsonify({'code': 200, 'data': status})


@app.route('/api/user/<int:uid>', methods=['GET'])
def get_user(uid):
    """获取用户信息"""
    cache_key = f'user_{uid}'
    import time
    
    # 检查缓存
    if cache_key in api_cache:
        cached_data = api_cache[cache_key]
        if time.time() - cached_data['timestamp'] < 600:  # 10分钟缓存
            return jsonify({'code': 200, 'data': cached_data['data']})
    
    user = User.query.get(uid)
    if not user:
        return jsonify({'code': 404, 'msg': '用户不存在'}), 404
    
    user_data = user.to_dict()
    
    # 缓存结果
    api_cache[cache_key] = {
        'data': user_data,
        'timestamp': time.time()
    }
    
    return jsonify({'code': 200, 'data': user_data})

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    """获取仪表盘数据（综合API）"""
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'code': 400, 'msg': '缺少用户ID'}), 400
    
    cache_key = f'dashboard_{user_id}'
    import time
    
    # 检查缓存
    if cache_key in api_cache:
        cached_data = api_cache[cache_key]
        if time.time() - cached_data['timestamp'] < 600:  # 10分钟缓存
            return jsonify({'code': 200, 'data': cached_data['data']})
    
    # 获取用户信息
    user = User.query.get(user_id)
    if not user:
        return jsonify({'code': 404, 'msg': '用户不存在'}), 404
    
    user_data = user.to_dict()
    
    # 获取模型状态
    from config import Config
    base_url = Config.OLLAMA_BASE_URL
    enabled = Config.USE_OLLAMA
    # 使用生成模型的名称，而不是嵌入模型的名称
    model = "qwen2.5:7b"
    
    status = {
        'enabled': enabled,
        'base_url': base_url,
        'configured_model': model,
        'connected': False,
        'available_models': []
    }
    
    # 只有在启用Ollama时才尝试连接
    if enabled:
        try:
            # 超时设置为2秒，避免阻塞
            response = requests.get(f'{base_url}/api/tags', timeout=2)
            if response.status_code == 200:
                data = response.json()
                status['connected'] = True
                status['available_models'] = [m['name'] for m in data.get('models', [])]
        except:
            pass
    
    # 构建响应数据
    dashboard_data = {
        'user': user_data,
        'model_status': status
    }
    
    # 缓存结果
    api_cache[cache_key] = {
        'data': dashboard_data,
        'timestamp': time.time()
    }
    
    return jsonify({'code': 200, 'data': dashboard_data})


@app.route('/api/user/profile', methods=['PUT'])
def update_profile():
    """更新用户基本信息"""
    # 处理multipart/form-data格式的请求
    user_id = request.form.get('user_id')
    
    if not user_id:
        return jsonify({'code': 400, 'msg': '缺少用户ID'}), 400
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'code': 404, 'msg': '用户不存在'}), 404
    
    # 更新用户信息
    if 'username' in request.form:
        user.username = request.form['username']
    if 'email' in request.form:
        user.email = request.form['email']
    if 'major' in request.form:
        user.major = request.form['major']
    if 'grade' in request.form:
        user.grade = request.form['grade']
    
    # 处理头像上传
    if 'avatar' in request.files:
        avatar_file = request.files['avatar']
        if avatar_file.filename:
            # 确保上传目录存在
            upload_dir = os.path.join(app.root_path, app.config['AVATAR_UPLOAD_FOLDER'])
            if not os.path.exists(upload_dir):
                os.makedirs(upload_dir)
            
            # 删除旧头像文件
            if user.avatar and user.avatar.startswith('/static/avatars/'):
                old_filename = user.avatar.replace('/static/avatars/', '')
                old_filepath = os.path.join(upload_dir, old_filename)
                if os.path.exists(old_filepath):
                    try:
                        os.remove(old_filepath)
                        print(f'已删除旧头像: {old_filepath}')
                    except Exception as e:
                        print(f'删除旧头像失败: {e}')
            
            # 生成唯一文件名
            import uuid
            ext = os.path.splitext(avatar_file.filename)[1]
            filename = f'{uuid.uuid4()}{ext}'
            filepath = os.path.join(upload_dir, filename)
            
            # 保存文件
            avatar_file.save(filepath)
            
            # 更新用户头像路径
            user.avatar = f'/static/avatars/{filename}'
    
    db.session.commit()
    
    # 清除缓存
    if f'user_{user_id}' in api_cache:
        del api_cache[f'user_{user_id}']
    if f'dashboard_{user_id}' in api_cache:
        del api_cache[f'dashboard_{user_id}']
    
    return jsonify({'code': 200, 'msg': '个人信息更新成功', 'data': {'avatar': user.avatar}})


# ==================== 知识库API接口 ====================

@app.route('/api/knowledge-base/upload', methods=['POST'])
@token_required
def upload_knowledge_file():
    """
    上传文件到知识库
    POST /api/knowledge-base/upload
    """
    if 'file' not in request.files:
        return jsonify({'code': 400, 'msg': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'code': 400, 'msg': '未选择文件'}), 400

    # 获取知识库名称，默认为default
    knowledge_base = request.form.get('knowledge_base', 'default')

    # 检查文件类型
    allowed_extensions = {'.pdf', '.txt', '.docx'}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        return jsonify({'code': 400, 'msg': f'不支持的文件格式，仅支持: {{", ".join(allowed_extensions)}}'}), 400

    try:
        # 保存文件 - 为每个知识库创建独立的文件夹
        upload_dir = os.path.join(app.root_path, app.config['DOCUMENT_UPLOAD_FOLDER'], knowledge_base)
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir)
        filename = os.path.join(upload_dir, file.filename)
        # 转换为绝对路径
        filename = os.path.abspath(filename)
        file.save(filename)

        # 获取知识库实例
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(knowledge_base)
        
        # 添加到知识库
        success = kb_instance.add_document(filename)

        if success:
            return jsonify({
                'code': 200,
                'msg': '文件已成功添加到知识库',
                'data': {'filename': file.filename, 'knowledge_base': knowledge_base}
            })
        else:
            return jsonify({'code': 500, 'msg': '添加文件到知识库失败'}), 500

    except Exception as e:
        return jsonify({'code': 500, 'msg': f'上传失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/stats', methods=['GET'])
@token_required
def get_knowledge_base_stats():
    """
    获取知识库统计信息
    GET /api/knowledge-base/stats?knowledge_base=default
    """
    try:
        knowledge_base = request.args.get('knowledge_base', 'default')
        
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(knowledge_base)
        
        stats = kb_instance.get_stats()
        return jsonify({
            'code': 200,
            'data': stats
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'获取统计信息失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/delete', methods=['DELETE'])
@token_required
def delete_knowledge_base():
    """
    删除知识库
    DELETE /api/knowledge-base/delete?knowledge_base=default
    """
    try:
        knowledge_base = request.args.get('knowledge_base', 'default')
        
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(knowledge_base)
        
        kb_instance.delete_knowledge_base()
        return jsonify({
            'code': 200,
            'msg': '知识库已删除'
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'删除知识库失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/rename', methods=['POST'])
@token_required
def rename_knowledge_base():
    """
    重命名知识库
    POST /api/knowledge-base/rename
    {
        "old_name": "old_kb",
        "new_name": "new_kb"
    }
    """
    try:
        data = request.json
        old_name = data.get('old_name', '')
        new_name = data.get('new_name', '')
        
        if not old_name or not new_name:
            return jsonify({'code': 400, 'msg': '缺少知识库名称'}), 400
        
        # 不允许重命名默认知识库
        if old_name == 'default':
            return jsonify({'code': 400, 'msg': '不能重命名默认知识库'}), 400
        
        # 验证新知识库名称
        if '/' in new_name or '\\' in new_name or '.' in new_name:
            return jsonify({'code': 400, 'msg': '知识库名称不能包含斜杠或点号'}), 400
        
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(old_name)
        
        # 重命名知识库
        kb_instance.rename_knowledge_base(new_name)
        
        return jsonify({
            'code': 200,
            'msg': '知识库已重命名'
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'重命名知识库失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/create', methods=['POST'])
@token_required
def create_knowledge_base():
    """
    创建知识库
    POST /api/knowledge-base/create
    {
        "name": "new_kb"
    }
    """
    try:
        data = request.json
        name = data.get('name', '')
        
        if not name:
            return jsonify({'code': 400, 'msg': '缺少知识库名称'}), 400
        
        # 验证知识库名称
        if '/' in name or '\\' in name or '.' in name:
            return jsonify({'code': 400, 'msg': '知识库名称不能包含斜杠或点号'}), 400
        
        # 初始化新知识库
        from app.services.knowledge_service import QAModule
        QAModule(name)
        
        return jsonify({
            'code': 200,
            'msg': '知识库已创建'
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'创建知识库失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/list', methods=['GET'])
@token_required
def get_knowledge_base_list():
    """
    获取所有知识库列表
    GET /api/knowledge-base/list
    """
    try:
        import os
        from app.services.knowledge_service import QAModule
        # 获取所有知识库实例
        knowledge_bases = QAModule.get_all_instances()
        
        # 扫描文件系统中的知识库文件
        for file in os.listdir('.'):
            if file.endswith('_vector_store.pkl'):
                # 提取知识库名称
                kb_name = file.replace('knowledge_base_', '').replace('_vector_store.pkl', '')
                if kb_name not in knowledge_bases:
                    knowledge_bases.append(kb_name)
                    # 初始化知识库
                    QAModule(kb_name)
        
        # 确保默认知识库存在
        if 'default' not in knowledge_bases:
            knowledge_bases.append('default')
            # 初始化默认知识库
            QAModule('default')
        
        return jsonify({
            'code': 200,
            'data': knowledge_bases
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'获取知识库列表失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/vectors', methods=['GET'])
@token_required
def get_knowledge_base_vectors():
    """
    获取知识库向量列表
    GET /api/knowledge-base/vectors?page=1&page_size=10&source=file_path&knowledge_base=default
    """
    try:
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 10, type=int)
        source = request.args.get('source')
        knowledge_base = request.args.get('knowledge_base', 'default')
        
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(knowledge_base)
        
        vectors = kb_instance.get_vectors(page=page, page_size=page_size, source=source)
        return jsonify({
            'code': 200,
            'data': vectors
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'获取向量列表失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/sources', methods=['GET'])
@token_required
def get_knowledge_base_sources():
    """
    获取知识库文件来源列表
    GET /api/knowledge-base/sources?knowledge_base=default
    """
    try:
        knowledge_base = request.args.get('knowledge_base', 'default')
        
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(knowledge_base)
        
        sources = kb_instance.get_file_sources()
        return jsonify({
            'code': 200,
            'data': sources
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'获取文件来源失败: {str(e)}'}), 500


@app.route('/api/knowledge-base/sources/<path:file_path>', methods=['DELETE'])
@token_required
def delete_knowledge_base_source(file_path):
    """
    按文件来源删除向量数据
    DELETE /api/knowledge-base/sources/file_path?knowledge_base=default
    """
    try:
        # 解码URL编码的文件路径
        import urllib.parse
        file_path = urllib.parse.unquote(file_path)
        
        knowledge_base = request.args.get('knowledge_base', 'default')
        
        from app.services.knowledge_service import QAModule
        kb_instance = QAModule(knowledge_base)
        
        deleted_count = kb_instance.delete_by_source(file_path)
        
        # 删除文档上传文件夹中的文件
        uploads_dir = os.path.join(app.root_path, app.config['DOCUMENT_UPLOAD_FOLDER'], knowledge_base)
        if os.path.exists(uploads_dir):
            # 获取文件名
            filename = os.path.basename(file_path)
            file_to_delete = os.path.join(uploads_dir, filename)
            if os.path.exists(file_to_delete):
                try:
                    os.remove(file_to_delete)
                    print(f'已删除上传文件: {file_to_delete}')
                except Exception as e:
                    print(f'删除上传文件失败: {e}')
        
        return jsonify({
            'code': 200,
            'msg': f'成功删除 {deleted_count} 个向量',
            'data': {'deleted_count': deleted_count}
        })
    except Exception as e:
        return jsonify({'code': 500, 'msg': f'删除向量失败: {str(e)}'}), 500


@app.route('/api/ask-with-kb', methods=['POST'])
def ask_question_with_kb():
    """
    智能问答接口（带知识库支持）
    POST /api/ask-with-kb
    {
        "question": "什么是机器学习？",
        "user_id": 1,
        "model_type": "ollama",
        "knowledge_base": "default"
    }
    """
    data = request.json
    question = data.get('question', '').strip()
    user_id = data.get('user_id', 1)
    model_type = data.get('model_type')
    knowledge_base = data.get('knowledge_base', 'default')

    if not question:
        return jsonify({'code': 400, 'msg': '问题不能为空'}), 400

    session_id = data.get('session_id') or str(uuid.uuid4())

    try:
        # 首先尝试从知识库获取答案
        from app.services.knowledge_service import QAModule
        qa_module = QAModule(knowledge_base, model_type)
        kb_result = qa_module.query_with_knowledge(question, model_type=model_type)

        # 如果知识库有相关内容，使用知识库答案
        if kb_result['context_used'] and kb_result['sources']:
            answer = kb_result['answer']
            sources = kb_result['sources']
            kb_used = True
            model_used = 'knowledge_base'
        else:
            # 否则使用AI服务
            # 获取用户对象
            user = User.get_by_id(user_id)
            result = ai_service.ask_question(question, user=user, user_id=user_id, model_type=model_type)
            answer = result['answer']
            sources = result.get('sources', [])
            kb_used = False
            model_used = result.get('model_used', 'unknown')

        # 保存学习记录
        record = LearningRecord(
            user_id=user_id,
            question=question,
            ai_answer=answer,
            sources=str(sources),
            session_id=session_id,
            category='其他',
            knowledge_base=knowledge_base
        )
        db.session.add(record)
        db.session.commit()

        # 清除相关缓存
        if f'sessions_{user_id}' in api_cache:
            del api_cache[f'sessions_{user_id}']
        if f'records_{user_id}_1' in api_cache:
            del api_cache[f'records_{user_id}_1']

        return jsonify({
            'code': 200,
            'data': {
                'answer': answer,
                'sources': sources,
                'record_id': record.id,
                'session_id': session_id,
                'model_used': model_used,
                'knowledge_base_used': kb_used,
                'knowledge_base_name': knowledge_base
            }
        })

    except Exception as e:
        return jsonify({'code': 500, 'msg': f'服务器错误: {str(e)}'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
