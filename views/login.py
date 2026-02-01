"""
登录页：表单校验、会话写入、登录审计日志、登录失败次数与冷却限制。
由 app 在未登录时调用；登录成功后写入 session_state 并 rerun。
"""
import time

import streamlit as st

from auth import verify_password
from config import LOGIN_COOLDOWN_SECONDS, LOGIN_FAIL_MAX, USERNAME_MAX_LEN
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

    username_stripped = (username or "").strip()
    if len(username_stripped) > USERNAME_MAX_LEN:
        st.error("用户名长度超出限制，请检查后重试。")
        return
    records = st.session_state.get("login_fail_records") or {}
    if username_stripped in records:
        count, last_ts = records[username_stripped]
        if count >= LOGIN_FAIL_MAX and (time.time() - last_ts) < LOGIN_COOLDOWN_SECONDS:
            st.error("登录失败次数过多，请 5 分钟后再试。")
            return
        # 冷却期已过则继续尝试，后续失败会重新计数

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username_stripped).first()
        if not user:
            _record_login_fail(st.session_state, username_stripped)
            st.error("用户名或密码错误。")
            return
        if not user.is_active:
            _record_login_fail(st.session_state, username_stripped)
            st.error("该账号已停用，请联系管理员。")
            return
        if not verify_password(password, user.password_hash):
            _record_login_fail(st.session_state, username_stripped)
            st.error("用户名或密码错误。")
            return
        # 校验通过：清除该账号失败记录
        if username_stripped in (st.session_state.get("login_fail_records") or {}):
            rec = st.session_state["login_fail_records"]
            if username_stripped in rec:
                del rec[username_stripped]
        # 写登录审计日志
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
        # 写入会话并刷新，设置最后活动时间供会话超时判断
        st.session_state.logged_in = True
        st.session_state.user_role = user.role
        st.session_state.user_name = user.full_name
        st.session_state.user_id = user.id
        st.session_state.username = user.username
        st.session_state.last_activity_at = time.time()
        st.rerun()
    except Exception:
        st.error("登录过程出错，请稍后重试。")
    finally:
        db.close()


def _record_login_fail(session_state, username_stripped: str) -> None:
    """记录一次登录失败，用于同一账号失败次数与冷却。"""
    records = session_state.get("login_fail_records")
    if records is None:
        records = {}
        session_state["login_fail_records"] = records
    count, _ = records.get(username_stripped, (0, 0.0))
    records[username_stripped] = (count + 1, time.time())
