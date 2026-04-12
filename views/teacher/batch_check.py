from io import BytesIO
import pandas as pd
import streamlit as st
from datetime import datetime

from core.database import db_session
from core.models import Blacklist
from views.components import render_simple_pagination
from core.excel_processor import REQUIRED_EXCEL_COLUMNS, cell_str, parse_batch_check_excel, sanitize_for_export
from core.audit_logger import log_audit_action
from core.config import (
    AUDIT_QUERY_BATCH,
    CAPTION_BATCH_INTRO,
    LABEL_BATCH_PAGE_OPTIONS,
    LABEL_PAGE_SIZE,
    LABEL_STUDENT_ID,
    LIST_PAGE_SIZE_OPTIONS,
    MIME_XLSX,
    MSG_HAVE_HIT,
    MSG_BATCH_NO_HIT_GOOD,
    MSG_TRY_AGAIN,
)

# 教师端批量比对默认每页条数（可选 10/20/50）
TEACHER_BATCH_PAGE_SIZE_DEFAULT = 10
TEACHER_BATCH_PAGE_OPTIONS = [o for o in LIST_PAGE_SIZE_OPTIONS if o <= 50]


def render_batch_check():
    """批量智能比对：上传 Excel，按学号与黑名单比对，展示结果并支持下载报告；表格分页每页 10 条。"""
    st.subheader("批量智能比对")
    st.caption(CAPTION_BATCH_INTRO)
    st.caption("Excel 须包含「学号/工号」列（必填），系统将根据该列与生效名单进行比对。其余列为辅助信息，方便您核对结果。")
    col_tpl, _ = st.columns([1, 3])
    with col_tpl:
        # 模板列名：必填列 + 选填辅助列（选填标注）
        _TPL_COLUMNS = ["姓名", "学号/工号", "单位（选填）", "原因（选填）", "处分时间（选填）"]
        tpl_buf = BytesIO()
        pd.DataFrame(columns=_TPL_COLUMNS).to_excel(tpl_buf, index=False, engine="openpyxl")
        st.download_button(
            "📥 下载 Excel 模板",
            data=tpl_buf.getvalue(),
            file_name="批量比对模板.xlsx",
            mime=MIME_XLSX,
            key="teacher_batch_tpl",
        )
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
        st.toast(f"比对完成：共 {len(student_ids)} 人，命中 {len(matched)} 条")

        # 按学号构建上传名单行（与导入格式一致：姓名、学号、专业、原因、处分时间）
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

        today_date = datetime.now().date()
        st.session_state["teacher_batch_matched"] = [
            {
                "姓名": r.name,
                "学号": r.student_id,
                "所在单位": r.major or "",
                "认定结论": ("📄 已上传" if r.reason and str(r.reason).endswith(".pdf") else ""),
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

    st.caption(f"上传名单共 **{upload_count}** 人，命中 **{total}** 人。")
    if total == 0:
        st.success(MSG_BATCH_NO_HIT_GOOD)

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
                "认定结论": st.column_config.TextColumn(
                    "认定结论",
                    help="显示『📄 已上传』代表已有 PDF 公示文件"
                )
            }
        )
        st.caption("注：表格内『认定结论』为空白代表该人员暂未上传 PDF 公示文件。")
        render_simple_pagination("teacher_batch_page", current_page, total_pages, len(page_data))

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
