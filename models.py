"""
数据模型定义
严格按需求文档中的表结构定义 User、Blacklist、AuditLog。
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    """用户表 (users)"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, comment="登录账号(工号/ID)")
    password_hash = Column(String(128), nullable=False, comment="BCrypt 加密后的密码")
    full_name = Column(String(64), nullable=False, comment="真实姓名(用于显示水印)")
    role = Column(String(16), nullable=False, comment="角色: admin 或 teacher")
    is_active = Column(Boolean, default=True, nullable=False, comment="账号状态")


class Blacklist(Base):
    """失信名单主表 (blacklist)"""

    __tablename__ = "blacklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, index=True, comment="姓名")
    id_card = Column(String(32), nullable=False, unique=True, comment="身份证号(清洗后)")
    major = Column(String(128), nullable=True, comment="所学专业")
    reason = Column(Text, nullable=True, comment="失信/违规具体原因")
    punishment_date = Column(Date, nullable=True, comment="处分日期")
    status = Column(Integer, default=1, nullable=False, comment="1=生效中, 0=已撤销/软删除")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")


class AuditLog(Base):
    """审计日志表 (audit_logs)，只增不减"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    operator_name = Column(String(64), nullable=False, comment="操作人姓名(冗余存储)")
    action_type = Column(String(32), nullable=False, comment="LOGIN, QUERY_BATCH, IMPORT, ADD, DELETE, BACKUP")
    target = Column(String(256), nullable=True, comment="操作对象简述")
    details = Column(Text, nullable=True, comment="变更详情(JSON/Text)")
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, comment="操作时间")
