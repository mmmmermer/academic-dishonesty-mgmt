import pandas as pd
import streamlit as st
from core.database import db_session
from core.models import AuditLog
from core.config import (
    AUDIT_TYPE_NAMES,
    EMPTY_CANNOT_GET_USER,
    EMPTY_NO_OPERATION_LOG,
    SESSION_KEY_USERNAME,
    MSG_TRY_AGAIN,
)

def render_my_logs():
    """个人记录：展示当前用户最近操作历史（审计日志）。使用 db_session 确保会话关闭。"""
    st.subheader("个人记录")
    st.caption("您最近的操作历史（最多 100 条）。")
    username = st.session_state.get(SESSION_KEY_USERNAME, "")
    if not username:
        st.caption(EMPTY_CANNOT_GET_USER)
        return
    with db_session() as db:
        try:
            with st.spinner("加载中..."):
                logs = (
                    db.query(AuditLog)
                    .filter(AuditLog.operator_username == username)
                    .order_by(AuditLog.timestamp.desc())
                    .limit(100)
                    .all()
                )
            if not logs:
                st.caption(EMPTY_NO_OPERATION_LOG)
                return
            log_df = pd.DataFrame([
                {
                    "时间": r.timestamp.strftime('%Y-%m-%d %H:%M:%S') if r.timestamp else "",
                    "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type),
                    "对象": r.target or "",
                    "详情": (r.details or "")[:80],
                }
                for r in logs
            ])
            st.dataframe(log_df, use_container_width=True, hide_index=True)
        except Exception:
            st.error("加载失败，" + MSG_TRY_AGAIN)
