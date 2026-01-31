"""
教师端页面：学术诚信档案查询（只读）、批量智能比对
"""
from io import BytesIO

import pandas as pd
import streamlit as st

from config import AUDIT_QUERY_BATCH, AUDIT_QUERY_SINGLE
from database import SessionLocal
from models import AuditLog, Blacklist
from utils import clean_student_id, parse_batch_check_excel


def _log_teacher_action(action_type: str, target: str = "", details: str = ""):
    """教师端写入审计日志（如批量比对）。"""
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


def _render_my_logs():
    """个人记录：展示当前用户最近操作历史（审计日志）。"""
    st.subheader("个人记录")
    st.caption("您最近的操作历史（最多 100 条）。")
    db = SessionLocal()
    try:
        with st.spinner("加载中..."):
            name = st.session_state.get("user_name", "")
            if not name:
                st.caption("无法获取当前用户。")
                return
            logs = (
                db.query(AuditLog)
                .filter(AuditLog.operator_name == name)
                .order_by(AuditLog.timestamp.desc())
                .limit(100)
                .all()
            )
        if not logs:
            st.caption("暂无操作记录。")
            return
        log_df = pd.DataFrame([
            {
                "时间": str(r.timestamp),
                "类型": r.action_type,
                "对象": r.target or "",
                "详情": (r.details or "")[:80],
            }
            for r in logs
        ])
        st.dataframe(log_df, use_container_width=True, hide_index=True)
    except Exception:
        st.error("加载失败，请稍后重试。")
    finally:
        db.close()


def render_teacher_page():
    """教师页：单条查询 + 批量智能比对 + 个人记录。"""
    st.title("🎓 学术诚信档案查询 (Academic Integrity Query)")
    st.caption("仅查询生效中的失信记录。")

    tab_single, tab_batch, tab_log = st.tabs(["🔍 单条查询", "📤 批量智能比对", "📋 个人记录"])

    with tab_single:
        _render_single_search()

    with tab_batch:
        _render_batch_check()

    with tab_log:
        _render_my_logs()


def _render_single_search():
    """单条查询：输入姓名或学号，展示结果。"""
    search_input = st.text_input("请输入学生姓名 或 学号", key="teacher_search", placeholder="姓名或学号")
    search_clicked = st.button("🔍 查询 (Search)", key="teacher_search_btn")

    if not search_clicked:
        return

    raw = (search_input or "").strip()
    if not raw:
        st.error("请输入姓名或学号后再查询。")
        return

    db = SessionLocal()
    try:
        with st.spinner("正在查询..."):
            clean_raw = clean_student_id(raw)
            q = db.query(Blacklist).filter(Blacklist.status == 1)
            q = q.filter(
                (Blacklist.name == raw) | (Blacklist.student_id == clean_raw)
            )
            records = q.all()
    except Exception:
        st.error("查询失败，请稍后重试。")
        return
    finally:
        db.close()

    _log_teacher_action(AUDIT_QUERY_SINGLE, target="", details="单条查询")

    if not records:
        st.success("✅ 未查询到违规记录，该生信用良好。")
        return

    st.error("⚠️ 查询到失信/违规记录，请核实。")
    single_table = pd.DataFrame([
        {
            "姓名": r.name,
            "学号": r.student_id,
            "专业": r.major or "",
            "原因": (r.reason or "")[:80],
            "处分日期": str(r.punishment_date) if r.punishment_date else "",
        }
        for r in records
    ])
    st.dataframe(single_table, use_container_width=True, hide_index=True)


PAGE_SIZE = 10  # 每页最多 10 条违规学生信息


def _render_batch_check():
    """批量智能比对：上传 Excel，按学号与黑名单比对，展示结果并支持下载报告；表格分页每页 10 条。"""
    st.subheader("批量智能比对")
    st.caption("上传包含「学号」列的 Excel (.xlsx / .xls)，与生效名单比对；可下载比对结果报告。")
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
            st.warning("Excel 中未找到有效的学号。")
            return

        student_ids = df["学号"].dropna().astype(str).unique().tolist()
        db = SessionLocal()
        try:
            matched = (
                db.query(Blacklist)
                .filter(Blacklist.status == 1, Blacklist.student_id.in_(student_ids))
                .all()
            )
        except Exception as e:
            st.error("比对失败，请稍后重试。")
            return
        finally:
            db.close()

        _log_teacher_action(AUDIT_QUERY_BATCH, target=uploaded.name, details=f"共 {len(student_ids)} 条，命中 {len(matched)} 条")

        if not matched:
            st.success("✅ 未查询到违规记录，名单内学生信用良好。")
            if "teacher_batch_matched" in st.session_state:
                del st.session_state["teacher_batch_matched"]
                del st.session_state["teacher_batch_page"]
            return

        # 将命中结果存入 session_state，供分页与下载使用
        st.session_state["teacher_batch_matched"] = [
            {
                "姓名": r.name,
                "学号": r.student_id,
                "专业": r.major or "",
                "原因": r.reason or "",
                "处分日期": str(r.punishment_date) if r.punishment_date else "",
            }
            for r in matched
        ]
        st.session_state["teacher_batch_page"] = 0

    if "teacher_batch_matched" not in st.session_state:
        return

    matched_store = st.session_state["teacher_batch_matched"]
    total = len(matched_store)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    current_page = st.session_state.get("teacher_batch_page", 0)
    current_page = max(0, min(current_page, total_pages - 1))
    st.session_state["teacher_batch_page"] = current_page

    start = current_page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    page_data = matched_store[start:end]

    st.error(f"⚠️ 共命中 {total} 条失信/违规记录，请核实。（当前第 {current_page + 1}/{total_pages} 页）")
    batch_table = pd.DataFrame([
        {
            "姓名": d["姓名"],
            "学号": d["学号"],
            "专业": d["专业"],
            "原因": (d["原因"] or "")[:80],
            "处分日期": d["处分日期"],
        }
        for d in page_data
    ])
    st.dataframe(batch_table, use_container_width=True, hide_index=True)

    # 分页：上一页 / 下一页，每页最多 PAGE_SIZE 条
    col_prev, col_info, col_next = st.columns([1, 2, 1])
    with col_prev:
        if current_page > 0:
            if st.button("上一页", key="teacher_batch_prev"):
                st.session_state["teacher_batch_page"] = current_page - 1
                st.rerun()
        else:
            st.button("上一页", key="teacher_batch_prev", disabled=True)
    with col_info:
        st.caption(f"第 {current_page + 1} 页 / 共 {total_pages} 页，本页 {len(page_data)} 条")
    with col_next:
        if current_page < total_pages - 1:
            if st.button("下一页", key="teacher_batch_next"):
                st.session_state["teacher_batch_page"] = current_page + 1
                st.rerun()
        else:
            st.button("下一页", key="teacher_batch_next", disabled=True)

    # 比对结果报告 Excel（全部命中记录）
    report_df = pd.DataFrame([
        {**d, "是否命中": "是"} for d in matched_store
    ])
    buf = BytesIO()
    report_df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    st.download_button(
        label="下载比对结果报告 (Excel)",
        data=buf.getvalue(),
        file_name="比对结果报告.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="teacher_batch_download",
    )
