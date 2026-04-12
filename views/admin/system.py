"""
管理员系统维护：审计日志（统计概览/筛选/详情展开/导出）、数据库备份与恢复。
"""
import logging
import time
from datetime import datetime, timedelta
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


def _audit_log_export_query(db, filter_operator, filter_type, date_start=None, date_end=None):
    """构建审计日志查询，支持日期范围筛选。"""
    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if filter_operator != "全部":
        q = q.filter(AuditLog.operator_name == filter_operator)
    if filter_type:
        q = q.filter(AuditLog.action_type == filter_type)
    if date_start is not None:
        q = q.filter(func.date(AuditLog.timestamp) >= str(date_start))
    if date_end is not None:
        q = q.filter(func.date(AuditLog.timestamp) <= str(date_end))
    return q


def _fetch_audit_logs(db, filter_operator, filter_type, date_start=None, date_end=None):
    """单次查询获取展示用记录（最多 501 条）和总数，避免两次独立查询。"""
    try:
        base = _audit_log_export_query(db, filter_operator, filter_type, date_start, date_end)
        # 取 501 条：若恰好 501 条说明总数 > 500，需要 count()；否则总数即为结果数
        logs = base.limit(501).all()
        if len(logs) <= 500:
            return logs, len(logs)
        total_export = base.count()
        return logs[:500], total_export
    except Exception as e:
        logger.exception("获取审计日志发生异常")
        return None, 0


def _fetch_audit_logs_export_batched(db, filter_operator, filter_type, date_start=None, date_end=None):
    q = _audit_log_export_query(db, filter_operator, filter_type, date_start, date_end)
    rows = []
    stream_query = q.limit(EXPORT_MAX_ROWS)
    if EXPORT_BATCH_SIZE > 0:
        stream_query = stream_query.yield_per(EXPORT_BATCH_SIZE)
    for row in stream_query:
        rows.append(row)
    return rows



def _render_audit_log_display(logs, total_export, db, filter_operator, filter_type, date_start, date_end):
    if not logs:
        st.caption("暂无符合条件的审计日志。")
        return
    log_df = pd.DataFrame(
        [{"ID": r.id, "操作人": r.operator_name, "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type), "对象": r.target or "", "详情": ((r.details or "")[:200] + "…" if len(r.details or "") > 200 else (r.details or "")), "时间": r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else ""} for r in logs]
    )

    # 支持选中行展开详情
    event = st.dataframe(
        log_df, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        key="audit_log_table_sel",
    )
    st.caption(f"表格展示 {len(logs)} 条（最多 500 条）；导出为当前筛选结果，共 {total_export} 条（最多导出 {EXPORT_MAX_ROWS} 条）。点击某行可查看完整详情。")

    # 选中行详情展开
    if hasattr(event, "selection") and hasattr(event.selection, "rows") and event.selection.rows:
        sel_idx = event.selection.rows[0]
        if sel_idx < len(logs):
            sel_log = logs[sel_idx]
            with st.container(border=True):
                st.markdown(f"#### 📝 日志详情 (ID: {sel_log.id})")
                dc1, dc2 = st.columns(2)
                with dc1:
                    st.markdown(f"**操作人**：{sel_log.operator_name}")
                    st.markdown(f"**操作类型**：{AUDIT_TYPE_NAMES.get(sel_log.action_type, sel_log.action_type)}")
                with dc2:
                    st.markdown(f"**操作对象**：{sel_log.target or '—'}")
                    st.markdown(f"**时间**：{sel_log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if sel_log.timestamp else '—'}")
                details = sel_log.details or ""
                if details:
                    st.markdown("**详情（全文）**：")
                    st.code(details, language=None)
                else:
                    st.caption("详情：无")

    # 惰性导出
    current_hash = f"audit_{filter_operator}_{filter_type}_{date_start}_{date_end}"
    cache_hash_key = "audit_export_hash"
    cache_data_key = "audit_export_data"

    if st.session_state.get(cache_hash_key) != current_hash or st.session_state.get(cache_data_key) is None:
        if st.button(f"⚡ 准备导出审计日志（共 {total_export} 条）", use_container_width=True, key="audit_prep_export"):
            with st.spinner(SPINNER_EXPORT):
                logs_export = _fetch_audit_logs_export_batched(db, filter_operator, filter_type, date_start, date_end)
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
    st.caption("可按操作人、操作类型、日期范围单独或组合筛选，留空或选「全部」表示不限制。")
    operator_names = _get_audit_operator_names(db)
    audit_type_display_options = ["全部"] + [AUDIT_TYPE_NAMES.get(t, t) for t in AUDIT_ACTION_TYPES]
    audit_name_to_code = {v: k for k, v in AUDIT_TYPE_NAMES.items()}
    col1, col2, col3 = st.columns(3)
    with col1:
        filter_operator = st.selectbox("操作人", ["全部"] + operator_names, key="audit_filter_operator")
    with col2:
        filter_type_display = st.selectbox("操作类型", audit_type_display_options, key="audit_filter_type")
        filter_type = None if filter_type_display == "全部" or filter_type_display not in audit_name_to_code else audit_name_to_code[filter_type_display]

    # ⑬ 日期范围筛选 + 快捷按钮
    with col3:
        use_date_filter = st.checkbox("按日期筛选", key="audit_use_date")

    date_start = None
    date_end = None
    if use_date_filter:
        # 快捷时间宏按钮
        today = datetime.now().date()
        qc1, qc2, qc3, qc4 = st.columns(4)
        with qc1:
            if st.button("📅 今天", key="audit_q_today", use_container_width=True):
                st.session_state["audit_date_start"] = today
                st.session_state["audit_date_end"] = today
                st.rerun()
        with qc2:
            if st.button("📅 近 7 天", key="audit_q_7d", use_container_width=True):
                st.session_state["audit_date_start"] = today - timedelta(days=7)
                st.session_state["audit_date_end"] = today
                st.rerun()
        with qc3:
            if st.button("📅 近 30 天", key="audit_q_30d", use_container_width=True):
                st.session_state["audit_date_start"] = today - timedelta(days=30)
                st.session_state["audit_date_end"] = today
                st.rerun()
        with qc4:
            if st.button("📅 全部", key="audit_q_all", use_container_width=True):
                st.session_state.pop("audit_date_start", None)
                st.session_state.pop("audit_date_end", None)
                st.session_state["audit_use_date"] = False
                st.rerun()

        dc1, dc2 = st.columns(2)
        with dc1:
            date_start = st.date_input(
                "起始日期",
                value=st.session_state.get("audit_date_start", today - timedelta(days=7)),
                key="audit_date_start_input",
            )
        with dc2:
            date_end = st.date_input(
                "结束日期",
                value=st.session_state.get("audit_date_end", today),
                key="audit_date_end_input",
            )
        # 同步到 session_state 供快捷按钮回写
        st.session_state["audit_date_start"] = date_start
        st.session_state["audit_date_end"] = date_end

    try:
        with st.spinner("加载日志..."):
            logs, total_export = _fetch_audit_logs(db, filter_operator, filter_type, date_start, date_end)
        if logs is None:
            st.error("加载审计日志失败，" + MSG_TRY_AGAIN)
        else:
            _render_audit_log_display(logs, total_export, db, filter_operator, filter_type, date_start, date_end)
    except Exception:
        st.error("加载审计日志失败，" + MSG_TRY_AGAIN)


def _render_audit_archive_section(db):
    """审计日志归档：导出旧日志并可选删除，防止数据库无限膨胀。"""
    with st.expander("▶ 审计日志归档清理", expanded=False):
        st.caption("导出并清理旧的审计日志，保持数据库精简。此操作本身会被记入审计日志。")

        today = datetime.now().date()
        keep_days = st.selectbox(
            "保留最近多少天的日志",
            options=[30, 60, 90, 180, 365],
            index=2,  # 默认 90 天
            key="archive_keep_days",
        )
        cutoff_date = today - timedelta(days=keep_days)

        try:
            old_count = db.query(func.count(AuditLog.id)).filter(
                func.date(AuditLog.timestamp) < str(cutoff_date)
            ).scalar() or 0
        except Exception:
            st.error("查询失败，请稍后重试")
            return

        if old_count == 0:
            st.success(f"✅ 无需清理：{cutoff_date} 之前没有旧日志")
            return

        st.info(f"📊 {cutoff_date} 之前共有 **{old_count}** 条旧日志可归档")

        ac1, ac2 = st.columns(2)
        with ac1:
            if st.button(f"📦 导出 {old_count} 条旧日志", use_container_width=True, key="btn_archive_export"):
                with st.spinner("正在导出..."):
                    old_logs = db.query(AuditLog).filter(
                        func.date(AuditLog.timestamp) < str(cutoff_date)
                    ).order_by(AuditLog.timestamp.desc()).limit(EXPORT_MAX_ROWS).all()
                    if old_logs:
                        df = pd.DataFrame([{
                            "ID": r.id,
                            "操作人": r.operator_name,
                            "类型": AUDIT_TYPE_NAMES.get(r.action_type, r.action_type),
                            "对象": r.target or "",
                            "详情": r.details or "",
                            "时间": str(r.timestamp),
                        } for r in old_logs])
                        buf = BytesIO()
                        df.to_excel(buf, index=False, engine="openpyxl")
                        st.session_state["_archive_export_data"] = buf.getvalue()
                        st.rerun()

        if st.session_state.get("_archive_export_data"):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            st.download_button(
                "⬇️ 下载归档文件",
                data=st.session_state["_archive_export_data"],
                file_name=f"审计日志归档_{stamp}.xlsx",
                mime=MIME_XLSX,
                key="archive_dl",
                use_container_width=True,
            )

        with ac2:
            if st.button(f"🗑️ 删除 {old_count} 条旧日志", use_container_width=True, key="btn_archive_delete", type="primary"):
                st.session_state["_archive_confirm"] = True

        if st.session_state.get("_archive_confirm"):
            st.warning(f"⚠️ 即将永久删除 {cutoff_date} 之前的 {old_count} 条审计日志，此操作不可撤销！")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("✅ 确认删除", key="btn_archive_confirm_yes", use_container_width=True, type="primary"):
                    try:
                        deleted = db.query(AuditLog).filter(
                            func.date(AuditLog.timestamp) < str(cutoff_date)
                        ).delete(synchronize_session=False)
                        db.commit()
                        log_audit_action("audit_archive", target=f"清理{deleted}条", details=f"删除 {cutoff_date} 之前的 {deleted} 条审计日志")
                        st.session_state.pop("_archive_confirm", None)
                        st.session_state.pop("_archive_export_data", None)
                        st.session_state["_flash_success"] = f"已清理 {deleted} 条旧审计日志"
                        st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("删除失败，请稍后重试")
            with cc2:
                if st.button("❌ 取消", key="btn_archive_confirm_no", use_container_width=True):
                    st.session_state.pop("_archive_confirm", None)
                    st.rerun()


def _render_system(db):
    """系统维护：审计日志 + 归档清理。"""
    _render_audit_log_section(db)
    _render_audit_archive_section(db)
    with st.expander("▶ 数据库备份与灾备", expanded=False):
        st.info("数据管理已交由 PG 灾备中心流水，本系统不再提供单机版数据库本地热下载与覆盖功能。请利用 pg_dump 执行外围自动化回演与灾备。")
