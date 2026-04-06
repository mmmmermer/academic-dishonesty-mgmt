"""
管理员名单查询：替代原仪表盘，提供沉浸式名单检索与导出。
"""
import os
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from core.models import Blacklist
from core.search import sync_blacklist_record_search_helper_fields
from core.utils import sanitize_for_export, clean_student_id, safe_filename, remove_old_pdf, log_audit_action
from core.config import (
    CAPTION_FILTER_BY_NAME_SID_MAJOR, EMPTY_NO_EFFECTIVE,
    LABEL_CUSTOM_INPUT, MIME_XLSX, LABEL_STUDENT_ID, LABEL_NAME,
    LABEL_MAJOR, LABEL_REASON, LABEL_PUNISHMENT_DATE,
    MSG_TRY_AGAIN, SUCCESS_SAVED, AUDIT_ADD,
    UNIT_INPUT_OPTIONS,
)
from views.components import (
    apply_blacklist_sort,
    build_blacklist_query,
    clamp_page,
    render_blacklist_export_button,
    render_blacklist_table,
    render_list_controls,
    render_pagination,
)


def _render_selected_actions(db, selected_records):
    """操作台：勾选触发操作选项，不再自动入区。显式点击才加入暂存区。"""
    cart = st.session_state.setdefault("admin_export_cart", {})
    if not selected_records:
        return

    n = len(selected_records)
    cols = st.columns([3, 2, 2])
    with cols[0]:
        st.caption(f"☑️ 已选中 {n} 条")
    with cols[1]:
        if n == 1:
            rec = selected_records[0]
            if st.button("✏️ 快捷编辑", width="stretch", key="btn_edit_sel"):
                _show_edit_dialog(db, rec.id)
    with cols[2]:
        if st.button(f"➕ 加入暂存区 ({n})", width="stretch", key="btn_add_to_cart"):
            added, skipped = 0, 0
            for r in selected_records:
                if r.id not in cart:
                    cart[r.id] = {
                        "id": r.id, "name": r.name, "student_id": r.student_id,
                        "major": r.major, "reason": r.reason,
                        "reason_text": r.reason_text,
                        "punishment_date": r.punishment_date,
                        "impact_start_date": r.impact_start_date,
                        "impact_end_date": r.impact_end_date
                    }
                    added += 1
                else:
                    skipped += 1
            msg = f"✅ 已加入 {added} 条"
            if skipped:
                msg += f"，跳过 {skipped} 条（已在暂存区）"
            st.toast(msg)
            st.rerun()

def _render_cart_panel(all_filtered_query=None, total_filtered=0):
    """暂存区面板：显式加入、一键删除、全量字段展示、一步导出。"""
    cart = st.session_state.setdefault("admin_export_cart", {})
    cart_count = len(cart)

    st.markdown("---")
    if cart_count == 0:
        st.caption("🖲️ 暂存区为空——请在表格中勾选记录并点击「加入暂存区」。")
        return

    with st.container(border=True):
        header_cols = st.columns([5, 1])
        with header_cols[0]:
            st.markdown(
                f"#### 🗂️ 暂存区 &nbsp;"
                f"<span style='background:#e63946;color:white;border-radius:12px;"
                f"padding:2px 10px;font-size:0.85em'>{cart_count}</span>",
                unsafe_allow_html=True
            )
        with header_cols[1]:
            if st.button("🧹 清空", width="stretch", key="btn_clear_cart"):
                st.session_state["admin_export_cart"] = {}
                st.session_state["cart_clear_nonce"] = st.session_state.get("cart_clear_nonce", 0) + 1
                st.session_state.pop("cart_sel", None)
                st.rerun()

        st.caption("勾选左侧方框标记条目，点「移除已勾选」即删除；不需要额外确认。")

        cart_list = list(cart.values())
        cart_ids  = [c["id"] for c in cart_list]
        today = datetime.now().date()

        view_df = pd.DataFrame([{
            "姓名": c["name"],
            "学号/工号": c["student_id"],
            "所在单位": c["major"] or "",
            "处理原因": c.get("reason_text") or "",
            "认定日期": str(c["punishment_date"]) if c["punishment_date"] else "",
            "影响期": (
                ("✅ 是" if c["impact_start_date"] <= today <= c["impact_end_date"] else "❌ 否")
                if c["impact_start_date"] and c["impact_end_date"]
                else ("✅ 是" if c["impact_start_date"] and c["impact_start_date"] <= today else "")
            ),
        } for c in cart_list])

        sel_event = st.dataframe(
            view_df,
            width="stretch",
            height=min(300, max(1, len(view_df)) * 35 + 39),
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="cart_sel"
        )

        selected_rows = sel_event.selection.rows if sel_event else []
        to_remove = [cart_ids[i] for i in selected_rows if i < len(cart_ids)]

        action_cols = st.columns([2, 2, 2])
        with action_cols[0]:
            remove_disabled = not bool(to_remove)
            remove_label = f"🗑️ 移除已勾选 ({len(to_remove)})" if to_remove else "🗑️ 移除已勾选"
            if st.button(remove_label, type="primary" if to_remove else "secondary",
                         disabled=remove_disabled, width="stretch", key="btn_remove_from_cart"):
                for rid in to_remove:
                    cart.pop(rid, None)
                st.session_state["cart_clear_nonce"] = st.session_state.get("cart_clear_nonce", 0) + 1
                st.session_state.pop("cart_sel", None)
                st.rerun()

        with action_cols[1]:
            if all_filtered_query is not None and total_filtered > 0:
                limit = min(total_filtered, 200)
                batch_label = f"➕ 全量入区 ({total_filtered} 条)" if total_filtered <= 200 else f"➕ 全量入区 (前 200 条)"
                if st.button(batch_label, width="stretch", key="btn_batch_add_all"):
                    batch_records = all_filtered_query.limit(limit).all()
                    added, skipped = 0, 0
                    for r in batch_records:
                        if r.id not in cart:
                            cart[r.id] = {
                                "id": r.id, "name": r.name, "student_id": r.student_id,
                                "major": r.major, "reason": r.reason,
                                "reason_text": getattr(r, "reason_text", None),
                                "punishment_date": r.punishment_date,
                                "impact_start_date": r.impact_start_date,
                                "impact_end_date": r.impact_end_date
                            }
                            added += 1
                        else:
                            skipped += 1
                    msg = f"✅ 已加入 {added} 条"
                    if skipped:
                        msg += f"，跳过 {skipped} 条（已在暂存区）"
                    st.toast(msg)
                    st.rerun()

        with action_cols[2]:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_df = pd.DataFrame([{
                "序号": i,
                "姓名": sanitize_for_export(r["name"]),
                "工号/学号": r["student_id"],
                "所在单位": sanitize_for_export(r["major"] or ""),
                "处理原因": sanitize_for_export(r.get("reason_text") or ""),
                "认定结论(文件路径)": sanitize_for_export(r.get("reason") or ""),
                "认定日期": str(r["punishment_date"]) if r["punishment_date"] else "",
                "处理起至时间": (
                    f"{r['impact_start_date']} 至 {r['impact_end_date']}"
                    if r["impact_start_date"] and r["impact_end_date"]
                    else (str(r["impact_start_date"]) or str(r["impact_end_date"]) or "")
                ),
            } for i, r in enumerate(cart_list, 1)])
            buf = BytesIO()
            export_df.to_excel(buf, index=False, engine="openpyxl")
            st.download_button(
                label=f"⬇️ 导出暂存区 ({cart_count} 条)",
                data=buf.getvalue(),
                file_name=f"导出暂存区_{stamp}.xlsx",
                mime=MIME_XLSX,
                key="btn_export_cart",
                width="stretch",
                type="primary"
            )



@st.dialog("修改学术失信人员记录", width="large")
def _show_edit_dialog(db, edit_id):
    """原位浮窗编辑器，直接在页面中央拦截交互并返回，不刷新整个框架"""
    rec = db.query(Blacklist).filter(Blacklist.id == edit_id).first()
    if not rec:
        st.error("找不到此记录。")
        return
        
    # 我们不在 dialog 中套 form submit 而直接监听，因为 button 在 dialog 中天然带有交互流 
    st.caption(f"正在快捷编辑记录。**{LABEL_STUDENT_ID}** 是核心标识，不可在此修改。")
    edit_name = st.text_input(LABEL_NAME, value=rec.name)
    st.text_input(LABEL_STUDENT_ID, value=rec.student_id, disabled=True)
    # 调用与名单查询和手动新增一致的「大小分类」级联选择器
    _cur_major = rec.major or ""
    from views.components import render_single_unit_selector
    edit_major = render_single_unit_selector("dialog_edit_major", default_val=_cur_major)
    edit_reason_text = st.text_input("处理原因(文字)", value=(rec.reason_text or ""))
    if rec.reason:
        st.caption(f"当前已有文件：{rec.reason.split('/')[-1]}")
    edit_reason_file = st.file_uploader(f"更新认定结论 (PDF)", type=["pdf"])
    edit_date = st.date_input(LABEL_PUNISHMENT_DATE, value=rec.punishment_date or datetime.now().date())
    
    default_dates = []
    if rec.impact_start_date and rec.impact_end_date:
        default_dates = [rec.impact_start_date, rec.impact_end_date]
    elif rec.impact_start_date:
        default_dates = [rec.impact_start_date]
        
    impact_dates_edit = st.date_input("处理起至时间 (可选)", value=default_dates or [])
    edit_impact_start = impact_dates_edit[0] if impact_dates_edit and len(impact_dates_edit) > 0 else None
    edit_impact_end = impact_dates_edit[1] if impact_dates_edit and len(impact_dates_edit) == 2 else None
        
    col_save, col_cancel = st.columns(2)
    with col_save:
        submit_save = st.button("🚀 保存并更新", type="primary", width="stretch")
    with col_cancel:
        if st.button("取消关闭", width="stretch"):
            st.rerun()

    if submit_save:
        try:
            rec.name = (edit_name or "").strip() or rec.name
            rec.major = (edit_major or "").strip() or None
            if edit_reason_text is not None:
                rec.reason_text = edit_reason_text.strip() or None
            if edit_reason_file is not None:
                # 清理旧 PDF（若存在），避免磁盘累积孤儿文件
                old_reason_path = rec.reason
                os.makedirs(os.path.join("static", "pdfs"), exist_ok=True)
                filename = f"{safe_filename(rec.student_id)}_{int(time.time())}.pdf"
                file_path = os.path.join("static", "pdfs", filename)
                with open(file_path, "wb") as f:
                    f.write(edit_reason_file.getvalue())
                rec.reason = f"/app/static/pdfs/{filename}"
                remove_old_pdf(old_reason_path)
            rec.punishment_date = edit_date
            rec.impact_start_date = edit_impact_start
            rec.impact_end_date = edit_impact_end
            sync_blacklist_record_search_helper_fields(db, rec)
            db.commit()
            log_audit_action(AUDIT_ADD, target=f"弹窗编辑 {rec.id}", details=f"{rec.name} {rec.student_id[:8]}***")
            st.success(SUCCESS_SAVED)
            time.sleep(0.5)
            st.rerun()
        except Exception:
            db.rollback()
            st.error("保存失败，" + MSG_TRY_AGAIN)


def _render_list_query(db):
    st.subheader("名单查询")
    
    fn, fs, fm, page_size, sort_key, sort_asc = render_list_controls("admin_effective")
    
    base = build_blacklist_query(db, status=1, name_filter=fn, sid_filter=fs, major_categories=fm)
    total = base.count()
    if total == 0:
        st.caption(EMPTY_NO_EFFECTIVE)
        return
        
    ordered = apply_blacklist_sort(base, sort_key, sort_asc)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = clamp_page("admin_effective_page", total_pages)
    page_records = ordered.offset(page * page_size).limit(page_size).all()
    
    st.caption(f"当前检索条件下共有 **{total}** 条有效记录。您可在表格内打钩进行定向导出或修改。")
    
    # 为当前数据的物理视图计算一个独立的“生命时空签名”。
    # cart_clear_nonce 变化时会强制重建表格，使 selected_records 为空，避免清空后被回填
    # 一旦您更改了搜索参数、当前页码、或者是每页显示的数量，这个 Signature 就会随之突变，
    # 迫使 Streamlit 将底层的表格彻底销毁并重生！自然地拔除了跨页/跨环境残留的脏数据打钩状态。
    state_sig = f"{page}_{page_size}_{fn}_{fs}_{','.join(sorted(fm))}_{sort_key}_{sort_asc}_{st.session_state.get('cart_clear_nonce', 0)}"
    current_sel_key = f"admin_query_table_sel_{state_sig}"
    
    # 后渲染：大体量的复选图表被安排在最上方自然伸展
    selected_records = render_blacklist_table(page_records, page_size, page, selection_key=current_sel_key)
    
    # 分页器（紧跟表格，方便翻页查找）
    render_pagination("admin_effective_page", page, total_pages, total, len(page_records))
    
    # 选中操作台（显式「加入暂存区」，不再自动入区）
    _render_selected_actions(db, selected_records)

    # 暂存区（始终渲染，有内容时展开面板）
    _render_cart_panel(all_filtered_query=ordered, total_filtered=total)
    
    st.markdown("---")
    
    st.caption("需要备份或对账？您也可以选择一次性全量导出👇")
    render_blacklist_export_button(db, 1, fn, fs, fm, sort_key, sort_asc, total, "生效名单", "admin_export_effective")
