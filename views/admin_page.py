"""
管理员页面：仪表盘、名单管理、系统维护、用户管理。
由 app 根据侧边栏导航（admin_nav_radio）渲染对应板块；名单支持分页、排序、每页条数、学号校验。
"""
import time
from datetime import datetime
from io import BytesIO

import bcrypt
import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func

from config import (
    AUDIT_ACTION_TYPES,
    AUDIT_ADD,
    AUDIT_BACKUP,
    AUDIT_DELETE,
    AUDIT_IMPORT,
    AUDIT_RESTORE,
    AUDIT_TYPE_NAMES,
    BATCH_IMPORT_COMMIT_EVERY,
    CAPTION_CONFIRM_RESTORE_DB,
    CAPTION_FILTER_BY_NAME_SID_MAJOR,
    EMPTY_NO_EFFECTIVE,
    EMPTY_NO_RECORDS,
    EMPTY_NO_REVOKED,
    EMPTY_NO_USER,
    LABEL_DISPLAY_OPTIONS_EXPANDER,
    LABEL_INIT_LIST,
    LABEL_NAME,
    LABEL_PAGE_SIZE,
    LABEL_REASON,
    LABEL_SORT_COLUMN,
    LABEL_DISPLAY_AND_SORT,
    LABEL_MAJOR,
    LABEL_PUNISHMENT_DATE,
    LABEL_SELECT_PANEL,
    LABEL_SORT_ORDER,
    LABEL_STUDENT_ID,
    LABEL_TABLE_SORT,
    LIST_PAGE_SIZE,
    LIST_PAGE_SIZE_OPTIONS,
    MIME_XLSX,
    MSG_DB_READ_FAIL,
    MSG_DB_RESTORE_FAIL,
    MSG_ENTER_VALID_SID,
    MSG_NOT_FOUND_EFFECTIVE,
    MSG_NOT_FOUND_REVOKED,
    MSG_TRY_AGAIN,
    MSG_TRY_AGAIN_OR_ADMIN,
    MSG_UPLOAD_EMPTY,
    MSG_CONFIRM_INIT_LIST,
    PASSWORD_MIN_LEN,
    PLACEHOLDER_FILTER_EMPTY,
    ROLE_ADMIN,
    ROLE_TEACHER,
    SESSION_KEY_USER_NAME,
    SESSION_KEY_USERNAME,
    SORT_ORDER_ASC,
    SORT_ORDER_OPTIONS,
    SUCCESS_ADDED,
    SUCCESS_DB_RESTORED,
    SUCCESS_IMPORT_DONE,
    SUCCESS_INIT_LIST,
    SUCCESS_PWD_RESET,
    SUCCESS_SAVED,
    USERNAME_MAX_LEN,
)
from database import SessionLocal, db_session
from models import AuditLog, Blacklist, User
from utils import (
    DATABASE_PATH,
    REQUIRED_EXCEL_COLUMNS,
    cell_str,
    clean_student_id,
    get_db_file_bytes,
    parse_blacklist_excel,
    validate_student_id,
)


def _log_action(action_type: str, target: str = "", details: str = ""):
    """写入审计日志。"""
    with db_session() as db:
        try:
            name = st.session_state.get(SESSION_KEY_USER_NAME, "未知")
            log = AuditLog(
                operator_name=name,
                action_type=action_type,
                target=target[:256] if target else None,
                details=details[:4096] if details else None,
            )
            db.add(log)
            db.commit()
        except Exception:
            db.rollback()


def _render_dashboard(db):
    """仪表盘：名单概览、分布图并排、近期变动、近期操作统计。"""
    st.caption("名单总数与生效/撤销概况、专业与年份分布、近期名单变动及操作统计，便于快速把握现状。")

    total = db.query(Blacklist).count()
    effective = db.query(Blacklist).filter(Blacklist.status == 1).count()
    revoked = db.query(Blacklist).filter(Blacklist.status == 0).count()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("名单总数", total)
    with col2:
        st.metric("生效中", effective)
    with col3:
        st.metric("已撤销", revoked)

    # 专业分布 与 按处分年份分布 并排，减少留白
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.subheader("专业分布")
        rows = db.query(Blacklist.major).filter(Blacklist.status == 1).all()
        if not rows:
            st.caption("暂无生效记录，无法生成专业分布图。")
        else:
            major_series = pd.Series([r[0] or "未填写" for r in rows])
            counts = major_series.value_counts().reset_index()
            counts.columns = ["专业", "人数"]
            fig = px.pie(counts, values="人数", names="专业", title="专业分布")
            st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        st.subheader("按处分年份分布")
        date_rows = db.query(Blacklist.punishment_date).filter(Blacklist.status == 1).all()
        if not date_rows or all(r[0] is None for r in date_rows):
            st.caption("暂无处分日期数据，无法生成年份分布图。")
        else:
            years = [r[0].year if r[0] else None for r in date_rows if r[0]]
            if not years:
                st.caption("暂无有效处分日期。")
            else:
                year_series = pd.Series(years)
                year_counts = year_series.value_counts().sort_index().reset_index()
                year_counts.columns = ["年份", "人数"]
                fig_bar = px.bar(year_counts, x="年份", y="人数", title="按处分年份分布")
                st.plotly_chart(fig_bar, use_container_width=True)

    # 近期名单变动：最近 10 条记录（按创建时间倒序）
    st.subheader("近期名单变动")
    st.caption("最近录入或更新的名单记录，便于核对与追溯。")
    try:
        recent = (
            db.query(Blacklist)
            .order_by(Blacklist.created_at.desc())
            .limit(10)
            .all()
        )
        if not recent:
            st.caption(EMPTY_NO_RECORDS)
        else:
            recent_df = pd.DataFrame(
                [
                    {
                        "姓名": r.name,
                        "学号": r.student_id,
                        "专业": (r.major or "")[:20],
                        "状态": "生效" if r.status == 1 else "已撤销",
                        "创建/更新": str(r.created_at)[:19] if r.created_at else "",
                    }
                    for r in recent
                ]
            )
            st.dataframe(recent_df, use_container_width=True, hide_index=True)
    except Exception:
        st.caption("加载近期变动失败。")


def _render_import_section(db):
    """名单管理：批量导入 Excel 预览与导入；上次导入结果详情与跳过行导出。"""
    st.subheader("批量导入")

    # 上次导入结果：详情与跳过行导出（若有）
    if st.session_state.get("admin_last_import_result"):
        res = st.session_state["admin_last_import_result"]
        with st.expander("▶ 上次导入结果", expanded=True):
            st.success(
                f"新增 **{res['imported']}** 条，更新 **{res['updated']}** 条"
                + (f"，跳过 **{res['skipped']}** 行（学号为空）。" if res["skipped"] else "。")
            )
            if res.get("skipped_rows"):
                st.caption("以下为跳过的行（学号为空），可下载后修正再导入。")
                skip_df = pd.DataFrame(res["skipped_rows"])
                st.dataframe(skip_df.head(20), use_container_width=True, hide_index=True)
                if len(res["skipped_rows"]) > 20:
                    st.caption(f"仅展示前 20 行，共 {len(res['skipped_rows'])} 行。")
                buf_skip = BytesIO()
                pd.DataFrame(res["skipped_rows"]).to_excel(buf_skip, index=False, engine="openpyxl")
                buf_skip.seek(0)
                st.download_button(
                    label="下载跳过行列表 (Excel)",
                    data=buf_skip.getvalue(),
                    file_name="导入跳过行.xlsx",
                    mime=MIME_XLSX,
                    key="admin_import_skip_download",
                )
            if st.button("关闭", key="admin_close_last_import"):
                del st.session_state["admin_last_import_result"]
                st.rerun()

    uploaded = st.file_uploader("上传 Excel (.xlsx / .xls)", type=["xlsx", "xls"], key="admin_import_file")
    # 上传新文件时解析并缓存，用于预览与导入
    if uploaded:
        if st.session_state.get("admin_import_filename") != uploaded.name:
            try:
                df_parsed = parse_blacklist_excel(uploaded)
                st.session_state["admin_import_df"] = df_parsed
                st.session_state["admin_import_filename"] = uploaded.name
            except ValueError as e:
                st.error(str(e))
    # 有解析结果时展示前 10 行预览
    if st.session_state.get("admin_import_df") is not None and st.session_state.get("admin_import_filename"):
        st.caption("以下为解析结果前 10 行预览，确认无误后点击「开始导入」。")
        st.dataframe(
            st.session_state["admin_import_df"].head(10),
            use_container_width=True,
            hide_index=True,
        )
        if st.button("开始导入", key="admin_import_btn"):
            df = st.session_state["admin_import_df"]
            imported = 0
            updated = 0
            skipped = 0
            skipped_rows = []
            last_imported = 0
            last_updated = 0
            batch_counter = 0
            try:
                with st.spinner("正在导入..."):
                    for idx, row in df.iterrows():
                        sid = str(row["学号"]).strip() if pd.notna(row["学号"]) else ""
                        if not sid:
                            skipped += 1
                            skipped_rows.append({
                                "行号": idx + 2,
                                **{c: cell_str(row.get(c)) for c in REQUIRED_EXCEL_COLUMNS},
                            })
                            continue
                        name = str(row["姓名"]).strip() if pd.notna(row["姓名"]) else ""
                        major = str(row["专业"]).strip() if pd.notna(row["专业"]) else None
                        reason = str(row["原因"]).strip() if pd.notna(row["原因"]) else None
                        raw_date = row.get("处分时间")
                        punishment_date = None
                        if pd.notna(raw_date):
                            try:
                                punishment_date = pd.to_datetime(raw_date).date()
                            except Exception:
                                pass
                        existing = db.query(Blacklist).filter(Blacklist.student_id == sid).first()
                        if existing:
                            existing.name = name or existing.name
                            existing.major = major if major else existing.major
                            existing.reason = reason if reason else existing.reason
                            if punishment_date:
                                existing.punishment_date = punishment_date
                            existing.status = 1
                            updated += 1
                        else:
                            rec = Blacklist(
                                name=name,
                                student_id=sid,
                                major=major or None,
                                reason=reason or None,
                                punishment_date=punishment_date,
                                status=1,
                            )
                            db.add(rec)
                            imported += 1
                        batch_counter += 1
                        if batch_counter >= BATCH_IMPORT_COMMIT_EVERY:
                            db.commit()
                            last_imported, last_updated = imported, updated
                            batch_counter = 0
                    if batch_counter > 0:
                        db.commit()
                    _log_action(
                        AUDIT_IMPORT,
                        target=st.session_state.get("admin_import_filename", ""),
                        details=f"新增 {imported} 条，更新 {updated} 条" + (f"，跳过 {skipped} 行" if skipped else ""),
                    )
                if "admin_import_df" in st.session_state:
                    del st.session_state["admin_import_df"]
                if "admin_import_filename" in st.session_state:
                    del st.session_state["admin_import_filename"]
                st.session_state["admin_last_import_result"] = {
                    "imported": imported,
                    "updated": updated,
                    "skipped": skipped,
                    "skipped_rows": skipped_rows,
                }
                st.success(SUCCESS_IMPORT_DONE)
                st.balloons()
                st.rerun()
            except Exception:
                db.rollback()
                st.error(
                    f"导入失败，已成功导入 {last_imported} 条，更新 {last_updated} 条；后续数据出错，请检查 Excel 格式（需包含：姓名、学号、专业、原因、处分时间）。"
                )


def _render_manual_add_section(db):
    """名单管理：手动新增单条记录。"""
    st.divider()
    st.subheader("手动新增")
    with st.form("admin_add_form"):
        add_name = st.text_input(LABEL_NAME, key="add_name")
        add_student_id = st.text_input(LABEL_STUDENT_ID, key="add_student_id")
        add_major = st.text_input(LABEL_MAJOR, key="add_major")
        add_reason = st.text_area(LABEL_REASON, key="add_reason")
        add_date = st.date_input(LABEL_PUNISHMENT_DATE, key="add_date")
        if st.form_submit_button("添加"):
            if not add_name or not add_student_id:
                st.error(f"请填写{LABEL_NAME}和{LABEL_STUDENT_ID}。")
            else:
                ok_sid, err_sid = validate_student_id(add_student_id)
                if not ok_sid:
                    st.error(err_sid or MSG_ENTER_VALID_SID)
                else:
                    try:
                        with st.spinner("正在保存..."):
                            sid_clean = clean_student_id(add_student_id)
                            if db.query(Blacklist).filter(Blacklist.student_id == sid_clean).first():
                                st.error(f"该{LABEL_STUDENT_ID}已存在。")
                            else:
                                rec = Blacklist(
                                    name=add_name.strip(),
                                    student_id=sid_clean,
                                    major=add_major.strip() or None,
                                    reason=add_reason.strip() or None,
                                    punishment_date=add_date,
                                    status=1,
                                )
                                db.add(rec)
                                db.commit()
                                _log_action(AUDIT_ADD, target=add_name, details=f"学号 {sid_clean[:8]}***")
                                st.success(SUCCESS_ADDED)
                                st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("添加失败，" + MSG_TRY_AGAIN_OR_ADMIN)


def _render_effective_list_section(db):
    """名单管理：生效名单、筛选、分页、导出；删除/初始化/编辑收在 expander 内。"""
    st.subheader("生效名单")
    effective_list = db.query(Blacklist).filter(Blacklist.status == 1).order_by(Blacklist.id).all()
    if not effective_list:
        st.caption(EMPTY_NO_EFFECTIVE)
    else:
        st.caption(CAPTION_FILTER_BY_NAME_SID_MAJOR)
        ef1, ef2, ef3 = st.columns(3)
        with ef1:
            filter_effective_name = st.text_input("姓名筛选", key="admin_effective_filter_name", placeholder=PLACEHOLDER_FILTER_EMPTY)
        with ef2:
            filter_effective_sid = st.text_input("学号筛选", key="admin_effective_filter_sid", placeholder=PLACEHOLDER_FILTER_EMPTY)
        with ef3:
            filter_effective_major = st.text_input("专业筛选", key="admin_effective_filter_major", placeholder=PLACEHOLDER_FILTER_EMPTY)
        fn = (filter_effective_name or "").strip()
        fs = (filter_effective_sid or "").strip()
        fm = (filter_effective_major or "").strip()
        filtered_effective = [
            r for r in effective_list
            if (not fn or (fn in (r.name or "")))
            and (not fs or (fs in (r.student_id or "")))
            and (not fm or (fm in (r.major or "")))
        ]
        # 每页条数、表格排序：以控件 key 为唯一数据源，不手动 rerun，保证稳定
        page_size_eff = st.session_state.get("admin_effective_page_size_select", LIST_PAGE_SIZE)
        if page_size_eff not in LIST_PAGE_SIZE_OPTIONS:
            page_size_eff = LIST_PAGE_SIZE
        sort_cols_eff = ["姓名", "学号", "专业", "处分日期"]
        if "admin_effective_sort_select" not in st.session_state:
            st.session_state["admin_effective_sort_select"] = "学号"
        if "admin_effective_order_select" not in st.session_state:
            st.session_state["admin_effective_order_select"] = SORT_ORDER_ASC
        with st.expander(LABEL_DISPLAY_OPTIONS_EXPANDER, expanded=False):
            row_opt_eff, col_sort_eff, col_order_eff = st.columns([1, 1, 1])
            with row_opt_eff:
                idx_size = LIST_PAGE_SIZE_OPTIONS.index(page_size_eff) if page_size_eff in LIST_PAGE_SIZE_OPTIONS else 0
                page_size_eff = st.selectbox(LABEL_PAGE_SIZE, LIST_PAGE_SIZE_OPTIONS, index=idx_size, key="admin_effective_page_size_select")
            with col_sort_eff:
                sort_key_eff = st.selectbox(LABEL_SORT_COLUMN, sort_cols_eff, key="admin_effective_sort_select")
            with col_order_eff:
                sort_order_eff = st.selectbox(LABEL_SORT_ORDER, SORT_ORDER_OPTIONS, key="admin_effective_order_select")
        sort_asc_eff = sort_order_eff == SORT_ORDER_ASC
        if sort_key_eff not in sort_cols_eff:
            sort_key_eff = "学号"
        attr_map = {"姓名": "name", "学号": "student_id", "专业": "major", "处分日期": "punishment_date"}
        key_attr = attr_map.get(sort_key_eff, "student_id")

        def _sort_key_eff(r):
            try:
                if key_attr == "punishment_date":
                    v = getattr(r, key_attr, None)
                    return (v.isoformat() if hasattr(v, "isoformat") else str(v)) if v else ""
                v = getattr(r, key_attr, None)
                return (v or "").__str__()
            except Exception:
                return ""

        try:
            filtered_effective = sorted(filtered_effective, key=_sort_key_eff, reverse=not sort_asc_eff)
        except Exception:
            pass  # 排序失败则保持原序，保证稳定

        total_eff = len(filtered_effective)
        total_pages_eff = max(1, (total_eff + page_size_eff - 1) // page_size_eff)
        page_eff = st.session_state.get("admin_effective_page", 0)
        page_eff = max(0, min(page_eff, total_pages_eff - 1))
        st.session_state["admin_effective_page"] = page_eff
        start_eff = page_eff * page_size_eff
        page_effective = filtered_effective[start_eff : start_eff + page_size_eff]
        df_display = pd.DataFrame(
            [
                {
                    "序号": start_eff + i,
                    "姓名": r.name,
                    "学号": r.student_id,
                    "专业": r.major or "",
                    "原因": (r.reason or "")[:50],
                    "处分日期": str(r.punishment_date) if r.punishment_date else "",
                }
                for i, r in enumerate(page_effective, 1)
            ]
        )
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        # 分页：单行紧凑布局 — 状态 | 上一页 | 下一页 | 跳至 [输入] [跳转]
        r1_eff, r2_eff, r3_eff, r4_eff, r5_eff = st.columns([2.2, 0.9, 0.9, 1.2, 0.8])
        with r1_eff:
            st.caption(f"第 {page_eff + 1}/{total_pages_eff} 页 · 本页 {len(page_effective)} 条 · 共 {total_eff} 条")
        with r2_eff:
            if st.button("上一页", key="admin_effective_prev", disabled=(page_eff <= 0)):
                st.session_state["admin_effective_page"] = page_eff - 1
                st.rerun()
        with r3_eff:
            if st.button("下一页", key="admin_effective_next", disabled=(page_eff >= total_pages_eff - 1)):
                st.session_state["admin_effective_page"] = page_eff + 1
                st.rerun()
        with r4_eff:
            jump_page_eff = st.number_input("跳至第 … 页", min_value=1, max_value=max(1, total_pages_eff), value=page_eff + 1, key="admin_effective_jump", label_visibility="collapsed")
        with r5_eff:
            if st.button("跳转", key="admin_effective_go") and 1 <= jump_page_eff <= total_pages_eff:
                st.session_state["admin_effective_page"] = int(jump_page_eff) - 1
                st.rerun()

        export_eff_df = pd.DataFrame(
            [
                {"序号": i, "姓名": r.name, "学号": r.student_id, "专业": r.major or "", "原因": r.reason or "", "处分日期": str(r.punishment_date) if r.punishment_date else ""}
                for i, r in enumerate(filtered_effective, 1)
            ]
        )
        if not export_eff_df.empty:
            buf_eff = BytesIO()
            export_eff_df.to_excel(buf_eff, index=False, engine="openpyxl")
            buf_eff.seek(0)
            st.download_button(
                label="导出当前筛选的生效名单 (Excel)",
                data=buf_eff.getvalue(),
                file_name=f"生效名单_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime=MIME_XLSX,
                key="admin_export_effective",
            )

        with st.expander("▶ 按学号删除、初始化名单、编辑", expanded=False):
            st.caption("按学号软删除、一键初始化生效名单、按学号进入编辑。")
            del_sid_input = st.text_input(f"输入要删除的{LABEL_STUDENT_ID}", key="del_student_id", placeholder=LABEL_STUDENT_ID)
            if st.button("软删除（设为已撤销）", key="admin_del_btn") and del_sid_input:
                ok_del, err_del = validate_student_id(del_sid_input)
                if not ok_del:
                    st.error(err_del or MSG_ENTER_VALID_SID)
                else:
                    try:
                        sid_clean = clean_student_id(del_sid_input.strip())
                        rec = db.query(Blacklist).filter(
                            Blacklist.status == 1, Blacklist.student_id == sid_clean
                        ).first()
                        if not rec:
                            st.error(MSG_NOT_FOUND_EFFECTIVE)
                        else:
                            with st.spinner("正在更新..."):
                                rec.status = 0
                                db.commit()
                                _log_action(AUDIT_DELETE, target=sid_clean[:16], details=f"软删除：{rec.name} {sid_clean[:8]}***")
                            st.success("已软删除。")
                            st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("删除操作失败，" + MSG_TRY_AGAIN)

            st.divider()
            st.caption("将所有生效记录设为已撤销，清空生效名单。请谨慎操作。")
            if not st.session_state.get("admin_show_init_confirm"):
                if st.button(LABEL_INIT_LIST, key="admin_init_list_btn"):
                    st.session_state["admin_show_init_confirm"] = True
                    st.rerun()
            else:
                st.warning(MSG_CONFIRM_INIT_LIST)
                col_confirm, col_cancel = st.columns(2)
                with col_confirm:
                    if st.button("确认初始化", key="admin_init_confirm_btn"):
                        try:
                            with st.spinner("正在初始化..."):
                                n = db.query(Blacklist).filter(Blacklist.status == 1).update({Blacklist.status: 0})
                                db.commit()
                                _log_action(AUDIT_DELETE, target=LABEL_INIT_LIST, details=f"共 {n} 条生效记录设为已撤销")
                            if "admin_show_init_confirm" in st.session_state:
                                del st.session_state["admin_show_init_confirm"]
                            st.success(SUCCESS_INIT_LIST)
                            st.rerun()
                        except Exception:
                            db.rollback()
                            st.error("初始化失败，" + MSG_TRY_AGAIN)
                with col_cancel:
                    if st.button("取消", key="admin_init_cancel_btn"):
                        if "admin_show_init_confirm" in st.session_state:
                            del st.session_state["admin_show_init_confirm"]
                        st.rerun()

            st.divider()
            edit_sid_input = st.text_input(f"输入要编辑的{LABEL_STUDENT_ID}", key="edit_student_id", placeholder=LABEL_STUDENT_ID)
            if st.button("编辑", key="admin_edit_btn") and edit_sid_input:
                ok_edit, err_edit = validate_student_id(edit_sid_input)
                if not ok_edit:
                    st.error(err_edit or MSG_ENTER_VALID_SID)
                else:
                    try:
                        sid_edit = clean_student_id(edit_sid_input.strip())
                        rec = db.query(Blacklist).filter(
                            Blacklist.status == 1, Blacklist.student_id == sid_edit
                        ).first()
                        if not rec:
                            st.error(MSG_NOT_FOUND_EFFECTIVE)
                        elif rec.status == 0:
                            st.warning("已撤销记录不可编辑，请先在「已撤销名单」中恢复。")
                        else:
                            st.session_state["admin_edit_id"] = rec.id
                            st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("查找记录失败，" + MSG_TRY_AGAIN)


def _render_edit_form_section():
    """名单管理：编辑现有记录表单（独立会话）。"""
    if not st.session_state.get("admin_edit_id"):
        return
    edit_id = st.session_state["admin_edit_id"]
    with db_session() as edit_db:
        rec = edit_db.query(Blacklist).filter(Blacklist.id == edit_id).first()
        if not rec or rec.status != 1:
            if "admin_edit_id" in st.session_state:
                del st.session_state["admin_edit_id"]
            st.rerun()
        else:
            with st.form("admin_edit_form"):
                st.caption(f"正在编辑记录 ID：{edit_id}（{LABEL_STUDENT_ID}不可修改）")
                edit_name = st.text_input(LABEL_NAME, value=rec.name, key="admin_edit_name")
                st.text_input(f"{LABEL_STUDENT_ID}（不可修改）", value=rec.student_id, disabled=True, key="admin_edit_sid_display")
                edit_major = st.text_input(LABEL_MAJOR, value=rec.major or "", key="admin_edit_major")
                edit_reason = st.text_area(LABEL_REASON, value=rec.reason or "", key="admin_edit_reason")
                edit_date_val = rec.punishment_date
                edit_date = st.date_input(LABEL_PUNISHMENT_DATE, value=edit_date_val or datetime.now().date(), key="admin_edit_date")
                col_save, col_cancel = st.columns(2)
                with col_save:
                    submit_save = st.form_submit_button("保存修改")
                with col_cancel:
                    submit_cancel = st.form_submit_button("取消")
                if submit_save:
                    try:
                        rec.name = (edit_name or "").strip() or rec.name
                        rec.major = (edit_major or "").strip() or None
                        rec.reason = (edit_reason or "").strip() or None
                        rec.punishment_date = edit_date
                        edit_db.commit()
                        _log_action(AUDIT_ADD, target=f"编辑记录 {edit_id}", details=f"{rec.name} {rec.student_id[:8]}***")
                        if "admin_edit_id" in st.session_state:
                            del st.session_state["admin_edit_id"]
                        st.success(SUCCESS_SAVED)
                        st.rerun()
                    except Exception:
                        edit_db.rollback()
                        st.error("保存失败，" + MSG_TRY_AGAIN)
                if submit_cancel:
                    if "admin_edit_id" in st.session_state:
                        del st.session_state["admin_edit_id"]
                    st.rerun()


def _render_revoked_section(db):
    """名单管理：已撤销名单、筛选、分页、导出、按学号恢复。"""
    st.subheader("已撤销名单")
    revoked_list = db.query(Blacklist).filter(Blacklist.status == 0).order_by(Blacklist.id).all()
    if not revoked_list:
        st.caption(EMPTY_NO_REVOKED)
    else:
        st.caption(CAPTION_FILTER_BY_NAME_SID_MAJOR)
        rv1, rv2, rv3 = st.columns(3)
        with rv1:
            filter_revoked_name = st.text_input("姓名筛选", key="admin_revoked_filter_name", placeholder=PLACEHOLDER_FILTER_EMPTY)
        with rv2:
            filter_revoked_sid = st.text_input("学号筛选", key="admin_revoked_filter_sid", placeholder=PLACEHOLDER_FILTER_EMPTY)
        with rv3:
            filter_revoked_major = st.text_input("专业筛选", key="admin_revoked_filter_major", placeholder=PLACEHOLDER_FILTER_EMPTY)
        rn = (filter_revoked_name or "").strip()
        rs = (filter_revoked_sid or "").strip()
        rm = (filter_revoked_major or "").strip()
        filtered_revoked = [
            r for r in revoked_list
            if (not rn or (rn in (r.name or "")))
            and (not rs or (rs in (r.student_id or "")))
            and (not rm or (rm in (r.major or "")))
        ]
        page_size_rev = st.session_state.get("admin_revoked_page_size_select", LIST_PAGE_SIZE)
        if page_size_rev not in LIST_PAGE_SIZE_OPTIONS:
            page_size_rev = LIST_PAGE_SIZE
        sort_cols_rev = ["姓名", "学号", "专业", "处分日期"]
        if "admin_revoked_sort_select" not in st.session_state:
            st.session_state["admin_revoked_sort_select"] = "学号"
        if "admin_revoked_order_select" not in st.session_state:
            st.session_state["admin_revoked_order_select"] = SORT_ORDER_ASC
        with st.expander(LABEL_DISPLAY_OPTIONS_EXPANDER, expanded=False):
            row_opt_rev, col_sort_rev, col_order_rev = st.columns([1, 1, 1])
            with row_opt_rev:
                idx_size_rev = LIST_PAGE_SIZE_OPTIONS.index(page_size_rev) if page_size_rev in LIST_PAGE_SIZE_OPTIONS else 0
                page_size_rev = st.selectbox(LABEL_PAGE_SIZE, LIST_PAGE_SIZE_OPTIONS, index=idx_size_rev, key="admin_revoked_page_size_select")
            with col_sort_rev:
                sort_key_rev = st.selectbox(LABEL_SORT_COLUMN, sort_cols_rev, key="admin_revoked_sort_select")
            with col_order_rev:
                sort_order_rev = st.selectbox(LABEL_SORT_ORDER, SORT_ORDER_OPTIONS, key="admin_revoked_order_select")
        sort_asc_rev = sort_order_rev == SORT_ORDER_ASC
        if sort_key_rev not in sort_cols_rev:
            sort_key_rev = "学号"
        attr_map_rev = {"姓名": "name", "学号": "student_id", "专业": "major", "处分日期": "punishment_date"}
        key_attr_rev = attr_map_rev.get(sort_key_rev, "student_id")

        def _sort_key_rev(r):
            try:
                if key_attr_rev == "punishment_date":
                    v = getattr(r, key_attr_rev, None)
                    return (v.isoformat() if hasattr(v, "isoformat") else str(v)) if v else ""
                v = getattr(r, key_attr_rev, None)
                return (v or "").__str__()
            except Exception:
                return ""

        try:
            filtered_revoked = sorted(filtered_revoked, key=_sort_key_rev, reverse=not sort_asc_rev)
        except Exception:
            pass

        total_rev = len(filtered_revoked)
        total_pages_rev = max(1, (total_rev + page_size_rev - 1) // page_size_rev)
        page_rev = st.session_state.get("admin_revoked_page", 0)
        page_rev = max(0, min(page_rev, total_pages_rev - 1))
        st.session_state["admin_revoked_page"] = page_rev
        start_rev = page_rev * page_size_rev
        page_revoked = filtered_revoked[start_rev : start_rev + page_size_rev]
        df_revoked = pd.DataFrame(
            [
                {
                    "序号": start_rev + i,
                    "姓名": r.name,
                    "学号": r.student_id,
                    "专业": r.major or "",
                    "原因": (r.reason or "")[:50],
                    "处分日期": str(r.punishment_date) if r.punishment_date else "",
                }
                for i, r in enumerate(page_revoked, 1)
            ]
        )
        st.dataframe(df_revoked, use_container_width=True, hide_index=True)
        # 分页：单行紧凑布局 — 状态 | 上一页 | 下一页 | 跳至 [输入] [跳转]
        r1_rev, r2_rev, r3_rev, r4_rev, r5_rev = st.columns([2.2, 0.9, 0.9, 1.2, 0.8])
        with r1_rev:
            st.caption(f"第 {page_rev + 1}/{total_pages_rev} 页 · 本页 {len(page_revoked)} 条 · 共 {total_rev} 条")
        with r2_rev:
            if st.button("上一页", key="admin_revoked_prev", disabled=(page_rev <= 0)):
                st.session_state["admin_revoked_page"] = page_rev - 1
                st.rerun()
        with r3_rev:
            if st.button("下一页", key="admin_revoked_next", disabled=(page_rev >= total_pages_rev - 1)):
                st.session_state["admin_revoked_page"] = page_rev + 1
                st.rerun()
        with r4_rev:
            jump_page_rev = st.number_input("跳至第 … 页", min_value=1, max_value=max(1, total_pages_rev), value=page_rev + 1, key="admin_revoked_jump", label_visibility="collapsed")
        with r5_rev:
            if st.button("跳转", key="admin_revoked_go") and 1 <= jump_page_rev <= total_pages_rev:
                st.session_state["admin_revoked_page"] = int(jump_page_rev) - 1
                st.rerun()

        export_rev_df = pd.DataFrame(
            [
                {"序号": i, "姓名": r.name, "学号": r.student_id, "专业": r.major or "", "原因": r.reason or "", "处分日期": str(r.punishment_date) if r.punishment_date else ""}
                for i, r in enumerate(filtered_revoked, 1)
            ]
        )
        if not export_rev_df.empty:
            buf_rev = BytesIO()
            export_rev_df.to_excel(buf_rev, index=False, engine="openpyxl")
            buf_rev.seek(0)
            st.download_button(
                label="导出当前筛选的已撤销名单 (Excel)",
                data=buf_rev.getvalue(),
                file_name=f"已撤销名单_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime=MIME_XLSX,
                key="admin_export_revoked",
            )

        with st.expander("▶ 按学号恢复为生效", expanded=False):
            st.caption("输入学号可将该条已撤销记录恢复为生效。")
            restore_sid_input = st.text_input(f"输入要恢复的{LABEL_STUDENT_ID}", key="restore_student_id", placeholder=LABEL_STUDENT_ID)
            if st.button("恢复为生效", key="admin_restore_btn") and restore_sid_input:
                ok_restore, err_restore = validate_student_id(restore_sid_input)
                if not ok_restore:
                    st.error(err_restore or MSG_ENTER_VALID_SID)
                else:
                    try:
                        sid_restore = clean_student_id(restore_sid_input.strip())
                        rec = db.query(Blacklist).filter(
                            Blacklist.status == 0, Blacklist.student_id == sid_restore
                        ).first()
                        if not rec:
                            st.error(MSG_NOT_FOUND_REVOKED)
                        else:
                            with st.spinner("正在恢复..."):
                                rec.status = 1
                                db.commit()
                                _log_action(
                                    AUDIT_RESTORE,
                                    target=sid_restore[:16],
                                    details=f"恢复：{rec.name} {sid_restore[:8]}***",
                                )
                            st.success("已恢复为生效。")
                            st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("恢复失败，" + MSG_TRY_AGAIN)


def _render_management(db):
    """名单管理：按「录入 → 生效名单 → 已撤销」分 Tab；批量比对与教师端一致，由教师端完成。"""
    st.caption("录入名单后，在生效/已撤销名单中查看与维护；批量比对请使用教师端。")
    tab_rec, tab_eff, tab_rev = st.tabs(["录入", "生效名单", "已撤销名单"])
    with tab_rec:
        _render_import_section(db)
        _render_manual_add_section(db)
    with tab_eff:
        _render_effective_list_section(db)
        _render_edit_form_section()
    with tab_rev:
        _render_revoked_section(db)


def _render_system(db):
    """Tab 3: 系统维护 - 审计日志（可按操作人/类型/日期筛选）、数据库下载。"""
    st.subheader("审计日志")
    st.caption("可按操作人、操作类型、日期单独或组合筛选，留空或选「全部」表示不限制。")

    try:
        # 获取所有出现过的操作人（用于下拉）
        operator_names = [
            r[0] for r in db.query(AuditLog.operator_name).distinct().order_by(AuditLog.operator_name).all()
        ]
    except Exception:
        operator_names = []

    audit_type_display_options = ["全部"] + [AUDIT_TYPE_NAMES.get(t, t) for t in AUDIT_ACTION_TYPES]
    audit_name_to_code = {v: k for k, v in AUDIT_TYPE_NAMES.items()}
    col1, col2, col3 = st.columns(3)
    with col1:
        filter_operator = st.selectbox(
            "操作人",
            ["全部"] + operator_names,
            key="audit_filter_operator",
        )
    with col2:
        filter_type_display = st.selectbox(
            "操作类型",
            audit_type_display_options,
            key="audit_filter_type",
        )
        filter_type = None if filter_type_display == "全部" or filter_type_display not in audit_name_to_code else audit_name_to_code[filter_type_display]
    with col3:
        use_date_filter = st.checkbox("按日期筛选", key="audit_use_date")
        filter_date = None
        if use_date_filter:
            filter_date = st.date_input("选择日期", key="audit_filter_date")

    try:
        with st.spinner("加载日志..."):
            q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
            if filter_operator != "全部":
                q = q.filter(AuditLog.operator_name == filter_operator)
            if filter_type:
                q = q.filter(AuditLog.action_type == filter_type)
            if use_date_filter and filter_date is not None:
                q = q.filter(func.date(AuditLog.timestamp) == str(filter_date))
            logs = q.limit(500).all()
            # 导出使用同一筛选条件但不做条数限制，导出当前筛选结果全部
            q_export = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
            if filter_operator != "全部":
                q_export = q_export.filter(AuditLog.operator_name == filter_operator)
            if filter_type:
                q_export = q_export.filter(AuditLog.action_type == filter_type)
            if use_date_filter and filter_date is not None:
                q_export = q_export.filter(func.date(AuditLog.timestamp) == str(filter_date))
            logs_export = q_export.all()
        if not logs:
            st.caption("暂无符合条件的审计日志。")
        else:
            log_df = pd.DataFrame(
                [
                    {
                        "ID": r.id,
                        "操作人": r.operator_name,
                        "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type),
                        "对象": r.target or "",
                        "详情": (r.details or "")[:100],
                        "时间": str(r.timestamp),
                    }
                    for r in logs
                ]
            )
            st.dataframe(log_df, use_container_width=True, hide_index=True)
            st.caption(
                f"表格展示 {len(logs)} 条（最多 500 条）；导出为当前筛选结果全部，共 {len(logs_export)} 条。"
            )
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_df_export = pd.DataFrame(
                [
                    {
                        "ID": r.id,
                        "操作人": r.operator_name,
                        "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type),
                        "对象": r.target or "",
                        "详情": r.details or "",
                        "时间": str(r.timestamp),
                    }
                    for r in logs_export
                ]
            )
            xlsx_buf = BytesIO()
            log_df_export.to_excel(xlsx_buf, index=False, engine="openpyxl")
            xlsx_buf.seek(0)
            st.download_button(
                label="导出审计日志 (Excel)",
                data=xlsx_buf.getvalue(),
                file_name=f"审计日志_{stamp}.xlsx",
                mime=MIME_XLSX,
                key="audit_export_xlsx",
            )
    except Exception:
        st.error("加载审计日志失败，" + MSG_TRY_AGAIN)

    with st.expander("▶ 数据库备份与恢复", expanded=False):
        st.caption("下载当前数据库或上传备份文件覆盖恢复；恢复为危险操作，请谨慎。")
        st.subheader("数据库备份下载")
        try:
            db_bytes = get_db_file_bytes()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            st.download_button(
                label="下载当前数据库 (.db)",
                data=db_bytes,
                file_name=f"database_{stamp}.db",
                mime="application/octet-stream",
                key="admin_download_db",
            )
        except FileNotFoundError as e:
            st.error(str(e))
        except OSError as e:
            st.error(MSG_DB_READ_FAIL)

        st.divider()
        st.subheader("⚡️ 危险操作：数据恢复")
        restore_uploaded = st.file_uploader(
            "上传备份文件以覆盖当前数据库",
            type=["db"],
            key="admin_restore_upload",
        )
        if restore_uploaded:
            st.warning("此操作将**覆盖**当前数据库，所有未备份的修改将丢失。")
            restore_confirm_checked = st.checkbox(
                "我已知晓将覆盖当前数据，确认执行恢复",
                key="admin_restore_confirm_check",
            )
            if restore_confirm_checked:
                if st.button("🔴 确认恢复", key="admin_confirm_restore"):
                    try:
                        backup_bytes = restore_uploaded.read()
                        if not backup_bytes:
                            st.error(MSG_UPLOAD_EMPTY)
                        else:
                            with open(DATABASE_PATH, "wb") as f:
                                f.write(backup_bytes)
                            st.cache_resource.clear()
                            _log_action(AUDIT_BACKUP, target="数据恢复", details=f"从文件 {restore_uploaded.name} 恢复")
                            st.success(SUCCESS_DB_RESTORED)
                            st.balloons()
                            time.sleep(2)
                            st.rerun()
                    except OSError:
                        st.error(MSG_DB_RESTORE_FAIL)
                    except Exception:
                        st.error("恢复过程出错，" + MSG_TRY_AGAIN_OR_ADMIN)
            else:
                st.caption(CAPTION_CONFIRM_RESTORE_DB)


def _render_user_management(db):
    """用户管理：用户列表、新增用户、密码重置、启用/禁用。"""
    users = db.query(User).order_by(User.id).all()

    st.subheader("用户列表")
    st.caption("工号唯一；禁用后该账号无法登录。")
    if not users:
        st.caption(EMPTY_NO_USER)
    else:
        user_df = pd.DataFrame(
            [
                {
                    "ID": u.id,
                    "工号": u.username,
                    "姓名": u.full_name,
                    "角色": "管理员" if u.role == "admin" else "教师",
                    "状态": "正常" if u.is_active else "已禁用",
                }
                for u in users
            ]
        )
        st.dataframe(user_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("新增用户")
    with st.form("admin_add_user_form"):
        new_username = st.text_input("工号/登录名", key="new_username", max_chars=USERNAME_MAX_LEN)
        new_password = st.text_input("密码", type="password", key="new_password")
        new_full_name = st.text_input("真实姓名", key="new_full_name", max_chars=USERNAME_MAX_LEN)
        new_role = st.selectbox("角色", ["教师", "管理员"], key="new_role")
        if st.form_submit_button("添加用户"):
            uname = (new_username or "").strip()
            pwd = (new_password or "").strip()
            fname = (new_full_name or "").strip()
            if not uname or not pwd or not fname:
                st.error("请填写工号、密码和姓名。")
            elif len(pwd) < PASSWORD_MIN_LEN:
                st.error(f"密码至少 {PASSWORD_MIN_LEN} 位，请重新输入。")
            elif len(uname) > USERNAME_MAX_LEN:
                st.error(f"工号长度不能超过 {USERNAME_MAX_LEN} 个字符。")
            else:
                try:
                    with st.spinner("正在保存..."):
                        if db.query(User).filter(User.username == uname).first():
                            st.error("该工号已存在。")
                        else:
                            pwd_hash = bcrypt.hashpw(
                                pwd.encode("utf-8"), bcrypt.gensalt()
                            ).decode("utf-8")
                            u = User(
                                username=uname,
                                password_hash=pwd_hash,
                                full_name=fname,
                                role="admin" if new_role == "管理员" else "teacher",
                                is_active=True,
                            )
                            db.add(u)
                            db.commit()
                            _log_action(AUDIT_ADD, target=f"用户 {uname}", details=f"角色 {new_role}")
                            st.success("用户已添加。")  # 与 SUCCESS_ADDED 区分（用户 vs 名单记录）
                            st.rerun()
                except Exception:
                    db.rollback()
                    st.error("添加失败，" + MSG_TRY_AGAIN)

    st.divider()
    st.subheader("密码重置与启用/禁用")
    st.caption("选择用户后执行重置密码或切换账号状态；不能禁用当前登录账号。")
    if not users:
        st.caption(EMPTY_NO_USER)
    else:
        reset_options = [f"{u.username}（{u.full_name}）" for u in users]
        toggle_options = [f"{u.username}（{u.full_name}）— {'正常' if u.is_active else '已禁用'}" for u in users]

        rcol1, rcol2 = st.columns(2)
        with rcol1:
            st.caption("密码重置")
            reset_choice = st.selectbox("选择用户", reset_options, key="reset_user_choice", label_visibility="collapsed")
            reset_password = st.text_input("新密码", type="password", key="reset_password")
            if st.button("重置密码", key="admin_reset_pwd_btn"):
                if not (reset_password or "").strip():
                    st.error("请输入新密码。")
                elif len((reset_password or "").strip()) < PASSWORD_MIN_LEN:
                    st.error(f"密码至少 {PASSWORD_MIN_LEN} 位，请重新输入。")
                else:
                    try:
                        idx = reset_options.index(reset_choice) if reset_choice in reset_options else -1
                        if idx < 0:
                            st.error("未找到该用户。")
                        else:
                            user = users[idx]
                            with st.spinner("正在重置..."):
                                user.password_hash = bcrypt.hashpw(
                                    (reset_password or "").strip().encode("utf-8"), bcrypt.gensalt()
                                ).decode("utf-8")
                                db.commit()
                                _log_action(AUDIT_ADD, target=f"密码重置 {user.username}", details="")
                                st.success(SUCCESS_PWD_RESET)
                                st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("重置失败，" + MSG_TRY_AGAIN)

        with rcol2:
            st.caption("启用/禁用账号")
            toggle_choice = st.selectbox("选择用户", toggle_options, key="toggle_user_choice", label_visibility="collapsed")
            if st.button("切换状态", key="admin_toggle_btn"):
                try:
                    idx = toggle_options.index(toggle_choice) if toggle_choice in toggle_options else -1
                    if idx < 0:
                        st.error("未找到该用户。")
                    else:
                        target_user = users[idx]
                        if target_user.username == st.session_state.get(SESSION_KEY_USERNAME):
                            st.warning("不能禁用当前登录账号。")
                        else:
                            with st.spinner("正在更新..."):
                                target_user.is_active = not target_user.is_active
                                db.commit()
                                status = "启用" if target_user.is_active else "禁用"
                                _log_action(AUDIT_ADD, target=f"账号{status} {target_user.username}", details="")
                                st.success(f"已{status} {target_user.username}。")
                                st.rerun()
                except Exception:
                    db.rollback()
                    st.error("操作失败，" + MSG_TRY_AGAIN)


# 管理员侧边栏导航选项（与下方 NAV_* 索引对应）
# 侧边栏导航：统一用 › 前缀，简洁耐看、不依赖 emoji 渲染
ADMIN_NAV_OPTIONS = ["› 仪表盘", "› 名单管理", "› 系统维护", "› 用户管理"]
NAV_DASHBOARD, NAV_MANAGEMENT, NAV_SYSTEM, NAV_USER = 0, 1, 2, 3

# 仅用 radio 的 key 作为唯一数据源，避免双写导致要点两次
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
    """管理员页主入口：根据侧边栏选中项渲染对应内容（仪表盘 / 名单管理 / 系统维护 / 用户管理）。"""
    nav_index = _get_admin_nav_index()

    with db_session() as db:
        if nav_index == NAV_DASHBOARD:
            _render_dashboard(db)
        elif nav_index == NAV_MANAGEMENT:
            _render_management(db)
        elif nav_index == NAV_SYSTEM:
            _render_system(db)
        else:
            _render_user_management(db)
