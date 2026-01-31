"""
登录页：表单校验、会话写入、登录审计日志
"""
import streamlit as st

from auth import verify_password
from database import SessionLocal
from models import AuditLog, User


def render_login_page():
    """渲染登录页：用户名、密码表单；校验后更新 session_state 并 rerun，失败则 st.error。"""
    st.title("校园学术失信人员管理系统")
    st.subheader("请登录")

    with st.form("login_form"):
        username = st.text_input("用户名（工号）", key="login_username")
        password = st.text_input("密码", type="password", key="login_password")
        submitted = st.form_submit_button("登录")

    if not submitted:
        return

    if not username or not password:
        st.error("请输入用户名和密码。")
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            st.error("用户名或密码错误。")
            return
        if not user.is_active:
            st.error("该账号已停用，请联系管理员。")
            return
        if not verify_password(password, user.password_hash):
            st.error("用户名或密码错误。")
            return
        # 校验通过：写登录审计日志
        log_db = SessionLocal()
        try:
            log_db.add(AuditLog(
                operator_name=user.full_name,
                action_type="LOGIN",
                target=user.username,
                details="",
            ))
            log_db.commit()
        except Exception:
            log_db.rollback()
        finally:
            log_db.close()
        # 写入会话并刷新
        st.session_state.logged_in = True
        st.session_state.user_role = user.role
        st.session_state.user_name = user.full_name
        st.session_state.user_id = user.id
        st.session_state.username = user.username  # 工号，用于水印
        st.rerun()
    except Exception:
        st.error("登录过程出错，请稍后重试。")
    finally:
        db.close()
