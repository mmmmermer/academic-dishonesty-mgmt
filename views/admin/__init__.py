"""
管理员页面子模块：仪表盘、名单管理、系统维护、用户管理。
"""
from views.admin.dashboard import _dashboard_fragment
from views.admin.management import _render_management
from views.admin.system import _render_system
from views.admin.user_mgmt import _render_user_management

import streamlit as st
from core.config import LABEL_SELECT_PANEL
from core.database import db_session

ADMIN_NAV_OPTIONS = ["› 仪表盘", "› 名单管理", "› 系统维护", "› 用户管理"]
NAV_DASHBOARD, NAV_MANAGEMENT, NAV_SYSTEM, NAV_USER = 0, 1, 2, 3
ADMIN_NAV_KEY = "admin_nav_radio"


def _get_admin_nav_index():
    """从 session_state 读取当前选中的板块索引（唯一数据源）。"""
    val = st.session_state.get(ADMIN_NAV_KEY, ADMIN_NAV_OPTIONS[NAV_DASHBOARD])
    if val in ADMIN_NAV_OPTIONS:
        return ADMIN_NAV_OPTIONS.index(val)
    return NAV_DASHBOARD


def render_admin_sidebar_nav():
    """在侧边栏渲染身份标题与四个功能板块导航（由 app 在 with st.sidebar 内调用）。"""
    if ADMIN_NAV_KEY not in st.session_state:
        st.session_state[ADMIN_NAV_KEY] = ADMIN_NAV_OPTIONS[NAV_DASHBOARD]
    st.markdown("### 管理员")
    st.caption(LABEL_SELECT_PANEL)
    st.radio(
        "功能",
        options=ADMIN_NAV_OPTIONS,
        key=ADMIN_NAV_KEY,
        label_visibility="collapsed",
    )


def render_admin_page():
    """管理员页主入口：根据侧边栏选中项渲染对应内容。"""
    nav_index = _get_admin_nav_index()

    with db_session() as db:
        if nav_index == NAV_DASHBOARD:
            _dashboard_fragment()
        elif nav_index == NAV_MANAGEMENT:
            _render_management(db)
        elif nav_index == NAV_SYSTEM:
            _render_system(db)
        else:
            _render_user_management(db)
