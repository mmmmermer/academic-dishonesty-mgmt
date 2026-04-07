"""
管理员用户管理：用户列表、新增用户、密码重置、启用/禁用。
"""
import bcrypt
import pandas as pd
import streamlit as st

from core.config import (
    AUDIT_ADD,
    AUDIT_RESET_PWD,
    AUDIT_TOGGLE_USER,
    EMPTY_NO_USER,
    MSG_TRY_AGAIN,
    PASSWORD_MIN_LEN,
    SESSION_KEY_USERNAME,
    SUCCESS_PWD_RESET,
    USERNAME_MAX_LEN,
)
from core.models import User
from core.session_store import delete_sessions_for_user
from core.audit_logger import log_audit_action


def _render_user_list(users):
    st.subheader("用户列表")
    st.caption("工号唯一；禁用后该账号无法登录。")
    if not users:
        st.caption(EMPTY_NO_USER)
        return
    user_df = pd.DataFrame(
        [
            {"ID": u.id, "工号": u.username, "姓名": u.full_name, "角色": "管理员" if u.role == "admin" else "教师", "状态": "正常" if u.is_active else "已禁用"}
            for u in users
        ]
    )
    st.dataframe(user_df, use_container_width=True, hide_index=True)


def _try_add_user(db, uname, pwd, fname, new_role):
    if not uname or not pwd or not fname:
        st.error("请填写工号、密码和姓名。")
        return False
    if len(pwd) < PASSWORD_MIN_LEN:
        st.error(f"密码至少 {PASSWORD_MIN_LEN} 位，请重新输入。")
        return False
    if len(uname) > USERNAME_MAX_LEN:
        st.error(f"工号长度不能超过 {USERNAME_MAX_LEN} 个字符。")
        return False
    try:
        with st.spinner("正在保存..."):
            if db.query(User).filter(User.username == uname).first():
                st.error("该工号已存在。")
                return False
            pwd_hash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            u = User(username=uname, password_hash=pwd_hash, full_name=fname, role="admin" if new_role == "管理员" else "teacher", is_active=True)
            db.add(u)
            db.commit()
            log_audit_action(AUDIT_ADD, target=f"用户 {uname}", details=f"角色 {new_role}")
            st.success("用户已添加。")
            return True
    except Exception:
        db.rollback()
        st.error("添加失败，" + MSG_TRY_AGAIN)
        return False


def _render_add_user_form(db):
    st.subheader("新增用户")
    with st.form("admin_add_user_form"):
        new_username = st.text_input("工号/登录名", key="new_username", max_chars=USERNAME_MAX_LEN)
        new_password = st.text_input("密码", type="password", key="new_password")
        new_full_name = st.text_input("真实姓名", key="new_full_name", max_chars=USERNAME_MAX_LEN)
        new_role = st.selectbox("角色", ["教师", "管理员"], key="new_role")
        if st.form_submit_button("添加用户"):
            uname = (new_username or "").strip()
            pwd = (new_password or "").strip()
            fname = (new_full_name or "").strip()
            if _try_add_user(db, uname, pwd, fname, new_role):
                st.rerun()


def _render_reset_password_section(db, users):
    st.caption("密码重置")
    reset_options = [f"{u.username}（{u.full_name}）" for u in users]
    reset_choice = st.selectbox("选择用户", reset_options, key="reset_user_choice", label_visibility="collapsed")
    reset_password = st.text_input("新密码", type="password", key="reset_password")
    if not st.button("重置密码", key="admin_reset_pwd_btn"):
        return
    pwd_stripped = (reset_password or "").strip()
    if not pwd_stripped:
        st.error("请输入新密码。")
        return
    if len(pwd_stripped) < PASSWORD_MIN_LEN:
        st.error(f"密码至少 {PASSWORD_MIN_LEN} 位，请重新输入。")
        return
    try:
        idx = reset_options.index(reset_choice) if reset_choice in reset_options else -1
        if idx < 0:
            st.error("未找到该用户。")
            return
        user = users[idx]
        with st.spinner("正在重置..."):
            user.password_hash = bcrypt.hashpw(pwd_stripped.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            db.commit()
            delete_sessions_for_user(user.username)
            log_audit_action(AUDIT_RESET_PWD, target=f"密码重置 {user.username}", details="")
            st.success(SUCCESS_PWD_RESET)
            st.rerun()
    except Exception:
        db.rollback()
        st.error("重置失败，" + MSG_TRY_AGAIN)


def _render_toggle_user_section(db, users):
    st.caption("启用/禁用账号")
    toggle_options = [f"{u.username}（{u.full_name}）— {'正常' if u.is_active else '已禁用'}" for u in users]
    toggle_choice = st.selectbox("选择用户", toggle_options, key="toggle_user_choice", label_visibility="collapsed")
    if not st.button("切换状态", key="admin_toggle_btn"):
        return
    try:
        idx = toggle_options.index(toggle_choice) if toggle_choice in toggle_options else -1
        if idx < 0:
            st.error("未找到该用户。")
            return
        target_user = users[idx]
        if target_user.username == st.session_state.get(SESSION_KEY_USERNAME):
            st.warning("不能禁用当前登录账号。")
            return
        with st.spinner("正在更新..."):
            target_user.is_active = not target_user.is_active
            db.commit()
            if not target_user.is_active:
                delete_sessions_for_user(target_user.username)
            status = "启用" if target_user.is_active else "禁用"
            log_audit_action(AUDIT_TOGGLE_USER, target=f"账号{status} {target_user.username}", details="")
            st.success(f"已{status} {target_user.username}。")
            st.rerun()
    except Exception:
        db.rollback()
        st.error("操作失败，" + MSG_TRY_AGAIN)


def _render_user_management(db):
    """用户管理：用户列表、新增用户、密码重置、启用/禁用。"""
    users = db.query(User).order_by(User.id).all()
    _render_user_list(users)
    st.divider()
    _render_add_user_form(db)
    st.divider()
    st.subheader("密码重置与启用/禁用")
    st.caption("选择用户后执行重置密码或切换账号状态；不能禁用当前登录账号。")
    if not users:
        st.caption(EMPTY_NO_USER)
    else:
        rcol1, rcol2 = st.columns(2)
        with rcol1:
            _render_reset_password_section(db, users)
        with rcol2:
            _render_toggle_user_section(db, users)
