"""
管理员页面：仪表盘、名单管理、系统维护、用户管理
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
    AUDIT_QUERY_BATCH,
    AUDIT_RESTORE,
)
from database import SessionLocal
from models import AuditLog, Blacklist, User
from utils import (
    DATABASE_PATH,
    clean_student_id,
    get_db_file_bytes,
    parse_batch_check_excel,
    parse_blacklist_excel,
)


def _log_action(action_type: str, target: str = "", details: str = ""):
    """写入审计日志。"""
    db = SessionLocal()
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
    finally:
        db.close()


def _render_dashboard(db):
    """Tab 1: 仪表盘 - 三项指标 + 专业分布饼图 + 按年份柱状图。"""
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


def _render_management(db):
    """Tab 2: 名单管理 - 批量导入、手动新增、列表与软删除。"""
    # ---------- 批量导入 ----------
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
            try:
                with st.spinner("正在导入..."):
                    imported = 0
                    updated = 0
                    skipped = 0
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
            except Exception as e:
                db.rollback()
                st.error("导入失败，请检查 Excel 格式（需包含：姓名、学号、专业、原因、处分时间）或联系管理员。")

    st.divider()
    # ---------- 手动新增 ----------
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
                except Exception as e:
                    db.rollback()
                    st.error("添加失败，请检查学号是否重复或联系管理员。")

    st.divider()
    # ---------- 列表与软删除 ----------
    st.subheader("生效名单与删除")
    effective_list = db.query(Blacklist).filter(Blacklist.status == 1).order_by(Blacklist.id).all()
    if not effective_list:
        st.caption("暂无生效记录。")
    else:
        df_display = pd.DataFrame(
            [
                {
                    "序号": i,
                    "姓名": r.name,
                    "学号": r.student_id,
                    "专业": r.major or "",
                    "原因": (r.reason or "")[:50],
                    "处分日期": str(r.punishment_date) if r.punishment_date else "",
                }
                for i, r in enumerate(effective_list, 1)
            ]
        )
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        del_sid_input = st.text_input("输入要删除的学号", key="del_student_id", placeholder="学号")
        if st.button("软删除（设为已撤销）", key="admin_del_btn") and del_sid_input:
            try:
                sid_clean = clean_student_id(del_sid_input.strip())
                if not sid_clean:
                    st.error("请输入有效学号。")
                else:
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

        # ---------- 初始化名单（二次确认） ----------
        st.divider()
        st.subheader("初始化名单")
        st.caption("将所有生效记录设为已撤销，清空生效名单。请谨慎操作。")
        if not st.session_state.get("admin_show_init_confirm"):
            if st.button("初始化名单", key="admin_init_list_btn"):
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
                            _log_action(AUDIT_DELETE, target="初始化名单", details=f"共 {n} 条生效记录设为已撤销")
                        if "admin_show_init_confirm" in st.session_state:
                            del st.session_state["admin_show_init_confirm"]
                        st.success("名单已初始化，生效名单已清空。")
                        st.rerun()
                    except Exception as e:
                        db.rollback()
                        st.error("初始化失败，请稍后重试。")
            with col_cancel:
                if st.button("取消", key="admin_init_cancel_btn"):
                    if "admin_show_init_confirm" in st.session_state:
                        del st.session_state["admin_show_init_confirm"]
                    st.rerun()

        # ---------- 编辑现有人员 ----------
        st.divider()
        st.subheader("编辑现有人员")
        edit_sid_input = st.text_input("输入要编辑的学号", key="edit_student_id", placeholder="学号")
        if st.button("编辑", key="admin_edit_btn") and edit_sid_input:
            try:
                sid_edit = clean_student_id(edit_sid_input.strip())
                if not sid_edit:
                    st.error("请输入有效学号。")
                else:
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

    # 编辑表单（在列表外，避免 db 作用域问题）
    if st.session_state.get("admin_edit_id"):
        edit_id = st.session_state["admin_edit_id"]
        edit_db = SessionLocal()
        try:
            rec = edit_db.query(Blacklist).filter(Blacklist.id == edit_id).first()
            if not rec or rec.status != 1:
                if "admin_edit_id" in st.session_state:
                    del st.session_state["admin_edit_id"]
                st.rerun()
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
                    except Exception as e:
                        edit_db.rollback()
                        st.error("保存失败，请检查输入后重试。")
                if submit_cancel:
                    if "admin_edit_id" in st.session_state:
                        del st.session_state["admin_edit_id"]
                    st.rerun()
        finally:
            edit_db.close()

    # ---------- 已撤销名单与恢复 ----------
    st.divider()
    st.subheader("已撤销名单与恢复")
    revoked_list = db.query(Blacklist).filter(Blacklist.status == 0).order_by(Blacklist.id).all()
    if not revoked_list:
        st.caption("暂无已撤销记录。")
    else:
        df_revoked = pd.DataFrame(
            [
                {
                    "序号": i,
                    "姓名": r.name,
                    "学号": r.student_id,
                    "专业": r.major or "",
                    "原因": (r.reason or "")[:50],
                    "处分日期": str(r.punishment_date) if r.punishment_date else "",
                }
                for i, r in enumerate(revoked_list, 1)
            ]
        )
        st.dataframe(df_revoked, use_container_width=True, hide_index=True)
        restore_sid_input = st.text_input("输入要恢复的学号", key="restore_student_id", placeholder="学号")
        if st.button("恢复为生效", key="admin_restore_btn") and restore_sid_input:
            try:
                sid_restore = clean_student_id(restore_sid_input.strip())
                if not sid_restore:
                    st.error("请输入有效学号。")
                else:
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

    st.divider()
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
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="admin_batch_download",
                    )


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
    except Exception as e:
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
        st.warning("This will OVERWRITE all current data!")
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
            except OSError as e:
                st.error("恢复失败，请确认上传的是有效的 .db 备份文件。")
            except Exception as e:
                st.error("恢复过程出错，请稍后重试或联系管理员。")


def _render_user_management(db):
    """Tab 4: 用户管理 - 用户列表、新增用户、密码重置、启用/禁用。"""
    st.subheader("用户列表")
    users = db.query(User).order_by(User.id).all()
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
        new_username = st.text_input("工号/登录名", key="new_username")
        new_password = st.text_input("密码", type="password", key="new_password")
        new_full_name = st.text_input("真实姓名", key="new_full_name")
        new_role = st.selectbox("角色", ["教师", "管理员"], key="new_role")
        if st.form_submit_button("添加用户"):
            if not new_username or not new_password or not new_full_name:
                st.error("请填写工号、密码和姓名。")
            elif len(new_password.strip()) < 6:
                st.error("密码至少 6 位，请重新输入。")
            else:
                try:
                    with st.spinner("正在保存..."):
                        if db.query(User).filter(User.username == new_username.strip()).first():
                            st.error("该工号已存在。")
                        else:
                            role_val = "admin" if new_role == "管理员" else "teacher"
                            pwd_hash = bcrypt.hashpw(
                                new_password.encode("utf-8"), bcrypt.gensalt()
                            ).decode("utf-8")
                            u = User(
                                username=new_username.strip(),
                                password_hash=pwd_hash,
                                full_name=new_full_name.strip(),
                                role=role_val,
                                is_active=True,
                            )
                            db.add(u)
                            db.commit()
                            _log_action(AUDIT_ADD, target=f"用户 {new_username}", details=f"角色 {new_role}")
                            st.success("用户已添加。")
                            st.rerun()
                except Exception:
                    db.rollback()
                    st.error("添加失败，请检查输入后重试。")

    st.divider()
    st.subheader("密码重置")
    if not users:
        st.caption("暂无用户，无法重置密码。")
    else:
        user_options = [f"{u.username}（{u.full_name}）" for u in users]
        reset_choice = st.selectbox("选择用户", user_options, key="reset_user_choice")
        reset_password = st.text_input("新密码", type="password", key="reset_password")
        if st.button("重置密码", key="admin_reset_pwd_btn") and reset_password:
            if len(reset_password.strip()) < 6:
                st.error("密码至少 6 位，请重新输入。")
            else:
                try:
                    with st.spinner("正在重置..."):
                        username = reset_choice.split("（")[0].strip()
                        user = db.query(User).filter(User.username == username).first()
                        if not user:
                            st.error("未找到该用户。")
                        else:
                            user.password_hash = bcrypt.hashpw(
                                reset_password.encode("utf-8"), bcrypt.gensalt()
                            ).decode("utf-8")
                            db.commit()
                            _log_action(AUDIT_ADD, target=f"密码重置 {username}", details="")
                            st.success("密码已重置。")
                            st.rerun()
                except Exception:
                    db.rollback()
                    st.error("重置失败，请稍后重试。")

    st.divider()
    st.subheader("启用/禁用账号")
    if not users:
        st.caption("暂无用户。")
    else:
        toggle_options = [f"{u.username}（{u.full_name}）— 当前：{'正常' if u.is_active else '已禁用'}" for u in users]
        toggle_choice = st.selectbox("选择用户", toggle_options, key="toggle_user_choice")
        target_username = toggle_choice.split("（")[0].strip()
        target_user = next((u for u in users if u.username == target_username), None)
        if target_user and st.button("切换状态", key="admin_toggle_btn"):
            if target_user.username == st.session_state.get("username"):
                st.warning("不能禁用当前登录账号。")
            else:
                try:
                    with st.spinner("正在更新..."):
                        target_user.is_active = not target_user.is_active
                        db.commit()
                        status = "启用" if target_user.is_active else "禁用"
                        _log_action(AUDIT_ADD, target=f"账号{status} {target_username}", details="")
                        st.success(f"已{status} {target_username}。")
                        st.rerun()
                except Exception:
                    db.rollback()
                    st.error("操作失败，请稍后重试。")


def render_admin_page():
    """管理员页主入口：四个 Tab（仪表盘、名单管理、系统维护、用户管理）。"""
    st.title("管理员")
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📊 仪表盘", "📋 名单管理", "🛠️ 系统维护", "👥 用户管理"]
    )

    db = SessionLocal()
    try:
        with tab1:
            _render_dashboard(db)
        with tab2:
            _render_management(db)
        with tab3:
            _render_system(db)
        with tab4:
            _render_user_management(db)
    finally:
        db.close()
