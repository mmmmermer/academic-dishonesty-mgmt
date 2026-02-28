"""
登录页：表单校验、会话写入、登录审计日志、登录失败次数与冷却限制。
由 app 在未登录时调用；登录成功后写入 session_state 并 rerun。
"""
import logging
import time

import streamlit as st

logger = logging.getLogger(__name__)

from core.auth import verify_password
from core.config import (
    AUDIT_LOGIN,
    BTN_LOGIN,
    LABEL_LOGIN_PASSWORD,
    LABEL_LOGIN_USERNAME,
    LOGIN_COOLDOWN_SECONDS,
    LOGIN_FAIL_MAX,
    MSG_ACCOUNT_DISABLED,
    MSG_ENTER_USERNAME_PASSWORD,
    MSG_LOGIN_ERROR,
    MSG_LOGIN_TOO_MANY_FAIL,
    MSG_LOGIN_WRONG,
    MSG_USERNAME_TOO_LONG,
    SESSION_KEY_LAST_ACTIVITY,
    SESSION_KEY_LOGIN_FAIL_RECORDS,
    SESSION_KEY_LOGGED_IN,
    SESSION_KEY_USER_ID,
    SESSION_KEY_USER_NAME,
    SESSION_KEY_USER_ROLE,
    SESSION_KEY_USERNAME,
    SUBTITLE_LOGIN,
    TITLE_APP,
    USERNAME_MAX_LEN,
)
from core.database import db_session
from core.models import User
from core.session_store import create_session
from core.utils import log_audit_action


def render_login_page():
    """渲染登录页：工号、密码表单；校验后更新 session_state 并 rerun，失败则 st.error。"""
    # 使用中间列收窄登录区域，避免输入框占满全屏
    _, col_center, _ = st.columns([1, 2, 1])
    with col_center:
        st.title(TITLE_APP)
        st.subheader(SUBTITLE_LOGIN)
        with st.form("login_form"):
            username = st.text_input(LABEL_LOGIN_USERNAME, key="login_username")
            password = st.text_input(LABEL_LOGIN_PASSWORD, type="password", key="login_password")
            submitted = st.form_submit_button(BTN_LOGIN)

    if not submitted:
        return

    def _show_error(msg: str) -> None:
        _, c, _ = st.columns([1, 2, 1])
        with c:
            st.error(msg)

    if not username or not password:
        _show_error(MSG_ENTER_USERNAME_PASSWORD)
        return

    username_stripped = (username or "").strip()
    if len(username_stripped) > USERNAME_MAX_LEN:
        _show_error(MSG_USERNAME_TOO_LONG)
        return
    records = st.session_state.get(SESSION_KEY_LOGIN_FAIL_RECORDS) or {}
    if username_stripped in records:
        count, last_ts = records[username_stripped]
        if count >= LOGIN_FAIL_MAX and (time.time() - last_ts) < LOGIN_COOLDOWN_SECONDS:
            _show_error(MSG_LOGIN_TOO_MANY_FAIL)
            return
        # 冷却期已过则继续尝试，后续失败会重新计数

    with db_session() as db:
        try:
            user = db.query(User).filter(User.username == username_stripped).first()
            if not user:
                _record_login_fail(st.session_state, username_stripped)
                logger.info("登录失败 user=%s reason=user_not_found", username_stripped)
                _show_error(MSG_LOGIN_WRONG)
                return
            if not user.is_active:
                _record_login_fail(st.session_state, username_stripped)
                logger.info("登录失败 user=%s reason=account_disabled", username_stripped)
                _show_error(MSG_ACCOUNT_DISABLED)
                return
            if not verify_password(password, user.password_hash):
                _record_login_fail(st.session_state, username_stripped)
                logger.info("登录失败 user=%s reason=wrong_password", username_stripped)
                _show_error(MSG_LOGIN_WRONG)
                return
            # 校验通过：清除该账号失败记录
            rec = st.session_state.get(SESSION_KEY_LOGIN_FAIL_RECORDS)
            if rec and username_stripped in rec:
                del rec[username_stripped]
            # 写入会话状态（在 db_session 关闭前读取 user 属性）
            user_id, user_role, user_full_name, user_username = user.id, user.role, user.full_name, user.username
        except Exception:
            logger.exception("登录过程异常 user=%s", username_stripped)
            _show_error(MSG_LOGIN_ERROR)
            return

    # DB 会话已关闭，写审计日志用独立会话
    log_audit_action(AUDIT_LOGIN, target=user_username, details="")
    st.session_state[SESSION_KEY_LOGGED_IN] = True
    st.session_state[SESSION_KEY_USER_ROLE] = user_role
    st.session_state[SESSION_KEY_USER_NAME] = user_full_name
    st.session_state[SESSION_KEY_USER_ID] = user_id
    st.session_state[SESSION_KEY_USERNAME] = user_username
    st.session_state[SESSION_KEY_LAST_ACTIVITY] = time.time()
    token = create_session(user_id, user_username, user_role, user_full_name)
    st.query_params["sid"] = token
    logger.info("登录成功 user=%s role=%s", username_stripped, user_role)
    st.rerun()


def _record_login_fail(session_state, username_stripped: str) -> None:
    """记录一次登录失败，用于同一账号失败次数与冷却。"""
    records = session_state.get(SESSION_KEY_LOGIN_FAIL_RECORDS)
    if records is None:
        records = {}
        session_state[SESSION_KEY_LOGIN_FAIL_RECORDS] = records
    count, _ = records.get(username_stripped, (0, 0.0))
    records[username_stripped] = (count + 1, time.time())
