"""
审计日志写入逻辑，负责操作溯源防篡改。
"""
import streamlit as st
import logging as _logging
from .database import db_session
from .models import AuditLog

_logger = _logging.getLogger(__name__)

try:
    from .config import SESSION_KEY_USER_NAME, SESSION_KEY_USERNAME
except ImportError:
    SESSION_KEY_USER_NAME = "user_name"
    SESSION_KEY_USERNAME = "username"


def log_audit_action(action_type: str, target: str = "", details: str = ""):
    """写入审计日志，供所有视图层统一调用。操作人从 session_state 获取。"""
    with db_session() as db:
        try:
            name = st.session_state.get(SESSION_KEY_USER_NAME, "未知")
            username = st.session_state.get(SESSION_KEY_USERNAME, "")
            # [H4 & M2] 确保能在操作人列体现具体账号，解决重名溯源问题，同时不破坏旧版 schema
            operator_full = f"{name} ({username})" if username else name
            
            log = AuditLog(
                operator_name=operator_full[:64],
                operator_username=username or None,
                action_type=action_type,
                target=target[:256] if target else None,
                details=details[:4096] if details else None,
            )
            db.add(log)
            db.commit()
        except Exception as exc:
            db.rollback()
            _logger.warning("审计日志写入失败 action=%s target=%s: %s", action_type, target[:64] if target else "", exc)
