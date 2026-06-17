"""Database models."""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from app.models.models import User, LearningRecord, ClassRoom, ClassMember, ClassJoinRequest, ClassMemberLog, ClassKnowledgeBase, UserKnowledgeBase, KnowledgeDocument, UserLog

__all__ = ['db', 'User', 'LearningRecord', 'ClassRoom', 'ClassMember', 'ClassJoinRequest', 'ClassMemberLog', 'ClassKnowledgeBase', 'UserKnowledgeBase', 'KnowledgeDocument', 'UserLog']
