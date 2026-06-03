"""
智学伴 - AI个性化学习伴侣系统
启动脚本
"""

import os
import sys
import importlib.util

# 解决 OpenMP 运行时库冲突问题。
# PaddleOCR / Faiss / NumPy 等依赖可能同时加载 OpenMP，Windows 下容易报重复库错误。
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

# 添加当前目录到 Python 路径，保证直接执行 `python run.py` 时也能导入本项目模块。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 动态导入 app.py 文件。
# 项目里同时存在 `app.py` 和 `app/` 包，直接 `import app` 可能产生名称冲突，
# 因此这里显式按文件路径加载 Flask 应用入口。
spec = importlib.util.spec_from_file_location("flask_app", os.path.join(os.path.dirname(__file__), "app.py"))
flask_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flask_app)

# 获取app和db实例
app = flask_app.app
db = flask_app.db

# 判断是否是Flask reload进程
is_reloader = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


def init_database():
    """初始化数据库表结构。

    SQLAlchemy 会根据 app.models 中定义的模型创建缺失的数据表。
    这里不会自动做字段迁移；如果生产环境表结构变更，建议配合迁移工具。
    """
    with app.app_context():
        if not is_reloader:
            print("=" * 50)
            print("正在初始化数据库...")
            print("=" * 50)
        
        # 创建所有表
        db.create_all()
        
        if not is_reloader:
            print("✅ 数据库表创建完成")
            print("=" * 50)
            print("数据库初始化完成！")
            print("=" * 50)


def check_database_connection():
    """检查数据库连接是否可用，启动前先失败退出，避免服务半启动。"""
    try:
        with app.app_context():
            db.engine.connect()
            return True
    except Exception as e:
        print(f"❌ 数据库连接失败: {str(e)}")
        return False


def start_server():
    """启动 Flask 开发服务器。"""
    if not is_reloader:
        print("\n" + "=" * 50)
        print("正在启动智学伴系统...")
        print("=" * 50)
        print(f"访问地址: http://localhost:5000")
        print("=" * 50 + "\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)


def main():
    """本地启动流程：检查数据库 -> 初始化表 -> 启动 Web 服务。"""
    if not is_reloader:
        print("\n" + "=" * 60)
        print("    智学伴 - AI个性化学习伴侣系统")
        print("=" * 60)
    
    # 检查数据库连接
    if not check_database_connection():
        print("\n❌ 无法连接到数据库，请检查数据库配置")
        sys.exit(1)
    
    if not is_reloader:
        print("✅ 数据库连接成功")
    
    # 初始化数据库
    init_database()
    
    # 启动服务器
    start_server()


if __name__ == '__main__':
    main()
