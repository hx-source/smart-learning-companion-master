"""Database models."""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from app.models.models import User, LearningRecord, ClassKnowledgeBase, UserKnowledgeBase, UserLog

__all__ = ['db', 'User', 'LearningRecord', 'ClassKnowledgeBase', 'UserKnowledgeBase', 'UserLog']
