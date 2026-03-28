"""
教师端页面：学术诚信档案查询（只读）、批量智能比对、个人操作记录。
单条查询支持姓名或学号（可多行/「姓名 学号」一次查多人）；批量比对支持上传 Excel、命中/未命中报告。
"""
import re
from io import BytesIO

import pandas as pd
import streamlit as st

from core.config import (
    AUDIT_QUERY_BATCH,
    AUDIT_QUERY_SINGLE,
    AUDIT_TYPE_NAMES,
    CAPTION_BATCH_INTRO,
    CAPTION_TEACHER,
    EMPTY_CANNOT_GET_USER,
    EMPTY_NO_OPERATION_LOG,
    LABEL_BATCH_PAGE_OPTIONS,
    LABEL_PAGE_SIZE,
    LABEL_SELECT_FEATURE,
    LABEL_STUDENT_ID,
    LIST_PAGE_SIZE_OPTIONS,
    MIME_XLSX,
    MSG_HAVE_HIT,
    MSG_BATCH_NO_HIT_GOOD,
    MSG_NO_RECORD_GOOD,
    MSG_TRY_AGAIN,
    SESSION_KEY_USER_NAME,
    TITLE_TEACHER,
    TERM_DISHONEST_RECORD,
)
from sqlalchemy import and_, or_, func

from core.database import db_session
from core.models import AuditLog, Blacklist
from views.components import render_simple_pagination, render_blacklist_table
from datetime import datetime
from core.utils import (
    REQUIRED_EXCEL_COLUMNS,
    cell_str,
    clean_student_id,
    log_audit_action,
    parse_batch_check_excel,
    validate_student_id,
    sanitize_for_export,
)


def _render_my_logs():
    """个人记录：展示当前用户最近操作历史（审计日志）。使用 db_session 确保会话关闭。"""
    st.subheader("个人记录")
    st.caption("您最近的操作历史（最多 100 条）。")
    name = st.session_state.get(SESSION_KEY_USER_NAME, "")
    if not name:
        st.caption(EMPTY_CANNOT_GET_USER)
        return
    with db_session() as db:
        try:
            with st.spinner("加载中..."):
                logs = (
                    db.query(AuditLog)
                    .filter(AuditLog.operator_name == name)
                    .order_by(AuditLog.timestamp.desc())
                    .limit(100)
                    .all()
                )
            if not logs:
                st.caption(EMPTY_NO_OPERATION_LOG)
                return
            log_df = pd.DataFrame([
                {
                    "时间": str(r.timestamp),
                    "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type),
                    "对象": r.target or "",
                    "详情": (r.details or "")[:80],
                }
                for r in logs
            ])
            st.dataframe(log_df, use_container_width=True, hide_index=True)
        except Exception:
            st.error("加载失败，" + MSG_TRY_AGAIN)


# 教师端侧边栏导航选项（与管理员界面一致：左侧单选导航）
TEACHER_NAV_OPTIONS = ["› 单条查询", "› 批量比对", "› 个人记录"]
TEACHER_NAV_SINGLE, TEACHER_NAV_BATCH, TEACHER_NAV_LOG = 0, 1, 2
TEACHER_NAV_KEY = "teacher_nav_radio"


def _get_teacher_nav_index():
    """从 session_state 读取当前选中的板块索引。"""
    val = st.session_state.get(TEACHER_NAV_KEY, TEACHER_NAV_OPTIONS[TEACHER_NAV_SINGLE])
    if val in TEACHER_NAV_OPTIONS:
        return TEACHER_NAV_OPTIONS.index(val)
    return TEACHER_NAV_SINGLE


def _on_teacher_nav_change():
    st.session_state["teacher_nav_loading"] = True

def render_teacher_sidebar_nav():
    """在侧边栏渲染身份标题与三个功能板块导航（由 app 在 with st.sidebar 内调用）。"""
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
    """教师页：根据侧边栏选中项渲染单条查询 / 批量比对 / 个人记录（与管理员界面布局一致）。"""
    st.title(TITLE_TEACHER)
    st.caption(CAPTION_TEACHER)

    main_area = st.empty()

    if st.session_state.pop("teacher_nav_loading", False):
        main_area.info("⏳ 正在为您极速切换板块并提取核心数据，请稍候...", icon="🚀")
        import time
        time.sleep(0.05)

    nav_index = _get_teacher_nav_index()
    
    with main_area.container():
        if nav_index == TEACHER_NAV_SINGLE:
            _render_single_search()
        elif nav_index == TEACHER_NAV_BATCH:
            _render_batch_check()
        else:
            _render_my_logs()


def _parse_one_line(line: str):
    """
    解析一行：若含连续数字（≥6 位）视为学号，拆成 (姓名, 学号)；否则整行作为姓名或学号。
    返回 (name_part 或 None, id_part 或 None)。两者至少一个非空。
    """
    line = line.strip()
    if not line:
        return None, None
    # 找连续数字段（≥6 位）作为学号候选
    m = re.search(r"\d{6,}", line)
    if m:
        id_raw = m.group()
        name_part = (line[: m.start()] + line[m.end() :]).strip()
        name_part = " ".join(name_part.split())  # 多余空白压成单空格
        id_clean = clean_student_id(id_raw)
        if id_clean:
            return (name_part if name_part else None, id_clean)
    # 无 ≥6 位连续数字：清洗后与原文一致说明无空白/全角变换，视为纯姓名
    clean = clean_student_id(line)
    if clean == line:
        return (line, None)
    # 清洗后有变化且非空，视为学号（含空白或全角数字）；否则视为姓名
    if clean:
        return (None, clean)
    return (line, None)

def _render_single_search():
    """单条查询：支持「姓名 工号/学号」、纯姓名、纯工号/学号；每行一人，可多行查多人。"""
    with st.form("teacher_single_search_form"):
        search_input = st.text_area(
            f"请输入姓名或{LABEL_STUDENT_ID}",
            key="teacher_search",
            placeholder=f"每行一人，可写「姓名 {LABEL_STUDENT_ID}」或仅姓名/仅{LABEL_STUDENT_ID}",
            height=100,
        )
        search_clicked = st.form_submit_button("查询")

    if not search_clicked:
        return

    raw_text = (search_input or "").strip()
    if not raw_text:
        st.error(f"请输入姓名或{LABEL_STUDENT_ID}后再查询。")
        return

    # 先按换行拆成行（每行一人），逗号、顿号也视为换行
    for sep in (",", "，", "、"):
        raw_text = raw_text.replace(sep, "\n")
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
    if not lines:
        st.error(f"请输入至少一个姓名或{LABEL_STUDENT_ID}。")
        return

    # 解析每行为 (name, id) 或 (name, None) 或 (None, id)
    parsed = []
    for ln in lines:
        name_part, id_part = _parse_one_line(ln)
        if name_part is None and id_part is None:
            continue
        if id_part and len(id_part) >= 6:
            ok, err = validate_student_id(id_part)
            if not ok:
                st.error(err or f"{LABEL_STUDENT_ID}格式有误，请检查后重试。")
                return
        parsed.append((name_part, id_part))

    if not parsed:
        st.error(f"请输入至少一个有效的姓名或{LABEL_STUDENT_ID}。")
        return

    with db_session() as db:
        try:
            with st.spinner("正在查询..."):
                # 将多条件合并为单次 OR 查询，避免 N+1
                conditions = []
                for name_part, id_part in parsed:
                    if name_part is not None and id_part is not None:
                        name_clean = "".join(name_part.split())
                        conditions.append(and_(func.replace(Blacklist.name, ' ', '') == name_clean, Blacklist.student_id == id_part))
                    elif id_part is not None:
                        conditions.append(Blacklist.student_id == id_part)
                    else:
                        name_clean = "".join(name_part.split())
                        conditions.append(func.replace(Blacklist.name, ' ', '') == name_clean)
                results = db.query(Blacklist).filter(Blacklist.status == 1, or_(*conditions)).all()
                seen_sids = set()
                unique_records = []
                for r in results:
                    if r.student_id not in seen_sids:
                        seen_sids.add(r.student_id)
                        unique_records.append(r)
        except Exception:
            st.error("查询失败，" + MSG_TRY_AGAIN)
            return

    log_audit_action(AUDIT_QUERY_SINGLE, target="", details=f"查询 {len(parsed)} 人，命中 {len(unique_records)} 人")

    if not unique_records:
        st.success(MSG_NO_RECORD_GOOD)
        return

    st.error(f"共查 {len(parsed)} 人，命中 {len(unique_records)} 条{TERM_DISHONEST_RECORD}，请核实。")
    render_blacklist_table(unique_records, page_size=max(1, len(unique_records)), current_page=0)


# 教师端批量比对默认每页条数（可选 10/20/50）
TEACHER_BATCH_PAGE_SIZE_DEFAULT = 10
TEACHER_BATCH_PAGE_OPTIONS = [o for o in LIST_PAGE_SIZE_OPTIONS if o <= 50]


def _render_batch_check():
    """批量智能比对：上传 Excel，按学号与黑名单比对，展示结果并支持下载报告；表格分页每页 10 条。"""
    st.subheader("批量智能比对")
    st.caption(CAPTION_BATCH_INTRO)
    uploaded = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls"], key="teacher_batch_file")
    run_batch = st.button("开始比对", key="teacher_batch_btn")

    if uploaded and run_batch:
        try:
            with st.spinner("正在解析并比对..."):
                df = parse_batch_check_excel(uploaded)
        except ValueError as e:
            st.error(str(e))
            return

        if df.empty:
            st.warning(f"Excel 中未找到有效的{LABEL_STUDENT_ID}。")
            return

        student_ids = df["学号"].dropna().astype(str).unique().tolist()
        with db_session() as db:
            try:
                BATCH_SIZE = 500
                matched = []
                for i in range(0, len(student_ids), BATCH_SIZE):
                    batch = student_ids[i : i + BATCH_SIZE]
                    batch_records = (
                        db.query(Blacklist)
                        .filter(Blacklist.status == 1, Blacklist.student_id.in_(batch))
                        .all()
                    )
                    matched.extend(batch_records)
            except Exception:
                st.error("比对失败，" + MSG_TRY_AGAIN)
                return

        log_audit_action(AUDIT_QUERY_BATCH, target=uploaded.name, details=f"共 {len(student_ids)} 条，命中 {len(matched)} 条")

        # 按学号构建上传名单行（与导入格式一致：姓名、学号、专业、原因、处分时间），每学号取首行（无命中时也需展示/导出）
        id_to_upload_row = {}
        for _, row in df.iterrows():
            sid = str(row.get("学号", "")).strip()
            if not sid or sid in id_to_upload_row:
                continue
            dt = row.get("处分时间") if "处分时间" in df.columns else row.get("处分日期", "")
            id_to_upload_row[sid] = {
                "姓名": cell_str(row.get("姓名")),
                "学号": sid,
                "专业": cell_str(row.get("专业")),
                "原因": cell_str(row.get("原因")),
                "处分时间": cell_str(dt),
            }
        upload_rows = [id_to_upload_row[sid] for sid in student_ids if sid in id_to_upload_row]
        if len(upload_rows) < len(student_ids):
            for sid in student_ids:
                if sid not in id_to_upload_row:
                    id_to_upload_row[sid] = {"姓名": "", "学号": sid, "专业": "", "原因": "", "处分时间": ""}
            upload_rows = [id_to_upload_row[sid] for sid in student_ids]

        # 无论是否命中，都写入 session_state，供分页、未命中表与下载使用；无命中时 matched 为空列表
        today_date = datetime.now().date()
        st.session_state["teacher_batch_matched"] = [
            {
                "姓名": r.name,
                "学号": r.student_id,
                "所在单位": r.major or "",
                "认定结论": (str(r.reason) if r.reason and str(r.reason).endswith(".pdf") else ""),
                "处理原因": (r.reason_text or ""),
                "认定日期": str(r.punishment_date) if r.punishment_date else "",
                "处理起至时间": f"{r.impact_start_date} 至 {r.impact_end_date}" if r.impact_start_date and r.impact_end_date else (str(r.impact_start_date) if r.impact_start_date else (str(r.impact_end_date) if r.impact_end_date else "")),
                "影响期": "✅ 是" if (
                    (r.impact_start_date and r.impact_end_date and r.impact_start_date <= today_date <= r.impact_end_date) or 
                    (r.impact_start_date and not r.impact_end_date and r.impact_start_date <= today_date) or
                    (r.impact_end_date and not r.impact_start_date and today_date <= r.impact_end_date)
                ) else "❌ 否"
            }
            for r in matched
        ]
        st.session_state["teacher_batch_upload_count"] = len(student_ids)
        st.session_state["teacher_batch_upload_ids"] = list(student_ids)
        st.session_state["teacher_batch_upload_rows"] = upload_rows
        st.session_state["teacher_batch_page"] = 0
        st.session_state["teacher_batch_unmatched_page"] = 0

    if "teacher_batch_matched" not in st.session_state:
        return

    matched_store = st.session_state["teacher_batch_matched"]
    total = len(matched_store)
    upload_count = st.session_state.get("teacher_batch_upload_count", total)
    upload_ids = st.session_state.get("teacher_batch_upload_ids", [d.get("学号", "") for d in matched_store])

    page_size_t = st.session_state.get("teacher_batch_page_size", TEACHER_BATCH_PAGE_SIZE_DEFAULT)
    if page_size_t not in TEACHER_BATCH_PAGE_OPTIONS:
        page_size_t = TEACHER_BATCH_PAGE_SIZE_DEFAULT
    total_pages = max(1, (total + page_size_t - 1) // page_size_t)
    current_page = st.session_state.get("teacher_batch_page", 0)
    current_page = max(0, min(current_page, total_pages - 1))
    st.session_state["teacher_batch_page"] = current_page

    start = current_page * page_size_t
    end = min(start + page_size_t, total)
    page_data = matched_store[start:end]

    # 汇总：上传 N 人，命中 M 人
    st.caption(f"上传名单共 **{upload_count}** 人，命中 **{total}** 人。")
    if total == 0:
        st.success(MSG_BATCH_NO_HIT_GOOD)

    # 命中名单区：仅当有命中时展示表格与分页
    if total > 0:
        with st.expander(LABEL_BATCH_PAGE_OPTIONS, expanded=False):
            idx_t = TEACHER_BATCH_PAGE_OPTIONS.index(page_size_t) if page_size_t in TEACHER_BATCH_PAGE_OPTIONS else 0
            new_ps = st.selectbox(LABEL_PAGE_SIZE, TEACHER_BATCH_PAGE_OPTIONS, index=idx_t, key="teacher_batch_page_size_select")
            if new_ps != page_size_t:
                st.session_state["teacher_batch_page_size"] = new_ps
                st.session_state["teacher_batch_page"] = 0
                st.rerun()
        st.error(f"{MSG_HAVE_HIT.format(n=total)}（当前第 {current_page + 1}/{total_pages} 页）")
        batch_table = pd.DataFrame(page_data)
        st.dataframe(
            batch_table, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "学号": st.column_config.TextColumn(
                    LABEL_STUDENT_ID,
                ),
                "认定结论": st.column_config.LinkColumn(
                    "认定结论",
                    display_text="📥 下载公示文件",
                    help="点击下载/预览官方 PDF 报告"
                )
            }
        )
        st.caption("注：表格内『认定结论』为空白代表该人员暂未上传 PDF 公示文件。")
        render_simple_pagination("teacher_batch_page", current_page, total_pages, len(page_data))

    # 未命中人员：与命中表相同的列（姓名、学号、专业、原因、处分时间），分页展示
    upload_rows = st.session_state.get("teacher_batch_upload_rows", [])
    matched_sids = {d["学号"] for d in matched_store}
    not_matched_ids = [s for s in upload_ids if s not in matched_sids]
    sid_to_upload = {r["学号"]: r for r in upload_rows}
    default_row = {"姓名": "", "学号": "", "专业": "", "原因": "", "处分时间": ""}
    not_matched_rows = [
        sid_to_upload.get(sid, {**default_row, "学号": sid})
        for sid in not_matched_ids
    ]

    not_total = len(not_matched_rows)
    page_size_u = st.session_state.get("teacher_batch_unmatched_page_size", TEACHER_BATCH_PAGE_SIZE_DEFAULT)
    if page_size_u not in TEACHER_BATCH_PAGE_OPTIONS:
        page_size_u = TEACHER_BATCH_PAGE_SIZE_DEFAULT
    total_pages_u = max(1, (not_total + page_size_u - 1) // page_size_u)
    current_page_u = st.session_state.get("teacher_batch_unmatched_page", 0)
    current_page_u = max(0, min(current_page_u, total_pages_u - 1))
    st.session_state["teacher_batch_unmatched_page"] = current_page_u
    start_u = current_page_u * page_size_u
    end_u = min(start_u + page_size_u, not_total)
    page_data_u = not_matched_rows[start_u:end_u]

    st.subheader("未命中名单")
    st.caption(f"上传名单中未在生效黑名单中的 **{not_total}** 人。（列与导入格式一致：姓名、学号、专业、原因、处分时间）")
    if not_total > 0:
        with st.expander("未命中名单 - 显示选项", expanded=False):
            idx_u = TEACHER_BATCH_PAGE_OPTIONS.index(page_size_u) if page_size_u in TEACHER_BATCH_PAGE_OPTIONS else 0
            new_ps_u = st.selectbox(LABEL_PAGE_SIZE, TEACHER_BATCH_PAGE_OPTIONS, index=idx_u, key="teacher_batch_unmatched_page_size_select")
            if new_ps_u != page_size_u:
                st.session_state["teacher_batch_unmatched_page_size"] = new_ps_u
                st.session_state["teacher_batch_unmatched_page"] = 0
                st.rerun()
        unmatched_table = pd.DataFrame([{c: d.get(c, "") for c in REQUIRED_EXCEL_COLUMNS} for d in page_data_u])
        st.dataframe(unmatched_table, use_container_width=True, hide_index=True)
        render_simple_pagination("teacher_batch_unmatched_page", current_page_u, total_pages_u, len(page_data_u))
    else:
        st.caption("名单内全部命中，无未命中人员。")

    # 下载：命中/未命中均按导入格式（姓名、学号、专业、原因、处分时间）+ 是否命中，含完整信息
    export_columns = REQUIRED_EXCEL_COLUMNS + ["是否命中"]
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        if total > 0:
            report_rows = [{**{c: sanitize_for_export(d.get(c, "")) for c in REQUIRED_EXCEL_COLUMNS}, "是否命中": "是"} for d in matched_store]
            report_df = pd.DataFrame(report_rows, columns=export_columns)
            buf = BytesIO()
            report_df.to_excel(buf, index=False, engine="openpyxl")
            buf.seek(0)
            st.download_button(
                label="下载命中名单 (Excel)",
                data=buf.getvalue(),
                file_name="比对结果_命中名单.xlsx",
                mime=MIME_XLSX,
                key="teacher_batch_download",
            )
        else:
            st.caption("无命中记录，无需下载命中名单。")
    with col_d2:
        if not_matched_rows:
            no_hit_df = pd.DataFrame(
                [{**{c: sanitize_for_export(r.get(c, "")) for c in REQUIRED_EXCEL_COLUMNS}, "是否命中": "否"} for r in not_matched_rows],
                columns=export_columns,
            )
            buf2 = BytesIO()
            no_hit_df.to_excel(buf2, index=False, engine="openpyxl")
            buf2.seek(0)
            st.download_button(
                label="下载未命中名单 (Excel)",
                data=buf2.getvalue(),
                file_name="比对结果_未命中名单.xlsx",
                mime=MIME_XLSX,
                key="teacher_batch_download_no_hit",
            )
        else:
            st.caption("名单内全部命中，无未命中名单。")
