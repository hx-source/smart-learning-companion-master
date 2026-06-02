"""
数据库模型模块
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# 从 models.py 导入所有模型
from app.models.models import User, LearningRecord, UserLog

# 导出模型
export = [User, LearningRecord, UserLog]
