from __future__ import annotations

"""
通用 UI 组件：分页、筛选、排序、导出等可复用的 Streamlit 渲染函数。
供 admin_page / teacher_page 等视图共享，消除重复代码。
"""
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from core.config import (
    ALL_CATEGORIZED_UNITS,
    LABEL_DISPLAY_OPTIONS_EXPANDER,
    LABEL_PAGE_SIZE,
    LABEL_SORT_COLUMN,
    LABEL_SORT_ORDER,
    LABEL_UNCATEGORIZED,
    LIST_PAGE_SIZE,
    LIST_PAGE_SIZE_OPTIONS,
    MIME_XLSX,
    PLACEHOLDER_FILTER_EMPTY,
    SORT_ORDER_ASC,
    SORT_ORDER_OPTIONS,
    UNIT_CATEGORY_MAP,
    UNIT_FILTER_OPTIONS,
)
from core.models import Blacklist
from core.search_config import PYTHON_NAME_SCAN_YIELD_BATCH
from core.search import (
    build_name_terms_sql_filter,
    build_chinese_name_sql_conditions,
    filter_record_ids_by_name_terms,
    has_search_helper_columns,
    should_use_python_name_scan,
    split_search_terms,
    split_student_id_terms,
)
from core.utils import sanitize_for_export


def _like_escape(s: str) -> str:
    """对 LIKE 模式中的 % _ 进行转义，避免用户输入导致匹配过宽。"""
    if not s:
        return s
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _expand_unit_categories(categories: list[str]) -> tuple[list[str], bool]:
    """将用户选中的分类选项展开为具体院系名列表，并标记是否包含'未归类'。"""
    units: list[str] = []
    has_uncategorized = False
    for item in categories:
        if item == LABEL_UNCATEGORIZED:
            has_uncategorized = True
        elif item.startswith("【全选】"):
            cat_name = item.replace("【全选】", "")
            units.extend(UNIT_CATEGORY_MAP.get(cat_name, []))
        else:
            units.append(item)
    return units, has_uncategorized


def _unit_like_keyword(unit_name: str) -> str:
    """从院系全名中提取模糊匹配关键字，兼容历史数据。

    例: '计算机科学与技术学院' -> '计算机科学与技术'
    """
    for suffix in ("学院", "学系", "医院", "研究所", "研究院"):
        if unit_name.endswith(suffix) and len(unit_name) > len(suffix):
            return unit_name[: -len(suffix)]
    return unit_name


def build_blacklist_query(db, status: int, name_filter: str = "", sid_filter: str = "",
                         major_categories: list[str] | None = None):
    """构建名单基础查询：支持多目标检索 + 分类多选筛选单位。"""
    from sqlalchemy import and_, or_
    q = db.query(Blacklist).filter(Blacklist.status == status)
    helper_columns = has_search_helper_columns(db.get_bind())

    if sid_filter:
        sids = split_student_id_terms(sid_filter)
        if not sids:
            return q.filter(Blacklist.id == -1)
        if len(sids) > 1:
            q = q.filter(or_(*[Blacklist.student_id.like(f"%{_like_escape(s)}%", escape="\\") for s in sids]))
        else:
            q = q.filter(Blacklist.student_id.like(f"%{_like_escape(sids[0])}%", escape="\\"))

    if name_filter:
        name_terms = split_search_terms(name_filter)
        if name_terms:
            if helper_columns:
                sql_filter, sql_params = build_name_terms_sql_filter(
                    name_terms,
                    include_helper_columns=True,
                    prefix_min_len=1,
                )
                if sql_filter is None:
                    q = q.filter(Blacklist.id == -1)
                else:
                    q = q.filter(sql_filter).params(**sql_params)
            elif should_use_python_name_scan(name_terms):
                matched_ids = filter_record_ids_by_name_terms(
                    q.yield_per(PYTHON_NAME_SCAN_YIELD_BATCH),
                    name_terms,
                    prefix_min_len=1,
                )
                if not matched_ids:
                    q = q.filter(Blacklist.id == -1)
                else:
                    q = q.filter(Blacklist.id.in_(matched_ids))
            else:
                name_conditions = build_chinese_name_sql_conditions(name_terms)
                if name_conditions is not None:
                    q = q.filter(name_conditions)

    if major_categories:
        units, has_uncategorized = _expand_unit_categories(major_categories)
        conditions = []
        # 精确匹配 + 模糊兼容历史数据（去学院/医院等后缀做 LIKE）
        if units:
            like_conds = []
            for u in units:
                kw = _unit_like_keyword(u)
                like_conds.append(Blacklist.major.like(f"%{_like_escape(kw)}%", escape="\\"))
            conditions.append(or_(*like_conds))
        # 未归类/异常值：major 不含任何已知院系关键字，或为空
        if has_uncategorized:
            all_kw_conds = []
            for known in ALL_CATEGORIZED_UNITS:
                kw = _unit_like_keyword(known)
                all_kw_conds.append(Blacklist.major.like(f"%{_like_escape(kw)}%", escape="\\"))
            conditions.append(or_(
                Blacklist.major.is_(None),
                Blacklist.major == "",
                and_(*[~c for c in all_kw_conds]),
            ))
        if conditions:
            q = q.filter(or_(*conditions))
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

    # 注入 CSS 魔术：抹除弹窗内 Expander 边框，收紧间距，强制拉宽面板以防折行
    st.markdown("""
        <style>
        /* 强制拉宽悬浮面板，让它在小按钮的基础向右侧展开，释放内部文字空间 */
        [data-testid="stPopoverBody"] {
            min-width: 480px !important;
        }
        /* 彻底抹除折叠面板的一切残余边框（兼容不同 Streamlit 版本的 details 标签） */
        [data-testid="stPopoverBody"] details,
        [data-testid="stPopoverBody"] [data-testid="stExpander"] {
            border: none !important;
            box-shadow: none !important;
            background-color: transparent !important;
        }
        [data-testid="stPopoverBody"] details summary,
        [data-testid="stPopoverBody"] [data-testid="stExpander"] summary {
            background-color: transparent !important;
            padding-left: 0 !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        /* 缩小字体大小，收紧全部行高 */
        [data-testid="stPopoverBody"] p,
        [data-testid="stPopoverBody"] div.stMarkdown,
        [data-testid="stPopoverBody"] details summary p,
        [data-testid="stPopoverBody"] [data-testid="stExpander"] summary p {
            font-size: 14px !important;
        }
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] {
            gap: 0.2rem !important; /* 原生默认是1rem，这里大幅减小以拉窄行距 */
        }
        [data-testid="stPopoverBody"] label[data-baseweb="checkbox"] {
            margin-bottom: -4px !important; 
        }
        /* 单位筛选 Popover 内：滚动容器为 VerticalBlock > VerticalBlock > HorizontalBlock，仅在此固定三列（子 div 用 nth-child 兼容不同 testid） */
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] > div:nth-child(1) {
            flex: 0 0 2.5rem !important;
            min-width: 2.5rem !important;
            max-width: 2.5rem !important;
            display: flex !important;
            align-items: center !important;
            justify-content: flex-start !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
        }
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] > div:nth-child(1) [data-testid="stCheckbox"] {
            margin-left: 0 !important;
        }
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] > div:nth-child(2) {
            flex: 0 0 6rem !important;
            min-width: 6rem !important;
            max-width: 6rem !important;
            display: flex !important;
            align-items: center !important;
            justify-content: flex-start !important;
            padding-left: 0 !important;
        }
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] > div:nth-child(2) div.stMarkdown {
            width: 100% !important;
        }
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] [data-testid="stVerticalBlock"] [data-testid="stHorizontalBlock"] > div:nth-child(3) {
            flex: 1 1 auto !important;
            min-width: 0 !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # 基础筛选与设置（第一行）
    c1, c2, c_settings = st.columns([4, 4, 3])
    with c1:
        fn = st.text_input(
            "姓名筛选",
            key=f"{key_prefix}_fn",
            placeholder="支持中文、拼音、首字母；多项请用换行、逗号或分号分隔",
        )
    with c2:
        fs = st.text_input("学号/工号筛选", key=f"{key_prefix}_fs", placeholder=PLACEHOLDER_FILTER_EMPTY)
    with c_settings:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        with st.popover("⚙️ 列表设置", width="stretch"):
            idx = page_size_options.index(page_size) if page_size in page_size_options else 0
            page_size = st.selectbox(LABEL_PAGE_SIZE, page_size_options, index=idx, key=ps_key)
            sort_key = st.selectbox(LABEL_SORT_COLUMN, sort_columns, key=sort_key_k)
            sort_order = st.radio(LABEL_SORT_ORDER, SORT_ORDER_OPTIONS, key=order_key, horizontal=True)

    # 高级单位筛选（第二行，拉长外层选择框，合适尺度）
    c_unit, _ = st.columns([5.5, 5.5])
    with c_unit:
        # 从 Session State 中提取已勾选的数量用于动态折叠按钮标题展示
        selected_count = 0
        if st.session_state.get(f"{key_prefix}_chk_u_uncat"):
            selected_count += 1
        for cat, units in UNIT_CATEGORY_MAP.items():
            if st.session_state.get(f"{key_prefix}_chk_cat_{cat}"):
                selected_count += 1
            else:
                for u in units:
                    if st.session_state.get(f"{key_prefix}_chk_u_{u}"):
                        selected_count += 1
                        
        btn_label = "🏛️ 选择分类或具体院系展开面板 ▾" if selected_count == 0 else f"🏛️ 已勾选 {selected_count} 项分类或院系，点击继续修改 ▾"
        fm = []
        
        st.markdown("<div style='font-size:14px;margin-bottom:6px;opacity:0.8'>单位精准筛选 (点击下方展开面板)</div>", unsafe_allow_html=True)
        with st.popover(btn_label, width="stretch"):
            with st.container(height=320, border=False):
                # 优先渲染常规类别
                for cat, units in UNIT_CATEGORY_MAP.items():
                    # 单行三列：不得在外层列内再嵌套 st.columns（Streamlit 仅允许一层列嵌套）；
                    # 比例与上方 CSS 中固定列宽配合，使复选框与「工科一/理科」等列对齐。
                    c_box, c_lbl, c_exp = st.columns(
                        [0.55, 2.25, 7.2], gap="small", vertical_alignment="center"
                    )

                    with c_box:
                        is_all = st.checkbox(
                            f"Select all {cat}",
                            key=f"{key_prefix}_chk_cat_{cat}",
                            label_visibility="collapsed",
                            help=f"全选「{cat}」下所有院系",
                        )
                    with c_lbl:
                        st.markdown(
                            f"<div style='width:100%;font-weight:600;line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{cat}</div>",
                            unsafe_allow_html=True,
                        )
                    if is_all:
                        fm.append(f"【全选】{cat}")

                    with c_exp:
                        with st.expander(f"📁 展开细选 ({len(units)}个院系)"):
                            # 既然右侧面板已分割变窄，内部恢复为单列排列防止院系名称过长被挤折行
                            for u in units:
                                if is_all:
                                    # 父类选中时禁用并强制勾选，添加带此前缀的dummy_key防重复报错
                                    st.checkbox(u, value=True, disabled=True, key=f"dummy_{key_prefix}_chk_u_{u}")
                                else:
                                    if st.checkbox(u, key=f"{key_prefix}_chk_u_{u}"):
                                        fm.append(u)
                                        
                # 异常数据兜底置于最宽面板末端
                st.markdown("---")
                if st.checkbox(LABEL_UNCATEGORIZED, key=f"{key_prefix}_chk_u_uncat"):
                    fm.append(LABEL_UNCATEGORIZED)

    sort_asc = sort_order == SORT_ORDER_ASC
    if sort_key not in sort_columns:
        sort_key = "工号/学号"
        
    return (fn or "").strip(), (fs or "").strip(), fm or [], page_size, sort_key, sort_asc


def render_blacklist_table(records, page_size: int, current_page: int, selection_key: str | None = None) -> list:
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

        _reason = str(r.reason) if r.reason else ""
        _is_pdf = _reason.lower().endswith(".pdf")
        df_data.append({
            "序号": start + i,
            "姓名": r.name,
            "工号/学号": r.student_id,
            "所在单位": r.major or "",
            "认定结论": _reason if _is_pdf else "",   # PDF 路径，供 LinkColumn 渲染
            "处理原因": r.reason_text or "",  # 纯文字原因，从此独立字段读取
            "认定日期": str(r.punishment_date) if r.punishment_date else "",
            "处理起至时间": f"{r.impact_start_date} 至 {r.impact_end_date}" if r.impact_start_date and r.impact_end_date else (str(r.impact_start_date) if r.impact_start_date else (str(r.impact_end_date) if r.impact_end_date else "")),
            "影响期": "✅ 是" if in_impact else "❌ 否",
        })
        
    df = pd.DataFrame(df_data)
    
    # 方案 A：精准计算动态高度，消除底部空出的半行间隙
    # 严格吸附高度：行高固定 35px，表头 38px，底部边线 1px
    computed_height = (max(1, len(df)) * 35) + 39
    
    kwargs = {}
    if selection_key is not None:
        kwargs["on_select"] = "rerun"
        kwargs["selection_mode"] = "multi-row"
        kwargs["key"] = selection_key
        
    event = st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        height=computed_height,
        column_config={
            "认定结论": st.column_config.LinkColumn(
                "认定结论",
                display_text="📊 PDF 公示文件",
                help="点击下载/预览官方 PDF 报告"
            ),
            "处理原因": st.column_config.TextColumn(
                "处理原因",
                help="Excel 导入或手动填写的原因文字；已上传 PDF 的记录此列为空"
            ),
        },
        **kwargs
    )
    st.caption("注：『认定结论』列有链接代表已上传 PDF 公示文件；『处理原因』列显示文字说明（未上传 PDF 时）。")
    
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
        if st.button("◀ 上一页", key=f"{page_key}_prev", disabled=(current_page <= 0), width="stretch"):
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
            if st.button("Go", key=f"{page_key}_go", width="stretch") and 1 <= jump <= total_pages:
                st.session_state[page_key] = int(jump) - 1
                st.rerun()
    with c_next:
        if st.button("下一页 ▶", key=f"{page_key}_next", disabled=(current_page >= total_pages - 1), width="stretch"):
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
    if max_rows <= 0:
        return []

    rows = []
    stream_query = query.limit(max_rows)
    if batch_size > 0:
        stream_query = stream_query.yield_per(batch_size)
    for row in stream_query:
        rows.append(row)
    return rows


def render_blacklist_export_button(db, status: int, fn: str, fs: str, fm: list[str],
                                   sort_key: str, sort_asc: bool, total: int,
                                   filename_prefix: str, button_key: str):
    """渲染名单导出 Excel 按钮（分批查询，惰性生成缓存，避免多选刷新时造成灾难性卡顿）。"""
    if total == 0:
        return
        
    # 定义当前查询状态的唯一签名
    current_hash = f"{status}_{fn}_{fs}_{','.join(sorted(fm))}_{sort_key}_{sort_asc}"
    cache_hash_key = f"{button_key}_hash"
    cache_data_key = f"{button_key}_data"
    
    # 若缓存失效或条件变化，展现渲染生成按钮，阻断底层耗时运算
    if st.session_state.get(cache_hash_key) != current_hash or st.session_state.get(cache_data_key) is None:
        if st.button(f"⚡ 准备打包下载所有筛选记录（共 {total} 条）", width="stretch", key=f"{button_key}_prep"):
            with st.spinner(SPINNER_EXPORT):
                base = build_blacklist_query(db, status, fn, fs, major_categories=fm)
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
            width="stretch"
        )


def render_single_unit_selector(key_prefix: str, default_val: str = "", label: str = "所在单位") -> str:
    """渲染类似名单查询页面的级联单位选择器（弹出单选版 + 模糊检索）。注意：此组件包含 st.button，不可放于 st.form 内。"""
    from core.config import ALL_UNIT_LIST

    sel_key = f"{key_prefix}_single_unit"
    search_key = f"{key_prefix}_search"
    clear_key = f"{key_prefix}_search_clear"
    panel_open_key = f"{key_prefix}_unit_panel_open"
    toggle_key = f"{key_prefix}_panel_toggle"

    if sel_key not in st.session_state:
        st.session_state[sel_key] = default_val

    # 必须在创建 text_input 之前清空，否则同一次 run 内改 key 会触发 StreamlitAPIException
    if st.session_state.get(clear_key):
        if search_key in st.session_state:
            st.session_state[search_key] = ""
        st.session_state[clear_key] = False

    current_val = st.session_state[sel_key]

    st.markdown(f"<div style='font-size:14px;margin-bottom:6px;opacity:0.8'>{label}</div>", unsafe_allow_html=True)
    btn_label = f"🏫 当前选择：{current_val}" if current_val else "🏫 请点击展开详细分类面板 ▾"
    panel_open = st.session_state.get(panel_open_key, False)
    toggle_label = btn_label if not panel_open else (f"🏫 {current_val or '未选择'} · 点击收起 ▾")

    if st.button(toggle_label, key=toggle_key, width="stretch"):
        st.session_state[panel_open_key] = not panel_open
        st.rerun()

    if not st.session_state.get(panel_open_key, False):
        return st.session_state[sel_key]

    with st.container(border=True):
        st.markdown(
            "<div style='font-size:13px;color:gray;margin-bottom:8px'>在下方输入关键字模糊检索，或者按大类折叠展开选择。选中后将自动套用并折叠该面板。</div>",
            unsafe_allow_html=True,
        )

        search_val = st.text_input(
            "模糊检索",
            key=search_key,
            placeholder="【🔍 输入搜索词按回车，仅显示匹配项】",
            label_visibility="collapsed",
        )

        if search_val and search_val.strip():
            matches = [u for u in ALL_UNIT_LIST if search_val.strip().lower() in u.lower()]
            if matches:
                st.caption(f"为您查找到以下 **{len(matches)}** 个相关单位，点击直接录入：")
                for m in matches:
                    if st.button(m, key=f"{key_prefix}_match_{m}", width="stretch", type="primary"):
                        st.session_state[sel_key] = m
                        st.session_state[clear_key] = True
                        st.session_state[panel_open_key] = False
                        st.rerun()
            else:
                st.caption("未查找到包含该字符的单位分类。")
            st.markdown("---")

        with st.container(height=300, border=False):
            for cat, units in UNIT_CATEGORY_MAP.items():
                with st.expander(f"📁 **{cat}** ({len(units)}个院系)"):
                    for u in units:
                        btn_type = "primary" if current_val == u else "secondary"
                        if st.button(u, key=f"{key_prefix}_btn_{u}", width="stretch", type=btn_type):
                            st.session_state[sel_key] = u
                            st.session_state[panel_open_key] = False
                            st.rerun()

    return st.session_state[sel_key]

def clamp_page(page_key: str, total_pages: int) -> int:
    """从 session_state 读取当前页并限制在有效范围内。"""
    page = st.session_state.get(page_key, 0)
    page = max(0, min(page, total_pages - 1))
    st.session_state[page_key] = page
    return page
