"""
教师端名单查询：为教师专门复刻的安全、只读名单查阅检索模块。
屏蔽导出、修改与批量选中功能。
"""
import streamlit as st

from core.database import db_session
from core.config import EMPTY_NO_EFFECTIVE
from views.components import (
    apply_blacklist_sort,
    build_blacklist_query,
    clamp_page,
    render_blacklist_table,
    render_list_controls,
    render_pagination,
    render_record_detail_card,
)


def render_teacher_list_query(db):
    """
    教师端的只读查询视图。包含姓名、学号拼音的高级组合检索，并支持分类细化。
    """
    st.subheader("大名单查询")
    
    # 强制将 cache 前缀独立为 teacher_effective，杜绝与管理员状态交叉
    fn, fs, fm, page_size, sort_key, sort_asc = render_list_controls("teacher_effective")
    
    base = build_blacklist_query(db, status=1, name_filter=fn, sid_filter=fs, major_categories=fm)
    total = base.count()
    if total == 0:
        st.caption(EMPTY_NO_EFFECTIVE)
        return
        
    ordered = apply_blacklist_sort(base, sort_key, sort_asc)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = clamp_page("teacher_effective_page", total_pages)
    page_records = ordered.offset(page * page_size).limit(page_size).all()
    
    st.caption(f"当前检索条件下共有 **{total}** 条有效记录。由于权限控制，此处仅支持查阅名单及公示决定（PDF）。点击某行可查看完整详情。")
    
    # 使用 single-row 选择模式（点击行即选中，无复选框列）
    state_sig = f"{page}_{page_size}_{fn}_{fs}_{','.join(sorted(fm))}_{sort_key}_{sort_asc}"
    sel_key = f"teacher_query_sel_{state_sig}"
    selected = render_blacklist_table(page_records, page_size, page, selection_key=sel_key)
    
    # 分页器（紧跟表格，方便翻页查找）
    render_pagination("teacher_effective_page", page, total_pages, total, len(page_records))
    
    # 选中单条记录时展开详情卡片
    if len(selected) == 1:
        render_record_detail_card(selected[0], key_prefix="teacher_lq_detail")
    
    st.markdown("---")
    st.caption("注：查询过程中不会记录单条浏览痕迹，系统严禁离线导出大规模黑名单人员信息。")
