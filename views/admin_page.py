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
    """Tab 1: 仪表盘 - 三项指标 + 专业分布饼图。"""
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
        return
    major_series = pd.Series([r[0] or "未填写" for r in rows])
    counts = major_series.value_counts().reset_index()
    counts.columns = ["专业", "人数"]
    fig = px.pie(counts, values="人数", names="专业", title="专业分布")
    st.plotly_chart(fig, use_container_width=True)


def _render_management(db):
    """Tab 2: 名单管理 - 批量导入、手动新增、列表与软删除。"""
    # ---------- 批量导入 ----------
    st.subheader("批量导入")
    uploaded = st.file_uploader("上传 Excel (.xlsx)", type=["xlsx"], key="admin_import_file")
    if st.button("开始导入", key="admin_import_btn") and uploaded:
        try:
            with st.spinner("正在解析并导入..."):
                df = parse_blacklist_excel(uploaded)
                # 列名映射：姓名->name, 学号->student_id, 专业->major, 原因->reason, 处分时间->punishment_date
                imported = 0
                updated = 0
                for _, row in df.iterrows():
                    sid = str(row["学号"]).strip() if pd.notna(row["学号"]) else ""
                    if not sid:
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
                _log_action("IMPORT", target=uploaded.name, details=f"新增 {imported} 条，更新 {updated} 条")
            st.success(f"导入成功：新增 {imported} 条，更新 {updated} 条。")
            st.balloons()
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            db.rollback()
            st.error(f"导入失败：{e!s}")

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
                            _log_action("ADD", target=add_name, details=f"学号 {sid_clean[:8]}***")
                            st.success("已添加。")
                            st.rerun()
                except Exception as e:
                    db.rollback()
                    st.error(f"添加失败：{e!s}")

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
                    "ID": r.id,
                    "姓名": r.name,
                    "学号": r.student_id,
                    "专业": r.major or "",
                    "原因": (r.reason or "")[:50],
                    "处分日期": str(r.punishment_date) if r.punishment_date else "",
                }
                for r in effective_list
            ]
        )
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        del_id_input = st.text_input("输入要删除的记录 ID", key="del_id")
        if st.button("软删除（设为已撤销）", key="admin_del_btn") and del_id_input:
            try:
                rid = int(del_id_input.strip())
                rec = db.query(Blacklist).filter(Blacklist.id == rid).first()
                if not rec:
                    st.error("未找到该 ID 的记录。")
                elif rec.status == 0:
                    st.warning("该记录已是已撤销状态。")
                else:
                    with st.spinner("正在更新..."):
                        rec.status = 0
                        db.commit()
                        _log_action("DELETE", target=str(rid), details=f"软删除：{rec.name} {rec.student_id[:8]}***")
                    st.success("已软删除。")
                    st.rerun()
            except ValueError:
                st.error("请输入有效的数字 ID。")
            except Exception as e:
                db.rollback()
                st.error(f"操作失败：{e!s}")

    st.divider()
    st.subheader("批量查询")
    st.caption("上传包含「学号」列的 Excel，与生效名单比对；可下载比对结果报告（含完整学号）。")
    admin_batch_file = st.file_uploader("选择 Excel 文件", type=["xlsx"], key="admin_batch_check_file")
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
                    "QUERY_BATCH",
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
    """Tab 3: 系统维护 - 审计日志、数据库下载。"""
    st.subheader("审计日志")
    try:
        with st.spinner("加载日志..."):
            logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(500).all()
        if not logs:
            st.caption("暂无审计日志。")
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
    except Exception as e:
        st.error(f"加载审计日志失败：{e!s}")

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
        st.error(f"读取数据库文件失败：{e!s}")

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
                    _log_action("BACKUP", target="数据恢复", details=f"从文件 {restore_uploaded.name} 恢复")
                    st.success("数据库已恢复，即将刷新页面。")
                    st.balloons()
                    time.sleep(2)
                    st.rerun()
            except OSError as e:
                st.error(f"恢复失败：{e!s}")
            except Exception as e:
                st.error(f"恢复过程出错：{e!s}")


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
                            _log_action("ADD", target=f"用户 {new_username}", details=f"角色 {new_role}")
                            st.success("用户已添加。")
                            st.rerun()
                except Exception as e:
                    db.rollback()
                    st.error(f"添加失败：{e!s}")

    st.divider()
    st.subheader("密码重置")
    if not users:
        st.caption("暂无用户，无法重置密码。")
    else:
        user_options = [f"{u.username}（{u.full_name}）" for u in users]
        reset_choice = st.selectbox("选择用户", user_options, key="reset_user_choice")
        reset_password = st.text_input("新密码", type="password", key="reset_password")
        if st.button("重置密码", key="admin_reset_pwd_btn") and reset_password:
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
                        _log_action("ADD", target=f"密码重置 {username}", details="")
                        st.success("密码已重置。")
                        st.rerun()
            except Exception as e:
                db.rollback()
                st.error(f"重置失败：{e!s}")

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
                        _log_action("ADD", target=f"账号{status} {target_username}", details="")
                        st.success(f"已{status} {target_username}。")
                        st.rerun()
                except Exception as e:
                    db.rollback()
                    st.error(f"操作失败：{e!s}")


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
