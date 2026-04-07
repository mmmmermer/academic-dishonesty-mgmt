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
from core.audit_logger import log_audit_action
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
    except Exception as e:
        logger.exception("获取审计日志发生异常")
        return None, 0


def _fetch_audit_logs_export_batched(db, filter_operator, filter_type, use_date_filter, filter_date):
    q = _audit_log_export_query(db, filter_operator, filter_type, use_date_filter, filter_date)
    rows = []
    stream_query = q.limit(EXPORT_MAX_ROWS)
    if EXPORT_BATCH_SIZE > 0:
        stream_query = stream_query.yield_per(EXPORT_BATCH_SIZE)
    for row in stream_query:
        rows.append(row)
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

    # 惰性导出：先显示"准备导出"按钮，点击后查询并缓存，再显示下载按钮
    current_hash = f"audit_{filter_operator}_{filter_type}_{use_date_filter}_{filter_date}"
    cache_hash_key = "audit_export_hash"
    cache_data_key = "audit_export_data"

    if st.session_state.get(cache_hash_key) != current_hash or st.session_state.get(cache_data_key) is None:
        if st.button(f"⚡ 准备导出审计日志（共 {total_export} 条）", use_container_width=True, key="audit_prep_export"):
            with st.spinner(SPINNER_EXPORT):
                logs_export = _fetch_audit_logs_export_batched(db, filter_operator, filter_type, use_date_filter, filter_date)
            if not logs_export:
                st.caption("无可导出的日志。")
                return
            if len(logs_export) >= EXPORT_MAX_ROWS:
                st.caption(f"筛选结果超过 {EXPORT_MAX_ROWS} 条，仅导出前 {EXPORT_MAX_ROWS} 条。")
            log_df_export = pd.DataFrame(
                [{"ID": r.id, "操作人": r.operator_name, "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type), "对象": r.target or "", "详情": r.details or "", "时间": str(r.timestamp)} for r in logs_export]
            )
            xlsx_buf = BytesIO()
            log_df_export.to_excel(xlsx_buf, index=False, engine="openpyxl")
            st.session_state[cache_hash_key] = current_hash
            st.session_state[cache_data_key] = xlsx_buf.getvalue()
            st.rerun()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label="⬇️ 导出审计日志 (Excel)",
            data=st.session_state[cache_data_key],
            file_name=f"审计日志_{stamp}.xlsx",
            mime=MIME_XLSX,
            key="audit_export_xlsx",
            use_container_width=True
        )


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


def _render_system(db):
    """系统维护：审计日志。"""
    _render_audit_log_section(db)
    with st.expander("▶ 数据库备份与灾备", expanded=False):
        st.info("数据管理已交由 PG 灾备中心流水，本系统不再提供单机版数据库本地热下载与覆盖功能。请利用 pg_dump 执行外围自动化回演与灾备。")
