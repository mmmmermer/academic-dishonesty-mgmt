"""
管理员名单管理：批量导入、手动新增、生效名单、已撤销名单、编辑。
"""
import logging
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from core.config import (
    AUDIT_ADD,
    AUDIT_DELETE,
    AUDIT_IMPORT,
    AUDIT_RESTORE,
    BATCH_IMPORT_COMMIT_EVERY,
    CAPTION_FILTER_BY_NAME_SID_MAJOR,
    EMPTY_NO_EFFECTIVE,
    EMPTY_NO_REVOKED,
    LABEL_INIT_LIST,
    LABEL_MAJOR,
    LABEL_NAME,
    LABEL_PUNISHMENT_DATE,
    LABEL_REASON,
    LABEL_STUDENT_ID,
    MIME_XLSX,
    MSG_CONFIRM_INIT_LIST,
    MSG_ENTER_VALID_SID,
    MSG_NOT_FOUND_EFFECTIVE,
    MSG_NOT_FOUND_REVOKED,
    MSG_TRY_AGAIN,
    MSG_TRY_AGAIN_OR_ADMIN,
    SUCCESS_ADDED,
    SUCCESS_IMPORT_DONE,
    SUCCESS_INIT_LIST,
    SUCCESS_SAVED,
)
from core.database import db_session
from core.models import Blacklist
from core.utils import (
    REQUIRED_EXCEL_COLUMNS,
    cell_str,
    clean_student_id,
    log_audit_action,
    parse_blacklist_excel,
    validate_student_id,
)
from views.components import (
    apply_blacklist_sort,
    build_blacklist_query,
    clamp_page,
    render_blacklist_export_button,
    render_blacklist_table,
    render_display_options,
    render_filter_inputs,
    render_pagination,
)

logger = logging.getLogger(__name__)


# ── 批量导入 ──────────────────────────────────────────────


def _render_import_last_result():
    if not st.session_state.get("admin_last_import_result"):
        return
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
                label="下载跳过行列表 (Excel)", data=buf_skip.getvalue(),
                file_name="导入跳过行.xlsx", mime=MIME_XLSX, key="admin_import_skip_download",
            )
        if st.button("关闭", key="admin_close_last_import"):
            del st.session_state["admin_last_import_result"]
            st.rerun()


def _parse_import_row(row):
    sid = str(row["学号"]).strip() if pd.notna(row["学号"]) else ""
    if not sid:
        return None
    name = str(row["姓名"]).strip() if pd.notna(row["姓名"]) else ""
    major = str(row["专业"]).strip() if pd.notna(row["专业"]) else None
    reason = str(row["原因"]).strip() if pd.notna(row["原因"]) else None
    punishment_date = None
    raw_date = row.get("处分时间")
    if pd.notna(raw_date):
        try:
            punishment_date = pd.to_datetime(raw_date).date()
        except Exception:
            pass
    return sid, name, major, reason, punishment_date


def _build_skipped_row(row, idx):
    return {"行号": idx + 2, **{c: cell_str(row.get(c)) for c in REQUIRED_EXCEL_COLUMNS}}


def _upsert_one_blacklist(db, sid, name, major, reason, punishment_date):
    existing = db.query(Blacklist).filter(Blacklist.student_id == sid).first()
    if existing:
        existing.name = name or existing.name
        existing.major = major if major else existing.major
        existing.reason = reason if reason else existing.reason
        if punishment_date:
            existing.punishment_date = punishment_date
        existing.status = 1
        return "updated"
    rec = Blacklist(name=name, student_id=sid, major=major or None, reason=reason or None, punishment_date=punishment_date, status=1)
    db.add(rec)
    return "imported"


def _run_batch_import(db, df, filename):
    imported = updated = skipped = batch_counter = 0
    skipped_rows = []
    try:
        for idx, row in df.iterrows():
            parsed = _parse_import_row(row)
            if parsed is None:
                skipped += 1
                skipped_rows.append(_build_skipped_row(row, idx))
                continue
            sid, name, major, reason, punishment_date = parsed
            action = _upsert_one_blacklist(db, sid, name, major, reason, punishment_date)
            if action == "imported":
                imported += 1
            else:
                updated += 1
            batch_counter += 1
            if batch_counter >= BATCH_IMPORT_COMMIT_EVERY:
                db.commit()
                batch_counter = 0
        if batch_counter > 0:
            db.commit()
        log_audit_action(
            AUDIT_IMPORT, target=filename,
            details=f"新增 {imported} 条，更新 {updated} 条" + (f"，跳过 {skipped} 行" if skipped else ""),
        )
        return {"imported": imported, "updated": updated, "skipped": skipped, "skipped_rows": skipped_rows}
    except Exception:
        db.rollback()
        return None


def _handle_import_confirm(db):
    df = st.session_state.get("admin_import_df")
    filename = st.session_state.get("admin_import_filename", "")
    if df is None:
        return False
    with st.spinner("正在导入..."):
        result = _run_batch_import(db, df, filename)
    if result is None:
        st.error("导入失败，已成功导入部分数据；后续数据出错，请检查 Excel 格式（需包含：姓名、学号、专业、原因、处分时间）。")
        return False
    for key in ("admin_import_df", "admin_import_filename"):
        if key in st.session_state:
            del st.session_state[key]
    st.session_state["admin_last_import_result"] = result
    logger.info("批量导入完成 imported=%s updated=%s skipped=%s", result["imported"], result["updated"], result.get("skipped", 0))
    st.success(SUCCESS_IMPORT_DONE)
    st.balloons()
    st.rerun()


def _render_import_section(db):
    st.subheader("批量导入")
    _render_import_last_result()
    uploaded = st.file_uploader("上传 Excel (.xlsx / .xls)", type=["xlsx", "xls"], key="admin_import_file")
    if uploaded and st.session_state.get("admin_import_filename") != uploaded.name:
        try:
            df_parsed = parse_blacklist_excel(uploaded)
            st.session_state["admin_import_df"] = df_parsed
            st.session_state["admin_import_filename"] = uploaded.name
        except ValueError as e:
            st.error(str(e))
    if st.session_state.get("admin_import_df") is not None and st.session_state.get("admin_import_filename"):
        st.caption("以下为解析结果前 10 行预览，确认无误后点击「开始导入」。")
        st.dataframe(st.session_state["admin_import_df"].head(10), use_container_width=True, hide_index=True)
        if st.button("开始导入", key="admin_import_btn"):
            _handle_import_confirm(db)


# ── 手动新增 ──────────────────────────────────────────────


def _try_manual_add(db, add_name, add_student_id, add_major, add_reason, add_date):
    if not add_name or not add_student_id:
        st.error(f"请填写{LABEL_NAME}和{LABEL_STUDENT_ID}。")
        return False
    ok_sid, err_sid = validate_student_id(add_student_id)
    if not ok_sid:
        st.error(err_sid or MSG_ENTER_VALID_SID)
        return False
    try:
        with st.spinner("正在保存..."):
            sid_clean = clean_student_id(add_student_id)
            if db.query(Blacklist).filter(Blacklist.student_id == sid_clean).first():
                st.error(f"该{LABEL_STUDENT_ID}已存在。")
                return False
            rec = Blacklist(
                name=add_name.strip(), student_id=sid_clean,
                major=add_major.strip() or None, reason=add_reason.strip() or None,
                punishment_date=add_date, status=1,
            )
            db.add(rec)
            db.commit()
            log_audit_action(AUDIT_ADD, target=add_name, details=f"学号 {sid_clean[:8]}***")
            st.success(SUCCESS_ADDED)
            return True
    except Exception:
        db.rollback()
        st.error("添加失败，" + MSG_TRY_AGAIN_OR_ADMIN)
        return False


def _render_manual_add_section(db):
    st.divider()
    st.subheader("手动新增")
    with st.form("admin_add_form"):
        add_name = st.text_input(LABEL_NAME, key="add_name")
        add_student_id = st.text_input(LABEL_STUDENT_ID, key="add_student_id")
        add_major = st.text_input(LABEL_MAJOR, key="add_major")
        add_reason = st.text_area(LABEL_REASON, key="add_reason")
        add_date = st.date_input(LABEL_PUNISHMENT_DATE, key="add_date")
        if st.form_submit_button("添加") and _try_manual_add(db, add_name, add_student_id, add_major, add_reason, add_date):
            st.rerun()


# ── 生效名单 ──────────────────────────────────────────────


def _render_effective_delete_block(db):
    del_sid_input = st.text_input(f"输入要删除的{LABEL_STUDENT_ID}", key="del_student_id", placeholder=LABEL_STUDENT_ID)
    if not (st.button("软删除（设为已撤销）", key="admin_del_btn") and del_sid_input):
        return
    ok_del, err_del = validate_student_id(del_sid_input)
    if not ok_del:
        st.error(err_del or MSG_ENTER_VALID_SID)
        return
    try:
        sid_clean = clean_student_id(del_sid_input.strip())
        rec = db.query(Blacklist).filter(Blacklist.status == 1, Blacklist.student_id == sid_clean).first()
        if not rec:
            st.error(MSG_NOT_FOUND_EFFECTIVE)
            return
        with st.spinner("正在更新..."):
            rec.status = 0
            db.commit()
            log_audit_action(AUDIT_DELETE, target=sid_clean[:16], details=f"软删除：{rec.name} {sid_clean[:8]}***")
        logger.info("名单软删除学号=%s", sid_clean[:16])
        st.success("已软删除。")
        st.rerun()
    except Exception:
        db.rollback()
        st.error("删除操作失败，" + MSG_TRY_AGAIN)


def _render_effective_init_block(db):
    st.caption("将所有生效记录设为已撤销，清空生效名单。请谨慎操作。")
    if not st.session_state.get("admin_show_init_confirm"):
        if st.button(LABEL_INIT_LIST, key="admin_init_list_btn"):
            st.session_state["admin_show_init_confirm"] = True
            st.rerun()
        return
    st.warning(MSG_CONFIRM_INIT_LIST)
    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button("确认初始化", key="admin_init_confirm_btn"):
            try:
                with st.spinner("正在初始化..."):
                    n = db.query(Blacklist).filter(Blacklist.status == 1).update({Blacklist.status: 0})
                    db.commit()
                    log_audit_action(AUDIT_DELETE, target=LABEL_INIT_LIST, details=f"共 {n} 条生效记录设为已撤销")
                logger.info("名单初始化 生效转撤销条数=%s", n)
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


def _render_effective_edit_block(db):
    edit_sid_input = st.text_input(f"输入要编辑的{LABEL_STUDENT_ID}", key="edit_student_id", placeholder=LABEL_STUDENT_ID)
    if not (st.button("编辑", key="admin_edit_btn") and edit_sid_input):
        return
    ok_edit, err_edit = validate_student_id(edit_sid_input)
    if not ok_edit:
        st.error(err_edit or MSG_ENTER_VALID_SID)
        return
    try:
        sid_edit = clean_student_id(edit_sid_input.strip())
        rec = db.query(Blacklist).filter(Blacklist.status == 1, Blacklist.student_id == sid_edit).first()
        if not rec:
            st.error(MSG_NOT_FOUND_EFFECTIVE)
            return
        st.session_state["admin_edit_id"] = rec.id
        st.rerun()
    except Exception:
        db.rollback()
        st.error("查找记录失败，" + MSG_TRY_AGAIN)


def _render_effective_actions_expander(db):
    with st.expander("▶ 按学号删除、初始化名单、编辑", expanded=False):
        st.caption("按学号软删除、一键初始化生效名单、按学号进入编辑。")
        _render_effective_delete_block(db)
        st.divider()
        _render_effective_init_block(db)
        st.divider()
        _render_effective_edit_block(db)


def _render_effective_list_section(db):
    st.subheader("生效名单")
    st.caption(CAPTION_FILTER_BY_NAME_SID_MAJOR)
    fn, fs, fm = render_filter_inputs("admin_effective")
    page_size, sort_key, sort_asc = render_display_options("admin_effective")
    base = build_blacklist_query(db, status=1, name_filter=fn, sid_filter=fs, major_filter=fm)
    total = base.count()
    if total == 0:
        st.caption(EMPTY_NO_EFFECTIVE)
        _render_effective_actions_expander(db)
        return
    ordered = apply_blacklist_sort(base, sort_key, sort_asc)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = clamp_page("admin_effective_page", total_pages)
    page_records = ordered.offset(page * page_size).limit(page_size).all()
    render_blacklist_table(page_records, page_size, page)
    render_pagination("admin_effective_page", page, total_pages, total, len(page_records))
    render_blacklist_export_button(db, 1, fn, fs, fm, sort_key, sort_asc, total, "生效名单", "admin_export_effective")
    _render_effective_actions_expander(db)


# ── 编辑表单 ──────────────────────────────────────────────


def _clear_edit_id():
    if "admin_edit_id" in st.session_state:
        del st.session_state["admin_edit_id"]


def _try_save_edit_form(edit_db, rec, edit_id, edit_name, edit_major, edit_reason, edit_date):
    try:
        rec.name = (edit_name or "").strip() or rec.name
        rec.major = (edit_major or "").strip() or None
        rec.reason = (edit_reason or "").strip() or None
        rec.punishment_date = edit_date
        edit_db.commit()
        log_audit_action(AUDIT_ADD, target=f"编辑记录 {edit_id}", details=f"{rec.name} {rec.student_id[:8]}***")
        _clear_edit_id()
        st.success(SUCCESS_SAVED)
        st.rerun()
    except Exception:
        edit_db.rollback()
        st.error("保存失败，" + MSG_TRY_AGAIN)
        return False


def _render_edit_form_section():
    if not st.session_state.get("admin_edit_id"):
        return
    edit_id = st.session_state["admin_edit_id"]
    with db_session() as edit_db:
        rec = edit_db.query(Blacklist).filter(Blacklist.id == edit_id).first()
        if not rec or rec.status != 1:
            _clear_edit_id()
            st.rerun()
            return
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
                _try_save_edit_form(edit_db, rec, edit_id, edit_name, edit_major, edit_reason, edit_date)
            elif submit_cancel:
                _clear_edit_id()
                st.rerun()


# ── 已撤销名单 ──────────────────────────────────────────


def _render_revoked_restore_expander(db):
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
                    rec = db.query(Blacklist).filter(Blacklist.status == 0, Blacklist.student_id == sid_restore).first()
                    if not rec:
                        st.error(MSG_NOT_FOUND_REVOKED)
                    else:
                        with st.spinner("正在恢复..."):
                            rec.status = 1
                            db.commit()
                            log_audit_action(AUDIT_RESTORE, target=sid_restore[:16], details=f"恢复：{rec.name} {sid_restore[:8]}***")
                        logger.info("名单恢复为生效 学号=%s", sid_restore[:16])
                        st.success("已恢复为生效。")
                        st.rerun()
                except Exception:
                    db.rollback()
                    st.error("恢复失败，" + MSG_TRY_AGAIN)


def _render_revoked_section(db):
    st.subheader("已撤销名单")
    st.caption(CAPTION_FILTER_BY_NAME_SID_MAJOR)
    rn, rs, rm = render_filter_inputs("admin_revoked")
    page_size, sort_key, sort_asc = render_display_options("admin_revoked")
    base = build_blacklist_query(db, status=0, name_filter=rn, sid_filter=rs, major_filter=rm)
    total = base.count()
    if total == 0:
        st.caption(EMPTY_NO_REVOKED)
        _render_revoked_restore_expander(db)
        return
    ordered = apply_blacklist_sort(base, sort_key, sort_asc)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = clamp_page("admin_revoked_page", total_pages)
    page_records = ordered.offset(page * page_size).limit(page_size).all()
    render_blacklist_table(page_records, page_size, page)
    render_pagination("admin_revoked_page", page, total_pages, total, len(page_records))
    render_blacklist_export_button(db, 0, rn, rs, rm, sort_key, sort_asc, total, "已撤销名单", "admin_export_revoked")
    _render_revoked_restore_expander(db)


# ── 主入口 ──────────────────────────────────────────────


def _render_management(db):
    """名单管理：按「录入 → 生效名单 → 已撤销」分 Tab。"""
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
