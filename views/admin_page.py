"""
管理员页面：仪表盘、名单管理、系统维护、用户管理。
由 app 根据侧边栏导航（admin_nav_radio）渲染对应板块；名单支持分页、排序、每页条数、学号校验。
"""
import time
from datetime import datetime, timedelta
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
    AUDIT_QUERY_BATCH,
    AUDIT_RESTORE,
    BATCH_IMPORT_COMMIT_EVERY,
    LABEL_INIT_LIST,
    LABEL_PAGE_SIZE,
    LABEL_SORT_COLUMN,
    LABEL_DISPLAY_AND_SORT,
    LABEL_SORT_ORDER,
    LABEL_TABLE_SORT,
    LIST_PAGE_SIZE,
    LIST_PAGE_SIZE_OPTIONS,
    MIME_XLSX,
    MSG_ENTER_VALID_SID,
    PASSWORD_MIN_LEN,
    PLACEHOLDER_FILTER_EMPTY,
    SORT_ORDER_ASC,
    SORT_ORDER_OPTIONS,
    USERNAME_MAX_LEN,
)
from database import SessionLocal, db_session
from models import AuditLog, Blacklist, User
from utils import (
    DATABASE_PATH,
    clean_student_id,
    get_db_file_bytes,
    parse_batch_check_excel,
    parse_blacklist_excel,
    validate_student_id,
)


def _log_action(action_type: str, target: str = "", details: str = ""):
    """写入审计日志。"""
    with db_session() as db:
        try:
            name = st.session_state.get("user_name", "未知")
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
            st.caption("暂无记录。")
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

    # 近期操作统计：近 7 天按操作类型的次数
    st.subheader("近期操作统计")
    st.caption("近 7 天各类操作次数，便于了解使用情况。")
    try:
        since = datetime.now() - timedelta(days=7)
        log_rows = (
            db.query(AuditLog.action_type, func.count(AuditLog.id))
            .filter(AuditLog.timestamp >= since)
            .group_by(AuditLog.action_type)
            .all()
        )
        if not log_rows:
            st.caption("近 7 天暂无操作记录。")
        else:
            type_names = {"LOGIN": "登录", "QUERY_SINGLE": "单条查询", "QUERY_BATCH": "批量查询", "IMPORT": "批量导入", "ADD": "新增/编辑", "DELETE": "删除/初始化", "RESTORE": "恢复", "BACKUP": "备份/恢复"}
            stat_df = pd.DataFrame(
                [{"操作类型": type_names.get(aty, aty), "次数": cnt} for aty, cnt in log_rows]
            )
            st.dataframe(stat_df, use_container_width=True, hide_index=True)
    except Exception:
        st.caption("加载操作统计失败。")


def _render_import_section(db):
    """名单管理：批量导入 Excel 预览与导入。"""
    st.subheader("批量导入")
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
            last_imported = 0
            last_updated = 0
            batch_counter = 0
            try:
                with st.spinner("正在导入..."):
                    for _, row in df.iterrows():
                        sid = str(row["学号"]).strip() if pd.notna(row["学号"]) else ""
                        if not sid:
                            skipped += 1
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
                msg = f"导入成功：新增 {imported} 条，更新 {updated} 条。"
                if skipped:
                    msg += f" 跳过学号为空的行 {skipped} 条。"
                st.success(msg)
                st.balloons()
                st.rerun()
            except Exception:
                db.rollback()
                st.error(
                    f"导入失败，已成功导入 {last_imported} 条，更新 {last_updated} 条；后续数据出错，请检查 Excel 格式（需包含：姓名、学号、专业、原因、处分时间）。"
                )
    return


def _render_manual_add_section(db):
    """名单管理：手动新增单条记录。"""
    st.divider()
    st.subheader("手动新增")
    with st.form("admin_add_form"):
        add_name = st.text_input("姓名", key="add_name")
        add_student_id = st.text_input("学号", key="add_student_id")
        add_major = st.text_input("专业", key="add_major")
        add_reason = st.text_area("原因", key="add_reason")
        add_date = st.date_input("处分日期", key="add_date")
        if st.form_submit_button("添加"):
            if not add_name or not add_student_id:
                st.error("请填写姓名和学号。")
            else:
                ok_sid, err_sid = validate_student_id(add_student_id)
                if not ok_sid:
                    st.error(err_sid or MSG_ENTER_VALID_SID)
                else:
                    try:
                        with st.spinner("正在保存..."):
                            sid_clean = clean_student_id(add_student_id)
                            if db.query(Blacklist).filter(Blacklist.student_id == sid_clean).first():
                                st.error("该学号已存在。")
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
                                st.success("已添加。")
                                st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("添加失败，请检查学号是否重复或联系管理员。")
    return


def _render_effective_list_section(db):
    """名单管理：生效名单、筛选、分页、导出；删除/初始化/编辑收在 expander 内。"""
    st.subheader("生效名单")
    effective_list = db.query(Blacklist).filter(Blacklist.status == 1).order_by(Blacklist.id).all()
    if not effective_list:
        st.caption("暂无生效记录。")
    else:
        st.caption("可按姓名、学号、专业筛选（留空表示不限制）。")
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
        st.caption(LABEL_DISPLAY_AND_SORT + "：")
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
            del_sid_input = st.text_input("输入要删除的学号", key="del_student_id", placeholder="学号")
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
                            st.error("未找到该学号的生效记录。")
                        else:
                            with st.spinner("正在更新..."):
                                rec.status = 0
                                db.commit()
                                _log_action(AUDIT_DELETE, target=sid_clean[:16], details=f"软删除：{rec.name} {sid_clean[:8]}***")
                            st.success("已软删除。")
                            st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("删除操作失败，请稍后重试。")

            st.divider()
            st.caption("将所有生效记录设为已撤销，清空生效名单。请谨慎操作。")
            if not st.session_state.get("admin_show_init_confirm"):
                if st.button(LABEL_INIT_LIST, key="admin_init_list_btn"):
                    st.session_state["admin_show_init_confirm"] = True
                    st.rerun()
            else:
                st.warning("确定要初始化名单吗？此操作将把所有生效记录设为已撤销（软删除），生效名单将为空。")
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
                            st.success("名单已初始化，生效名单已清空。")
                            st.rerun()
                        except Exception:
                            db.rollback()
                            st.error("初始化失败，请稍后重试。")
                with col_cancel:
                    if st.button("取消", key="admin_init_cancel_btn"):
                        if "admin_show_init_confirm" in st.session_state:
                            del st.session_state["admin_show_init_confirm"]
                        st.rerun()

            st.divider()
            edit_sid_input = st.text_input("输入要编辑的学号", key="edit_student_id", placeholder="学号")
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
                            st.error("未找到该学号的生效记录。")
                        elif rec.status == 0:
                            st.warning("已撤销记录不可编辑，请先恢复或忽略。")
                        else:
                            st.session_state["admin_edit_id"] = rec.id
                            st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("查找记录失败，请检查学号后重试。")
    return


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
            return
        with st.form("admin_edit_form"):
            st.caption(f"正在编辑记录 ID：{edit_id}（学号不可修改）")
            edit_name = st.text_input("姓名", value=rec.name, key="admin_edit_name")
            st.text_input("学号（不可修改）", value=rec.student_id, disabled=True, key="admin_edit_sid_display")
            edit_major = st.text_input("专业", value=rec.major or "", key="admin_edit_major")
            edit_reason = st.text_area("原因", value=rec.reason or "", key="admin_edit_reason")
            edit_date_val = rec.punishment_date
            edit_date = st.date_input("处分日期", value=edit_date_val or datetime.now().date(), key="admin_edit_date")
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
                    st.success("已保存修改。")
                    st.rerun()
                except Exception:
                    edit_db.rollback()
                    st.error("保存失败，请检查输入后重试。")
            if submit_cancel:
                if "admin_edit_id" in st.session_state:
                    del st.session_state["admin_edit_id"]
                st.rerun()


def _render_revoked_section(db):
    """名单管理：已撤销名单、筛选、分页、导出、按学号恢复。"""
    st.subheader("已撤销名单")
    revoked_list = db.query(Blacklist).filter(Blacklist.status == 0).order_by(Blacklist.id).all()
    if not revoked_list:
        st.caption("暂无已撤销记录。")
    else:
        st.caption("可按姓名、学号、专业筛选（留空表示不限制）。")
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
        st.caption(LABEL_DISPLAY_AND_SORT + "：")
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
            restore_sid_input = st.text_input("输入要恢复的学号", key="restore_student_id", placeholder="学号")
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
                            st.error("未找到该学号的已撤销记录。")
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
                        st.error("恢复失败，请稍后重试。")
    return


def _render_batch_check_section(db):
    """名单管理：批量查询（上传 Excel 与生效名单比对）。"""
    st.subheader("批量查询")
    st.caption("上传包含「学号」列的 Excel，与生效名单比对；可下载比对结果报告（含完整学号）。")
    admin_batch_file = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls"], key="admin_batch_check_file")
    if st.button("开始比对", key="admin_batch_check_btn") and admin_batch_file:
        try:
            with st.spinner("正在解析并比对..."):
                df_batch = parse_batch_check_excel(admin_batch_file)
        except ValueError as e:
            st.error(str(e))
        else:
            if df_batch.empty:
                st.warning("Excel 中未找到有效的学号。")
            else:
                student_ids_batch = df_batch["学号"].dropna().astype(str).unique().tolist()
                matched_batch = (
                    db.query(Blacklist)
                    .filter(Blacklist.status == 1, Blacklist.student_id.in_(student_ids_batch))
                    .all()
                )
                _log_action(
                    AUDIT_QUERY_BATCH,
                    target=admin_batch_file.name,
                    details=f"共 {len(student_ids_batch)} 条，命中 {len(matched_batch)} 条",
                )
                if not matched_batch:
                    st.success("✅ 未查询到违规记录，名单内学生信用良好。")
                else:
                    st.error(f"⚠️ 共命中 {len(matched_batch)} 条失信/违规记录。")
                    st.dataframe(
                        pd.DataFrame([
                            {"姓名": r.name, "学号": r.student_id, "专业": r.major or "", "原因": (r.reason or "")[:50], "处分日期": str(r.punishment_date) if r.punishment_date else ""}
                            for r in matched_batch
                        ]),
                        use_container_width=True,
                        hide_index=True,
                    )
                    report_rows_admin = []
                    for r in matched_batch:
                        report_rows_admin.append({
                            "姓名": r.name,
                            "学号": r.student_id,
                            "专业": r.major or "",
                            "原因": r.reason or "",
                            "处分日期": str(r.punishment_date) if r.punishment_date else "",
                            "是否命中": "是",
                        })
                    report_df_admin = pd.DataFrame(report_rows_admin)
                    buf_admin = BytesIO()
                    report_df_admin.to_excel(buf_admin, index=False, engine="openpyxl")
                    buf_admin.seek(0)
                    st.download_button(
                        label="下载比对结果报告 (Excel)",
                        data=buf_admin.getvalue(),
                        file_name="比对结果报告_管理员.xlsx",
                        mime=MIME_XLSX,
                        key="admin_batch_download",
                    )


def _render_management(db):
    """名单管理：按「录入 → 生效名单 → 已撤销 → 批量查询」分 Tab，缩短单页、符合操作逻辑。"""
    st.caption("录入名单后，在生效/已撤销名单中查看与维护，需要时使用批量查询。")
    tab_rec, tab_eff, tab_rev, tab_query = st.tabs(["录入", "生效名单", "已撤销名单", "批量查询"])
    with tab_rec:
        _render_import_section(db)
        _render_manual_add_section(db)
    with tab_eff:
        _render_effective_list_section(db)
        _render_edit_form_section()
    with tab_rev:
        _render_revoked_section(db)
    with tab_query:
        _render_batch_check_section(db)


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

    col1, col2, col3 = st.columns(3)
    with col1:
        filter_operator = st.selectbox(
            "操作人",
            ["全部"] + operator_names,
            key="audit_filter_operator",
        )
    with col2:
        filter_type = st.selectbox(
            "操作类型",
            ["全部"] + AUDIT_ACTION_TYPES,
            key="audit_filter_type",
        )
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
            if filter_type != "全部":
                q = q.filter(AuditLog.action_type == filter_type)
            if use_date_filter and filter_date is not None:
                q = q.filter(func.date(AuditLog.timestamp) == str(filter_date))
            logs = q.limit(500).all()

        if not logs:
            st.caption("暂无符合条件的审计日志。")
        else:
            log_df = pd.DataFrame(
                [
                    {
                        "ID": r.id,
                        "操作人": r.operator_name,
                        "类型": r.action_type,
                        "对象": r.target or "",
                        "详情": (r.details or "")[:100],
                        "时间": str(r.timestamp),
                    }
                    for r in logs
                ]
            )
            st.dataframe(log_df, use_container_width=True, hide_index=True)
            st.caption(f"共 {len(logs)} 条（最多展示 500 条）。")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            col_csv, col_xlsx, _ = st.columns([1, 1, 2])
            with col_csv:
                csv_buf = BytesIO()
                log_df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
                csv_buf.seek(0)
                st.download_button(
                    label="导出 CSV",
                    data=csv_buf.getvalue(),
                    file_name=f"审计日志_{stamp}.csv",
                    mime="text/csv",
                    key="audit_export_csv",
                )
            with col_xlsx:
                xlsx_buf = BytesIO()
                log_df.to_excel(xlsx_buf, index=False, engine="openpyxl")
                xlsx_buf.seek(0)
                st.download_button(
                    label="导出 Excel",
                    data=xlsx_buf.getvalue(),
                    file_name=f"审计日志_{stamp}.xlsx",
                    mime=MIME_XLSX,
                    key="audit_export_xlsx",
                )
    except Exception:
        st.error("加载审计日志失败，请稍后重试。")

    st.divider()
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
        st.error("读取数据库文件失败，请检查备份目录或联系管理员。")

    st.divider()
    st.subheader("⚡️ 危险操作：数据恢复 (Restore)")
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
            if st.button("🔴 确认恢复 (Confirm Restore)", key="admin_confirm_restore"):
                try:
                    backup_bytes = restore_uploaded.read()
                    if not backup_bytes:
                        st.error("上传文件为空，无法恢复。")
                    else:
                        with open(DATABASE_PATH, "wb") as f:
                            f.write(backup_bytes)
                        st.cache_resource.clear()
                        _log_action(AUDIT_BACKUP, target="数据恢复", details=f"从文件 {restore_uploaded.name} 恢复")
                        st.success("数据库已恢复，即将刷新页面。")
                        st.balloons()
                        time.sleep(2)
                        st.rerun()
                except OSError:
                    st.error("恢复失败，请确认上传的是有效的 .db 备份文件。")
                except Exception:
                    st.error("恢复过程出错，请稍后重试或联系管理员。")
        else:
            st.caption("请先勾选上方确认框后再执行恢复。")


def _render_user_management(db):
    """用户管理：用户列表、新增用户、密码重置、启用/禁用。"""
    users = db.query(User).order_by(User.id).all()

    st.subheader("用户列表")
    st.caption("工号唯一；禁用后该账号无法登录。")
    if not users:
        st.caption("暂无用户。")
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
                            st.success("用户已添加。")
                            st.rerun()
                except Exception:
                    db.rollback()
                    st.error("添加失败，请检查输入后重试。")

    st.divider()
    st.subheader("密码重置与启用/禁用")
    st.caption("选择用户后执行重置密码或切换账号状态；不能禁用当前登录账号。")
    if not users:
        st.caption("暂无用户。")
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
                                st.success("密码已重置。")
                                st.rerun()
                    except Exception:
                        db.rollback()
                        st.error("重置失败，请稍后重试。")

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
                        if target_user.username == st.session_state.get("username"):
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
                    st.error("操作失败，请稍后重试。")


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
    st.caption("选择功能板块")
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
