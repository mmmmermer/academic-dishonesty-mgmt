"""
管理员页面：仪表盘、名单管理、系统维护
"""
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from database import SessionLocal
from models import AuditLog, Blacklist
from utils import clean_id_card, get_db_file_bytes, parse_blacklist_excel


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
                # 列名映射：姓名->name, 身份证号->id_card, 专业->major, 原因->reason, 处分时间->punishment_date
                imported = 0
                updated = 0
                for _, row in df.iterrows():
                    id_card = str(row["身份证号"]).strip() if pd.notna(row["身份证号"]) else ""
                    if not id_card:
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
                    existing = db.query(Blacklist).filter(Blacklist.id_card == id_card).first()
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
                            id_card=id_card,
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
        add_id_card = st.text_input("身份证号", key="add_id_card")
        add_major = st.text_input("专业", key="add_major")
        add_reason = st.text_area("原因", key="add_reason")
        add_date = st.date_input("处分日期", key="add_date")
        if st.form_submit_button("添加"):
            if not add_name or not add_id_card:
                st.error("请填写姓名和身份证号。")
            else:
                try:
                    with st.spinner("正在保存..."):
                        id_card_clean = clean_id_card(add_id_card)
                        if db.query(Blacklist).filter(Blacklist.id_card == id_card_clean).first():
                            st.error("该身份证号已存在。")
                        else:
                            rec = Blacklist(
                                name=add_name.strip(),
                                id_card=id_card_clean,
                                major=add_major.strip() or None,
                                reason=add_reason.strip() or None,
                                punishment_date=add_date,
                                status=1,
                            )
                            db.add(rec)
                            db.commit()
                            _log_action("ADD", target=add_name, details=f"身份证号 {id_card_clean[:6]}***")
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
                    "身份证号": r.id_card,
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
                        _log_action("DELETE", target=str(rid), details=f"软删除：{rec.name} {rec.id_card[:6]}***")
                    st.success("已软删除。")
                    st.rerun()
            except ValueError:
                st.error("请输入有效的数字 ID。")
            except Exception as e:
                db.rollback()
                st.error(f"操作失败：{e!s}")


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


def render_admin_page():
    """管理员页主入口：三个 Tab（仪表盘、名单管理、系统维护）。"""
    st.title("管理员")
    tab1, tab2, tab3 = st.tabs(["📊 仪表盘", "📋 名单管理", "🛠️ 系统维护"])

    db = SessionLocal()
    try:
        with tab1:
            _render_dashboard(db)
        with tab2:
            _render_management(db)
        with tab3:
            _render_system(db)
    finally:
        db.close()
