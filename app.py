"""
主入口：会话初始化、水印、导航、登出、启动时自动备份、会话超时、全局异常捕获与日志
"""
import html
import logging
import os
import time

import streamlit as st

from config import SESSION_TIMEOUT_MINUTES

# 日志：写入 logs/app.log，便于排查问题
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _setup_logging():
    """配置根 logger：写入 logs/app.log，便于排查问题。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except OSError:
        pass
    log_file = os.path.join(LOG_DIR, "app.log")
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

# 教师/管理员页面若尚未实现，则使用占位
try:
    from views.teacher_page import render_teacher_page
except ImportError:

    def render_teacher_page():
        st.info("教师页面开发中，敬请期待。")

try:
    from views.admin_page import render_admin_page
except ImportError:

    def render_admin_page():
        st.info("管理员页面开发中，敬请期待。")


from views.login import render_login_page


def _run_auto_backup_once():
    """会话内仅执行一次：将 database.db 备份到 backups/，失败不阻塞启动。"""
    if st.session_state.get("auto_backup_done"):
        return
    try:
        from utils import auto_backup
        auto_backup()
        st.session_state["auto_backup_done"] = True
    except Exception:
        pass


def _init_session_state():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user_role" not in st.session_state:
        st.session_state.user_role = ""
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    if "user_id" not in st.session_state:
        st.session_state.user_id = 0
    if "username" not in st.session_state:
        st.session_state.username = ""
    if "last_activity_at" not in st.session_state:
        st.session_state.last_activity_at = 0.0
    if "login_fail_records" not in st.session_state:
        st.session_state.login_fail_records = {}


def _inject_watermark():
    """仅登录后显示水印（姓名+工号），全局固定、不阻挡操作；退出登录后不注入。"""
    if not st.session_state.logged_in:
        return
    raw_text = f"{st.session_state.user_name} {st.session_state.username}"
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


def main():
    st.set_page_config(page_title="学术失信人员管理系统", layout="wide")
    try:
        _init_session_state()
        # 启动时自动备份（每会话一次，失败不阻塞）
        _run_auto_backup_once()
        # 水印：仅登录后注入，退出后消失
        _inject_watermark()

        # 侧边栏：管理员为「身份标题 + 四板块导航」+ 当前用户信息 + 登出（先渲染导航，保证 session 中 nav 已就绪）
        with st.sidebar:
            if st.session_state.logged_in:
                if st.session_state.user_role == "admin":
                    from views.admin_page import render_admin_sidebar_nav
                    render_admin_sidebar_nav()  # 唯一数据源 admin_nav_radio，主区据此渲染
                    st.divider()
                role_label = "管理员" if st.session_state.user_role == "admin" else "教师"
                st.caption(f"**当前用户**：{st.session_state.user_name}（{st.session_state.username}）")
                st.caption(f"**角色**：{role_label}")
                st.divider()
                if st.button("退出登录", key="logout_btn"):
                    st.session_state.logged_in = False
                    st.session_state.user_role = ""
                    st.session_state.user_name = ""
                    st.session_state.user_id = 0
                    st.session_state.username = ""
                    st.rerun()
            st.divider()

        if not st.session_state.logged_in:
            render_login_page()
            return

        # 会话超时：先检查距上次活动是否超时，再更新最后活动时间
        now = time.time()
        prev_activity = st.session_state.last_activity_at
        timeout_seconds = SESSION_TIMEOUT_MINUTES * 60
        if prev_activity > 0 and (now - prev_activity) > timeout_seconds:
            st.session_state.logged_in = False
            st.session_state.user_role = ""
            st.session_state.user_name = ""
            st.session_state.user_id = 0
            st.session_state.username = ""
            st.session_state.last_activity_at = 0.0
            st.warning("登录已超时，请重新登录。")
            st.rerun()
        st.session_state.last_activity_at = now

        if st.session_state.user_role == "teacher":
            render_teacher_page()
        elif st.session_state.user_role == "admin":
            render_admin_page()
        else:
            st.warning("未知角色，请联系管理员。")

    except Exception as e:
        logger.exception("应用未捕获异常")
        st.error("系统遇到异常，请稍后重试或联系管理员。")
        with st.expander("技术详情（供管理员排查）", expanded=False):
            st.code(str(e), language="text")
            st.caption("完整堆栈已写入 logs/app.log。")
        st.stop()


if __name__ == "__main__":
    main()
