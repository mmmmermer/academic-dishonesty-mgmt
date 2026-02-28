"""
通用 UI 组件：分页、筛选、排序、导出等可复用的 Streamlit 渲染函数。
供 admin_page / teacher_page 等视图共享，消除重复代码。
"""
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from core.config import (
    LABEL_DISPLAY_OPTIONS_EXPANDER,
    LABEL_PAGE_SIZE,
    LABEL_SORT_COLUMN,
    LABEL_SORT_ORDER,
    LIST_PAGE_SIZE,
    LIST_PAGE_SIZE_OPTIONS,
    MIME_XLSX,
    PLACEHOLDER_FILTER_EMPTY,
    SORT_ORDER_ASC,
    SORT_ORDER_OPTIONS,
)
from core.models import Blacklist


def _like_escape(s: str) -> str:
    """对 LIKE 模式中的 % _ 进行转义，避免用户输入导致匹配过宽。"""
    if not s:
        return s
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_blacklist_query(db, status: int, name_filter: str = "", sid_filter: str = "", major_filter: str = ""):
    """构建名单基础查询：按 status 筛选，可选姓名/学号/专业 LIKE（已转义 %/_）。"""
    q = db.query(Blacklist).filter(Blacklist.status == status)
    if name_filter:
        q = q.filter(Blacklist.name.like(f"%{_like_escape(name_filter)}%", escape="\\"))
    if sid_filter:
        q = q.filter(Blacklist.student_id.like(f"%{_like_escape(sid_filter)}%", escape="\\"))
    if major_filter:
        q = q.filter(Blacklist.major.like(f"%{_like_escape(major_filter)}%", escape="\\"))
    return q


BLACKLIST_SORT_ATTR_MAP = {"姓名": "name", "学号": "student_id", "专业": "major", "处分日期": "punishment_date"}
BLACKLIST_SORT_COLUMNS = list(BLACKLIST_SORT_ATTR_MAP.keys())


def apply_blacklist_sort(query, sort_key: str, sort_asc: bool):
    """对名单查询施加排序。"""
    col = getattr(Blacklist, BLACKLIST_SORT_ATTR_MAP.get(sort_key, "student_id"))
    return query.order_by(col.asc() if sort_asc else col.desc())


def render_filter_inputs(key_prefix: str):
    """渲染三列筛选输入框（姓名/学号/专业），返回 (name, sid, major) stripped。"""
    c1, c2, c3 = st.columns(3)
    with c1:
        fn = st.text_input("姓名筛选", key=f"{key_prefix}_filter_name", placeholder=PLACEHOLDER_FILTER_EMPTY)
    with c2:
        fs = st.text_input("学号筛选", key=f"{key_prefix}_filter_sid", placeholder=PLACEHOLDER_FILTER_EMPTY)
    with c3:
        fm = st.text_input("专业筛选", key=f"{key_prefix}_filter_major", placeholder=PLACEHOLDER_FILTER_EMPTY)
    return (fn or "").strip(), (fs or "").strip(), (fm or "").strip()


def render_display_options(key_prefix: str, sort_columns=None, page_size_options=None):
    """渲染「显示与排序」展开区，返回 (page_size, sort_key, sort_asc)。"""
    if sort_columns is None:
        sort_columns = BLACKLIST_SORT_COLUMNS
    if page_size_options is None:
        page_size_options = LIST_PAGE_SIZE_OPTIONS

    ps_key = f"{key_prefix}_page_size_select"
    sort_key_k = f"{key_prefix}_sort_select"
    order_key = f"{key_prefix}_order_select"

    page_size = st.session_state.get(ps_key, LIST_PAGE_SIZE)
    if page_size not in page_size_options:
        page_size = LIST_PAGE_SIZE
    if sort_key_k not in st.session_state:
        st.session_state[sort_key_k] = "学号"
    if order_key not in st.session_state:
        st.session_state[order_key] = SORT_ORDER_ASC

    with st.expander(LABEL_DISPLAY_OPTIONS_EXPANDER, expanded=False):
        col_ps, col_sort, col_order = st.columns([1, 1, 1])
        with col_ps:
            idx = page_size_options.index(page_size) if page_size in page_size_options else 0
            page_size = st.selectbox(LABEL_PAGE_SIZE, page_size_options, index=idx, key=ps_key)
        with col_sort:
            sort_key = st.selectbox(LABEL_SORT_COLUMN, sort_columns, key=sort_key_k)
        with col_order:
            sort_order = st.selectbox(LABEL_SORT_ORDER, SORT_ORDER_OPTIONS, key=order_key)

    sort_asc = sort_order == SORT_ORDER_ASC
    if sort_key not in sort_columns:
        sort_key = "学号"
    return page_size, sort_key, sort_asc


def render_blacklist_table(records, page_size: int, current_page: int):
    """渲染名单表格（通用列：序号/姓名/学号/专业/原因/处分日期）。"""
    start = current_page * page_size
    df = pd.DataFrame(
        [
            {
                "序号": start + i,
                "姓名": r.name,
                "学号": r.student_id,
                "专业": r.major or "",
                "原因": (r.reason or "")[:50],
                "处分日期": str(r.punishment_date) if r.punishment_date else "",
            }
            for i, r in enumerate(records, 1)
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_pagination(page_key: str, current_page: int, total_pages: int, total_items: int, page_count: int):
    """渲染分页控件：上一页/下一页/跳转/信息。"""
    c1, c2, c3, c4, c5 = st.columns([2.2, 0.9, 0.9, 1.2, 0.8])
    with c1:
        st.caption(f"第 {current_page + 1}/{total_pages} 页 · 本页 {page_count} 条 · 共 {total_items} 条")
    with c2:
        if st.button("上一页", key=f"{page_key}_prev", disabled=(current_page <= 0)):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with c3:
        if st.button("下一页", key=f"{page_key}_next", disabled=(current_page >= total_pages - 1)):
            st.session_state[page_key] = current_page + 1
            st.rerun()
    with c4:
        jump = st.number_input(
            "跳至第 … 页", min_value=1, max_value=max(1, total_pages),
            value=current_page + 1, key=f"{page_key}_jump", label_visibility="collapsed",
        )
    with c5:
        if st.button("跳转", key=f"{page_key}_go") and 1 <= jump <= total_pages:
            st.session_state[page_key] = int(jump) - 1
            st.rerun()


def render_simple_pagination(page_key: str, current_page: int, total_pages: int, page_count: int):
    """渲染简单分页控件（上一页/信息/下一页，无跳转），用于教师端等场景。"""
    c_prev, c_info, c_next = st.columns([1, 2, 1])
    with c_prev:
        if current_page > 0:
            if st.button("上一页", key=f"{page_key}_prev"):
                st.session_state[page_key] = current_page - 1
                st.rerun()
        else:
            st.button("上一页", key=f"{page_key}_prev", disabled=True)
    with c_info:
        st.caption(f"第 {current_page + 1} 页 / 共 {total_pages} 页，本页 {page_count} 条")
    with c_next:
        if current_page < total_pages - 1:
            if st.button("下一页", key=f"{page_key}_next"):
                st.session_state[page_key] = current_page + 1
                st.rerun()
        else:
            st.button("下一页", key=f"{page_key}_next", disabled=True)


EXPORT_BATCH_SIZE = 2000
EXPORT_MAX_ROWS = 50000
SPINNER_EXPORT = "准备导出中…"


def fetch_export_rows(query, max_rows: int = EXPORT_MAX_ROWS, batch_size: int = EXPORT_BATCH_SIZE):
    """按排序查询分批拉取记录，最多 max_rows 条。"""
    rows = []
    for offset in range(0, max_rows, batch_size):
        batch = query.offset(offset).limit(batch_size).all()
        if not batch:
            break
        rows.extend(batch)
    return rows


def render_blacklist_export_button(db, status: int, fn: str, fs: str, fm: str,
                                   sort_key: str, sort_asc: bool, total: int,
                                   filename_prefix: str, button_key: str):
    """渲染名单导出 Excel 按钮（分批查询，最多 50000 条）。"""
    if total == 0:
        return
    with st.spinner(SPINNER_EXPORT):
        base = build_blacklist_query(db, status, fn, fs, fm)
        ordered = apply_blacklist_sort(base, sort_key, sort_asc)
        export_rows = fetch_export_rows(ordered)
    if not export_rows:
        return
    if len(export_rows) >= EXPORT_MAX_ROWS:
        st.caption(f"筛选结果超过 {EXPORT_MAX_ROWS} 条，仅导出前 {EXPORT_MAX_ROWS} 条。")
    export_df = pd.DataFrame(
        [
            {
                "序号": i,
                "姓名": r.name,
                "学号": r.student_id,
                "专业": r.major or "",
                "原因": r.reason or "",
                "处分日期": str(r.punishment_date) if r.punishment_date else "",
            }
            for i, r in enumerate(export_rows, 1)
        ]
    )
    buf = BytesIO()
    export_df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        label=f"导出当前筛选的{filename_prefix} (Excel)",
        data=buf.getvalue(),
        file_name=f"{filename_prefix}_{stamp}.xlsx",
        mime=MIME_XLSX,
        key=button_key,
    )


def clamp_page(page_key: str, total_pages: int) -> int:
    """从 session_state 读取当前页并限制在有效范围内。"""
    page = st.session_state.get(page_key, 0)
    page = max(0, min(page, total_pages - 1))
    st.session_state[page_key] = page
    return page
