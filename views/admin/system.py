"""
管理员系统维护：审计日志（筛选/导出）、数据库备份与恢复。
"""
import logging
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from sqlalchemy import func

from core.config import (
    AUDIT_ACTION_TYPES,
    AUDIT_BACKUP,
    AUDIT_TYPE_NAMES,
    CAPTION_CONFIRM_RESTORE_DB,
    MIME_XLSX,
    MSG_DB_READ_FAIL,
    MSG_DB_RESTORE_FAIL,
    MSG_TRY_AGAIN,
    MSG_TRY_AGAIN_OR_ADMIN,
    MSG_UPLOAD_EMPTY,
    SUCCESS_DB_RESTORED,
)
from core.database import IS_SQLITE, db_session
from core.models import AuditLog
from core.utils import DATABASE_PATH, get_db_file_bytes, log_audit_action
from views.components import EXPORT_BATCH_SIZE, EXPORT_MAX_ROWS, SPINNER_EXPORT

logger = logging.getLogger(__name__)


def _get_audit_operator_names(db):
    try:
        return [r[0] for r in db.query(AuditLog.operator_name).distinct().order_by(AuditLog.operator_name).all()]
    except Exception:
        return []


def _audit_log_export_query(db, filter_operator, filter_type, use_date_filter, filter_date):
    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if filter_operator != "全部":
        q = q.filter(AuditLog.operator_name == filter_operator)
    if filter_type:
        q = q.filter(AuditLog.action_type == filter_type)
    if use_date_filter and filter_date is not None:
        q = q.filter(func.date(AuditLog.timestamp) == str(filter_date))
    return q


def _fetch_audit_logs(db, filter_operator, filter_type, use_date_filter, filter_date):
    """单次查询获取展示用记录（最多 501 条）和总数，避免两次独立查询。"""
    try:
        base = _audit_log_export_query(db, filter_operator, filter_type, use_date_filter, filter_date)
        # 取 501 条：若恰好 501 条说明总数 > 500，需要 count()；否则总数即为结果数
        logs = base.limit(501).all()
        if len(logs) <= 500:
            return logs, len(logs)
        total_export = base.count()
        return logs[:500], total_export
    except Exception:
        return None, 0


def _fetch_audit_logs_export_batched(db, filter_operator, filter_type, use_date_filter, filter_date):
    q = _audit_log_export_query(db, filter_operator, filter_type, use_date_filter, filter_date)
    rows = []
    for offset in range(0, EXPORT_MAX_ROWS, EXPORT_BATCH_SIZE):
        batch = q.offset(offset).limit(EXPORT_BATCH_SIZE).all()
        if not batch:
            break
        rows.extend(batch)
    return rows


def _render_audit_log_display(logs, total_export, db, filter_operator, filter_type, use_date_filter, filter_date):
    if not logs:
        st.caption("暂无符合条件的审计日志。")
        return
    log_df = pd.DataFrame(
        [{"ID": r.id, "操作人": r.operator_name, "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type), "对象": r.target or "", "详情": (r.details or "")[:100], "时间": str(r.timestamp)} for r in logs]
    )
    st.dataframe(log_df, use_container_width=True, hide_index=True)
    st.caption(f"表格展示 {len(logs)} 条（最多 500 条）；导出为当前筛选结果，共 {total_export} 条（最多导出 {EXPORT_MAX_ROWS} 条）。")
    with st.spinner(SPINNER_EXPORT):
        logs_export = _fetch_audit_logs_export_batched(db, filter_operator, filter_type, use_date_filter, filter_date)
    if not logs_export:
        return
    if len(logs_export) >= EXPORT_MAX_ROWS:
        st.caption(f"筛选结果超过 {EXPORT_MAX_ROWS} 条，仅导出前 {EXPORT_MAX_ROWS} 条。")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_df_export = pd.DataFrame(
        [{"ID": r.id, "操作人": r.operator_name, "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type), "对象": r.target or "", "详情": r.details or "", "时间": str(r.timestamp)} for r in logs_export]
    )
    xlsx_buf = BytesIO()
    log_df_export.to_excel(xlsx_buf, index=False, engine="openpyxl")
    xlsx_buf.seek(0)
    st.download_button(label="导出审计日志 (Excel)", data=xlsx_buf.getvalue(), file_name=f"审计日志_{stamp}.xlsx", mime=MIME_XLSX, key="audit_export_xlsx")


def _render_audit_log_section(db):
    st.subheader("审计日志")
    st.caption("可按操作人、操作类型、日期单独或组合筛选，留空或选「全部」表示不限制。")
    operator_names = _get_audit_operator_names(db)
    audit_type_display_options = ["全部"] + [AUDIT_TYPE_NAMES.get(t, t) for t in AUDIT_ACTION_TYPES]
    audit_name_to_code = {v: k for k, v in AUDIT_TYPE_NAMES.items()}
    col1, col2, col3 = st.columns(3)
    with col1:
        filter_operator = st.selectbox("操作人", ["全部"] + operator_names, key="audit_filter_operator")
    with col2:
        filter_type_display = st.selectbox("操作类型", audit_type_display_options, key="audit_filter_type")
        filter_type = None if filter_type_display == "全部" or filter_type_display not in audit_name_to_code else audit_name_to_code[filter_type_display]
    with col3:
        use_date_filter = st.checkbox("按日期筛选", key="audit_use_date")
        filter_date = st.date_input("选择日期", key="audit_filter_date") if use_date_filter else None
    try:
        with st.spinner("加载日志..."):
            logs, total_export = _fetch_audit_logs(db, filter_operator, filter_type, use_date_filter, filter_date)
        if logs is None:
            st.error("加载审计日志失败，" + MSG_TRY_AGAIN)
        else:
            _render_audit_log_display(logs, total_export, db, filter_operator, filter_type, use_date_filter, filter_date)
    except Exception:
        st.error("加载审计日志失败，" + MSG_TRY_AGAIN)


def _render_db_backup_section():
    st.subheader("数据库备份下载")
    try:
        db_bytes = get_db_file_bytes()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(label="下载当前数据库 (.db)", data=db_bytes, file_name=f"database_{stamp}.db", mime="application/octet-stream", key="admin_download_db")
    except NotImplementedError:
        st.info("当前使用 MySQL/PostgreSQL，不支持在此下载 .db 文件。请使用 mysqldump 或 pg_dump 在服务器上备份。")
    except FileNotFoundError as e:
        st.error(str(e))
    except OSError:
        st.error(MSG_DB_READ_FAIL)


def _render_db_restore_section():
    st.subheader("⚡️ 危险操作：数据恢复")
    if not IS_SQLITE:
        st.info("当前使用 MySQL/PostgreSQL，不支持在此上传 .db 恢复。请使用 mysql/pg 客户端或备份工具从备份恢复。")
        return
    restore_uploaded = st.file_uploader("上传备份文件以覆盖当前数据库", type=["db"], key="admin_restore_upload")
    if not restore_uploaded:
        return
    st.warning("此操作将**覆盖**当前数据库，所有未备份的修改将丢失。")
    restore_confirm_checked = st.checkbox("我已知晓将覆盖当前数据，确认执行恢复", key="admin_restore_confirm_check")
    if not restore_confirm_checked:
        st.caption(CAPTION_CONFIRM_RESTORE_DB)
        return
    if not st.button("🔴 确认恢复", key="admin_confirm_restore"):
        return
    try:
        backup_bytes = restore_uploaded.read()
        if not backup_bytes:
            st.error(MSG_UPLOAD_EMPTY)
        else:
            with open(DATABASE_PATH, "wb") as f:
                f.write(backup_bytes)
            st.cache_resource.clear()
            st.cache_data.clear()
            log_audit_action(AUDIT_BACKUP, target="数据恢复", details=f"从文件 {restore_uploaded.name} 恢复")
            logger.info("数据库恢复完成 文件=%s", restore_uploaded.name)
            st.success(SUCCESS_DB_RESTORED)
            st.caption("数据已写入。若未看到更新请刷新或重启应用以使连接加载新数据。")
            st.balloons()
            time.sleep(2)
            st.rerun()
    except OSError:
        st.error(MSG_DB_RESTORE_FAIL)
    except Exception:
        st.error("恢复过程出错，" + MSG_TRY_AGAIN_OR_ADMIN)


def _render_system(db):
    """系统维护：审计日志 + 数据库备份恢复。"""
    _render_audit_log_section(db)
    with st.expander("▶ 数据库备份与恢复", expanded=False):
        st.caption("下载当前数据库或上传备份文件覆盖恢复；恢复为危险操作，请谨慎。")
        _render_db_backup_section()
        st.divider()
        _render_db_restore_section()
