"""
教师端主入口页面
作为路由器负责分发各个功能板块的交互，已剥离所有硬编码 UI 至子组件。
"""
import streamlit as st

from core.database import db_session
from core.config import (
    CAPTION_TEACHER,
    LABEL_SELECT_FEATURE,
    TITLE_TEACHER,
)
from views.teacher import (
    render_single_search,
    render_batch_check,
    render_my_logs,
    render_teacher_list_query
)

# 教师端侧边栏导航选项（新增 名单查询）
TEACHER_NAV_OPTIONS = ["› 单条查询", "› 批量比对", "› 名单查询", "› 个人记录"]
TEACHER_NAV_SINGLE, TEACHER_NAV_BATCH, TEACHER_NAV_LIST_QUERY, TEACHER_NAV_LOG = 0, 1, 2, 3
TEACHER_NAV_KEY = "teacher_nav_radio"


def _get_teacher_nav_index():
    """从 session_state 读取当前选中的板块索引。"""
    val = st.session_state.get(TEACHER_NAV_KEY, TEACHER_NAV_OPTIONS[TEACHER_NAV_SINGLE])
    if val in TEACHER_NAV_OPTIONS:
        return TEACHER_NAV_OPTIONS.index(val)
    return TEACHER_NAV_SINGLE


def _on_teacher_nav_change():
    """导航回调（保留空函数以满足 on_change 绑定）。"""
    pass


def render_teacher_sidebar_nav():
    """在侧边栏渲染身份标题与四个功能板块导航（由 app 在 with st.sidebar 内调用）。"""
    if TEACHER_NAV_KEY not in st.session_state:
        st.session_state[TEACHER_NAV_KEY] = TEACHER_NAV_OPTIONS[TEACHER_NAV_SINGLE]
    st.markdown("### 教师")
    st.caption(LABEL_SELECT_FEATURE)
    st.radio(
        "功能",
        options=TEACHER_NAV_OPTIONS,
        key=TEACHER_NAV_KEY,
        label_visibility="collapsed",
        on_change=_on_teacher_nav_change
    )


def render_teacher_page():
    """教师页：根据侧边栏选中项路由到真实的视图处理模块。"""
    st.title(TITLE_TEACHER)
    st.caption(CAPTION_TEACHER)

    nav_index = _get_teacher_nav_index()

    if nav_index == TEACHER_NAV_SINGLE:
        render_single_search()
    elif nav_index == TEACHER_NAV_BATCH:
        render_batch_check()
    elif nav_index == TEACHER_NAV_LIST_QUERY:
        with db_session() as db:
            render_teacher_list_query(db)
    else:
        render_my_logs()
