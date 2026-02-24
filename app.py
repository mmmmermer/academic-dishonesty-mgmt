"""
主入口：会话初始化、水印、导航、登出、启动时自动备份、会话超时、全局异常捕获与日志
"""
import html
import logging
import os
import time

import streamlit as st

from core.config import (
    LABEL_CURRENT_USER,
    LABEL_LOGOUT,
    LABEL_ROLE,
    LABEL_SELECT_PANEL,
    LOG_FILENAME,
    LOG_SUBDIR,
    MSG_SESSION_TIMEOUT,
    MSG_SESSION_TIMEOUT_SOON,
    MSG_SYSTEM_ERROR,
    MSG_UNKNOWN_ROLE,
    ROLE_ADMIN,
    ROLE_TEACHER,
    SESSION_KEY_AUTO_BACKUP_DONE,
    SESSION_KEY_LAST_ACTIVITY,
    SESSION_KEY_LOGGED_IN,
    SESSION_KEY_USER_ID,
    SESSION_KEY_USER_NAME,
    SESSION_KEY_USER_ROLE,
    SESSION_KEY_USERNAME,
    SESSION_KEY_LOGIN_FAIL_RECORDS,
    SESSION_TIMEOUT_MINUTES,
    SESSION_TIMEOUT_WARN_MINUTES,
)

# 日志：路径由 config 统一配置（阶段四）
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_SUBDIR)


def _setup_logging():
    """配置根 logger：写入 logs/app.log，便于排查问题。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except OSError:
        pass
    log_file = os.path.join(LOG_DIR, LOG_FILENAME)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    try:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(fmt))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        if not root.handlers:
            root.addHandler(handler)
    except Exception:
        pass


_setup_logging()
logger = logging.getLogger(__name__)

# 多实例部署时禁止使用 SQLite（阶段三）：仅当显式设置 MULTI_INSTANCE=1 或 true 时检查并打日志
_multi_instance = os.environ.get("MULTI_INSTANCE", "").strip().lower()
if _multi_instance in ("1", "true"):
    try:
        from core.database import IS_SQLITE
        if IS_SQLITE:
            logger.warning("MULTI_INSTANCE=1 但当前使用 SQLite；多实例部署必须使用 MySQL/PostgreSQL，请设置 DATABASE_URL。")
    except Exception:
        pass

# 教师/管理员页面与侧栏导航（顶层导入，避免在 sidebar 内重复 import）
try:
    from views.teacher_page import render_teacher_page, render_teacher_sidebar_nav
except ImportError:

    def render_teacher_page():
        st.info("教师页面开发中，敬请期待。")

    def render_teacher_sidebar_nav():
        """占位：教师模块未安装时侧栏不渲染导航。"""
        pass

try:
    from views.admin_page import render_admin_page, render_admin_sidebar_nav
except ImportError:

    def render_admin_page():
        st.info("管理员页面开发中，敬请期待。")

    def render_admin_sidebar_nav():
        """占位：管理员模块未安装时侧栏不渲染导航。"""
        pass

from views.login import render_login_page
from core.session_store import delete_session, get_session


def _restore_session_from_sid():
    """
    若当前未登录但 URL 带 sid，则从服务端 session 恢复登录态（解决刷新掉线）。
    无效或过期的 sid 会从 URL 中移除。
    """
    if st.session_state.get(SESSION_KEY_LOGGED_IN):
        return
    sid = st.query_params.get("sid")
    if not sid:
        return
    data = get_session(sid)
    if not data:
        del st.query_params["sid"]
        return
    st.session_state[SESSION_KEY_LOGGED_IN] = True
    st.session_state[SESSION_KEY_USER_ID] = data.get("user_id", 0)
    st.session_state[SESSION_KEY_USERNAME] = data.get("username", "")
    st.session_state[SESSION_KEY_USER_ROLE] = data.get("role", "")
    st.session_state[SESSION_KEY_USER_NAME] = data.get("full_name", "")
    st.session_state[SESSION_KEY_LAST_ACTIVITY] = time.time()


def _run_auto_backup_once():
    """会话内仅执行一次：将 database.db 备份到 backups/，失败不阻塞启动。"""
    if st.session_state.get(SESSION_KEY_AUTO_BACKUP_DONE):
        return
    try:
        from core.utils import auto_backup
        auto_backup()
        st.session_state[SESSION_KEY_AUTO_BACKUP_DONE] = True
    except Exception:
        pass


def _init_session_state():
    """初始化会话键，避免 KeyError；键名使用 config 常量便于维护。"""
    defaults = [
        (SESSION_KEY_LOGGED_IN, False),
        (SESSION_KEY_USER_ROLE, ""),
        (SESSION_KEY_USER_NAME, ""),
        (SESSION_KEY_USER_ID, 0),
        (SESSION_KEY_USERNAME, ""),
        (SESSION_KEY_LAST_ACTIVITY, 0.0),
        (SESSION_KEY_LOGIN_FAIL_RECORDS, {}),
    ]
    for key, val in defaults:
        if key not in st.session_state:
            st.session_state[key] = val


def _inject_watermark():
    """仅登录后显示水印（姓名+工号），全局固定、不阻挡操作；退出登录后不注入。"""
    if not st.session_state.get(SESSION_KEY_LOGGED_IN):
        return
    raw_text = f"{st.session_state.get(SESSION_KEY_USER_NAME, '')} {st.session_state.get(SESSION_KEY_USERNAME, '')}"
    text = html.escape(raw_text)
    # 固定全屏、高层级、半透明、不响应点击，确保登录后全局可见且不消失；内容转义防 XSS
    css = f"""
    <div aria-hidden="true" style="
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 2147483647;
        opacity: 0.14;
        font-size: 96px;
        font-weight: 500;
        color: #666;
        transform: rotate(-25deg);
        display: flex;
        align-items: center;
        justify-content: center;
        white-space: nowrap;
    ">{text}</div>
    """
    st.markdown(css, unsafe_allow_html=True)


def _render_sidebar():
    """侧边栏：身份标题 + 功能导航（管理员/教师）+ 当前用户信息 + 登出。"""
    with st.sidebar:
        if st.session_state.get(SESSION_KEY_LOGGED_IN):
            role = st.session_state.get(SESSION_KEY_USER_ROLE, "")
            if role == "admin":
                render_admin_sidebar_nav()
            elif role == "teacher":
                render_teacher_sidebar_nav()
            if role in ("admin", "teacher"):
                st.divider()
            role_label = ROLE_ADMIN if role == "admin" else ROLE_TEACHER
            st.caption(
                f"**{LABEL_CURRENT_USER}**：{st.session_state.get(SESSION_KEY_USER_NAME, '')}（{st.session_state.get(SESSION_KEY_USERNAME, '')}）"
            )
            st.caption(f"**{LABEL_ROLE}**：{role_label}")
            st.divider()
            if st.button(LABEL_LOGOUT, key="logout_btn"):
                sid = st.query_params.get("sid")
                if sid:
                    delete_session(sid)
                    del st.query_params["sid"]
                st.session_state[SESSION_KEY_LOGGED_IN] = False
                st.session_state[SESSION_KEY_USER_ROLE] = ""
                st.session_state[SESSION_KEY_USER_NAME] = ""
                st.session_state[SESSION_KEY_USER_ID] = 0
                st.session_state[SESSION_KEY_USERNAME] = ""
                st.rerun()
        st.divider()


def _check_session_timeout() -> bool:
    """
    检查会话是否超时；若未超时则更新最后活动时间，必要时提示即将超时。
    :return: True 表示已超时并已清空会话（调用方应 rerun），False 表示未超时。
    """
    now = time.time()
    prev = st.session_state.get(SESSION_KEY_LAST_ACTIVITY, 0.0)
    timeout_seconds = SESSION_TIMEOUT_MINUTES * 60
    warn_seconds = SESSION_TIMEOUT_WARN_MINUTES * 60
    if prev > 0 and (now - prev) > timeout_seconds:
        sid = st.query_params.get("sid")
        if sid:
            delete_session(sid)
            del st.query_params["sid"]
        st.session_state[SESSION_KEY_LOGGED_IN] = False
        st.session_state[SESSION_KEY_USER_ROLE] = ""
        st.session_state[SESSION_KEY_USER_NAME] = ""
        st.session_state[SESSION_KEY_USER_ID] = 0
        st.session_state[SESSION_KEY_USERNAME] = ""
        st.session_state[SESSION_KEY_LAST_ACTIVITY] = 0.0
        st.warning(MSG_SESSION_TIMEOUT)
        return True
    remaining = timeout_seconds - (now - prev) if prev > 0 else timeout_seconds
    if 0 < remaining <= warn_seconds:
        warn_mins = max(1, int(remaining / 60))
        st.warning(MSG_SESSION_TIMEOUT_SOON.format(mins=warn_mins))
    st.session_state[SESSION_KEY_LAST_ACTIVITY] = now
    return False


def main():
    _init_session_state()
    _restore_session_from_sid()
    # 未登录时侧栏折叠（登录页不占左侧），登录后侧栏展开
    sidebar_state = "expanded" if st.session_state.get(SESSION_KEY_LOGGED_IN) else "collapsed"
    st.set_page_config(page_title="学术失信人员管理系统", layout="wide", initial_sidebar_state=sidebar_state)
    try:
        _run_auto_backup_once()
        _inject_watermark()
        # 仅登录后渲染侧栏导航，登录页不显示左侧栏内容
        if st.session_state.get(SESSION_KEY_LOGGED_IN):
            _render_sidebar()

        if not st.session_state.get(SESSION_KEY_LOGGED_IN):
            render_login_page()
            return

        if _check_session_timeout():
            st.rerun()

        role = st.session_state.get(SESSION_KEY_USER_ROLE, "")
        if role == "teacher":
            render_teacher_page()
        elif role == "admin":
            render_admin_page()
        else:
            st.warning(MSG_UNKNOWN_ROLE)

    except Exception as e:
        logger.exception("应用未捕获异常")
        st.error(MSG_SYSTEM_ERROR)
        with st.expander("技术详情（供管理员排查）", expanded=False):
            st.code(str(e), language="text")
            st.caption("完整堆栈已写入 logs/app.log。")
        st.stop()


if __name__ == "__main__":
    main()
