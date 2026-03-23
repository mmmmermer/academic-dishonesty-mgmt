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
from core.utils import sanitize_for_export


def _like_escape(s: str) -> str:
    """对 LIKE 模式中的 % _ 进行转义，避免用户输入导致匹配过宽。"""
    if not s:
        return s
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_blacklist_query(db, status: int, name_filter: str = "", sid_filter: str = "", major_filter: str = ""):
    """构建名单基础查询：支持按空格分隔的多目标检索。"""
    from sqlalchemy import func, or_
    q = db.query(Blacklist).filter(Blacklist.status == status)
    
    if name_filter:
        names = [n.strip() for n in name_filter.split() if n.strip()]
        if len(names) > 1:
            q = q.filter(or_(*[func.replace(Blacklist.name, ' ', '').like(f"%{_like_escape(n)}%", escape="\\") for n in names]))
        else:
            name_clean = "".join(name_filter.split())
            q = q.filter(func.replace(Blacklist.name, ' ', '').like(f"%{_like_escape(name_clean)}%", escape="\\"))
            
    if sid_filter:
        sids = [s.strip() for s in sid_filter.split() if s.strip()]
        if len(sids) > 1:
            q = q.filter(or_(*[Blacklist.student_id.like(f"%{_like_escape(s)}%", escape="\\") for s in sids]))
        else:
            q = q.filter(Blacklist.student_id.like(f"%{_like_escape(sid_filter)}%", escape="\\"))
            
    if major_filter:
        q = q.filter(Blacklist.major.like(f"%{_like_escape(major_filter)}%", escape="\\"))
    return q


BLACKLIST_SORT_ATTR_MAP = {"姓名": "name", "工号/学号": "student_id", "所在单位": "major", "认定日期": "punishment_date"}
BLACKLIST_SORT_COLUMNS = list(BLACKLIST_SORT_ATTR_MAP.keys())


def apply_blacklist_sort(query, sort_key: str, sort_asc: bool):
    """对名单查询施加排序。"""
    col = getattr(Blacklist, BLACKLIST_SORT_ATTR_MAP.get(sort_key, "student_id"))
    return query.order_by(col.asc() if sort_asc else col.desc())


def render_list_controls(key_prefix: str, sort_columns=None, page_size_options=None):
    """渲染列表的主控栏：单行紧凑布局合并筛选框与 Popover 设置面板。"""
    if sort_columns is None:
        sort_columns = BLACKLIST_SORT_COLUMNS
    if page_size_options is None:
        page_size_options = LIST_PAGE_SIZE_OPTIONS

    ps_key = f"{key_prefix}_page_size"
    sort_key_k = f"{key_prefix}_sort_col"
    order_key = f"{key_prefix}_sort_order"

    page_size = st.session_state.get(ps_key, LIST_PAGE_SIZE)
    if page_size not in page_size_options:
        page_size = LIST_PAGE_SIZE
    if sort_key_k not in st.session_state:
        st.session_state[sort_key_k] = "工号/学号"
    if order_key not in st.session_state:
        st.session_state[order_key] = SORT_ORDER_ASC

    c1, c2, c3, c4 = st.columns([3, 3, 3, 2.5])
    with c1:
        fn = st.text_input("姓名筛选", key=f"{key_prefix}_fn", placeholder=PLACEHOLDER_FILTER_EMPTY)
    with c2:
        fs = st.text_input("学号/工号筛选", key=f"{key_prefix}_fs", placeholder=PLACEHOLDER_FILTER_EMPTY)
    with c3:
        fm = st.text_input("专业筛选", key=f"{key_prefix}_fm", placeholder=PLACEHOLDER_FILTER_EMPTY)
    with c4:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        with st.popover("⚙️ 列表设置", use_container_width=True):
            idx = page_size_options.index(page_size) if page_size in page_size_options else 0
            page_size = st.selectbox(LABEL_PAGE_SIZE, page_size_options, index=idx, key=ps_key)
            sort_key = st.selectbox(LABEL_SORT_COLUMN, sort_columns, key=sort_key_k)
            sort_order = st.radio(LABEL_SORT_ORDER, SORT_ORDER_OPTIONS, key=order_key, horizontal=True)

    sort_asc = sort_order == SORT_ORDER_ASC
    if sort_key not in sort_columns:
        sort_key = "工号/学号"
        
    return (fn or "").strip(), (fs or "").strip(), (fm or "").strip(), page_size, sort_key, sort_asc


def render_blacklist_table(records, page_size: int, current_page: int, selection_key: str = None) -> list:
    """渲染名单表格，支持链接下载与动态时效展示。开启 selection_key 时将带多选框并返回选中的对象。"""
    start = current_page * page_size
    today = datetime.now().date()
    df_data = []
    
    for i, r in enumerate(records, 1):
        in_impact = False
        if r.impact_start_date and r.impact_end_date:
            in_impact = r.impact_start_date <= today <= r.impact_end_date
        elif r.impact_start_date:
            in_impact = r.impact_start_date <= today
        elif r.impact_end_date:
            in_impact = today <= r.impact_end_date

        df_data.append({
            "序号": start + i,
            "姓名": r.name,
            "工号/学号": r.student_id,
            "所在单位": r.major or "",
            "认定结论": r.reason if (r.reason and str(r.reason).startswith("/app/static/")) else "",
            "认定日期": str(r.punishment_date) if r.punishment_date else "",
            "处理起至时间": f"{r.impact_start_date} 至 {r.impact_end_date}" if r.impact_start_date and r.impact_end_date else (str(r.impact_start_date) if r.impact_start_date else (str(r.impact_end_date) if r.impact_end_date else "")),
            "影响期": "✅ 是" if in_impact else "❌ 否",
        })
        
    df = pd.DataFrame(df_data)
    
    # 方案 A：精准计算动态高度，消除底部空出的半行间隙
    # 严格吸附高度：行高固定 35px，表头 38px，底部边线 1px
    computed_height = (max(1, len(df)) * 35) + 39
    
    kwargs = {}
    if selection_key:
        kwargs["on_select"] = "rerun"
        kwargs["selection_mode"] = "multi-row"
        kwargs["key"] = selection_key
        
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=computed_height,
        column_config={
            "认定结论": st.column_config.LinkColumn(
                "认定结论",
                display_text="📥 下载公示文件",
                help="点击下载/预览官方 PDF 报告"
            )
        },
        **kwargs
    )
    st.caption("注：表格内『认定结论』为空白代表该项暂未上传 PDF 格式的公示文件。")
    
    if selection_key and hasattr(event, "selection") and hasattr(event.selection, "rows"):
        selected_indices = event.selection.rows
        # 严控数组越界，丢弃所有非法的“跨时空/空心缩水”产生的幽灵勾选
        return [records[i] for i in selected_indices if i < len(records)]
    return []


def render_pagination(page_key: str, current_page: int, total_pages: int, total_items: int, page_count: int):
    """渲染分页控件：居中三栏布局，左右翻页+中间页码信息，末尾内联跳转。"""
    # 主翻页行：上一页 | 页码 & 跳转 | 下一页
    c_prev, c_mid, c_next = st.columns([1, 3, 1])
    with c_prev:
        if st.button("◀ 上一页", key=f"{page_key}_prev", disabled=(current_page <= 0), use_container_width=True):
            st.session_state[page_key] = current_page - 1
            st.rerun()
    with c_mid:
        # 页码信息 + 内联跳转输入框 + 跳转按钮
        jc1, jc2, jc3 = st.columns([2, 1.2, 0.8])
        with jc1:
            st.markdown(
                f"<div style='text-align:center;padding-top:8px;font-size:13px;opacity:.8'>"
                f"第 <b>{current_page + 1}</b> / {total_pages} 页 &nbsp;·&nbsp; 共 {total_items} 条</div>",
                unsafe_allow_html=True
            )
        with jc2:
            jump = st.number_input(
                "跳至", min_value=1, max_value=max(1, total_pages),
                value=current_page + 1, key=f"{page_key}_jump", label_visibility="collapsed",
            )
        with jc3:
            if st.button("Go", key=f"{page_key}_go", use_container_width=True) and 1 <= jump <= total_pages:
                st.session_state[page_key] = int(jump) - 1
                st.rerun()
    with c_next:
        if st.button("下一页 ▶", key=f"{page_key}_next", disabled=(current_page >= total_pages - 1), use_container_width=True):
            st.session_state[page_key] = current_page + 1
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
    """渲染名单导出 Excel 按钮（分批查询，惰性生成缓存，避免多选刷新时造成灾难性卡顿）。"""
    if total == 0:
        return
        
    # 定义当前查询状态的唯一签名
    current_hash = f"{status}_{fn}_{fs}_{fm}_{sort_key}_{sort_asc}"
    cache_hash_key = f"{button_key}_hash"
    cache_data_key = f"{button_key}_data"
    
    # 若缓存失效或条件变化，展现渲染生成按钮，阻断底层耗时运算
    if st.session_state.get(cache_hash_key) != current_hash or st.session_state.get(cache_data_key) is None:
        if st.button(f"⚡ 准备打包下载所有筛选记录（共 {total} 条）", use_container_width=True, key=f"{button_key}_prep"):
            with st.spinner(SPINNER_EXPORT):
                base = build_blacklist_query(db, status, fn, fs, fm)
                ordered = apply_blacklist_sort(base, sort_key, sort_asc)
                export_rows = fetch_export_rows(ordered)
                
                if len(export_rows) >= EXPORT_MAX_ROWS:
                    st.caption(f"筛选结果超过 {EXPORT_MAX_ROWS} 条，仅导出前 {EXPORT_MAX_ROWS} 条。")
                    
                export_df = pd.DataFrame([
                    {
                        "序号": i,
                        "姓名": sanitize_for_export(r.name),
                        "工号/学号": r.student_id,
                        "所在单位": sanitize_for_export(r.major or ""),
                        "认定结论(文件路径)": sanitize_for_export(r.reason or ""),
                        "认定日期": str(r.punishment_date) if r.punishment_date else "",
                        "处理起至时间": f"{r.impact_start_date} 至 {r.impact_end_date}" if r.impact_start_date and r.impact_end_date else (str(r.impact_start_date) if r.impact_start_date else (str(r.impact_end_date) if r.impact_end_date else "")),
                    }
                    for i, r in enumerate(export_rows, 1)
                ])
                buf = BytesIO()
                export_df.to_excel(buf, index=False, engine="openpyxl")
                
                # 持久化到会话内存
                st.session_state[cache_hash_key] = current_hash
                st.session_state[cache_data_key] = buf.getvalue()
                st.rerun()
    else:
        # 完全命中内存态，0 CPU / 0 IO 渲染真理下载按钮
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label=f"⬇️ 导出当前筛选的{filename_prefix} (Excel)",
            data=st.session_state[cache_data_key],
            file_name=f"{filename_prefix}_{stamp}.xlsx",
            mime=MIME_XLSX,
            key=button_key,
            use_container_width=True
        )


def clamp_page(page_key: str, total_pages: int) -> int:
    """从 session_state 读取当前页并限制在有效范围内。"""
    page = st.session_state.get(page_key, 0)
    page = max(0, min(page, total_pages - 1))
    st.session_state[page_key] = page
    return page
