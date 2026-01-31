"""
主入口：会话初始化、水印、导航、登出
"""
import streamlit as st

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


def _inject_watermark():
    """仅登录后显示水印（姓名+工号），全局固定、不阻挡操作；退出登录后不注入。"""
    if not st.session_state.logged_in:
        return
    text = f"{st.session_state.user_name} {st.session_state.username}"
    # 固定全屏、高层级、半透明、不响应点击，确保登录后全局可见且不消失
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
    _init_session_state()
    # 水印：仅登录后注入，退出后消失
    _inject_watermark()

    # 侧边栏登出（仅登录时显示按钮）
    with st.sidebar:
        if st.session_state.logged_in:
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

    if st.session_state.user_role == "teacher":
        render_teacher_page()
    elif st.session_state.user_role == "admin":
        render_admin_page()
    else:
        st.warning("未知角色，请联系管理员。")


if __name__ == "__main__":
    main()
