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
from core.utils import sanitize_for_export, clean_student_id, log_audit_action
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


def _render_selected_actions(db, selected_records, page_records):
    """渲染快捷操作台：勾选累积写入暂存区（只写不退），显式按钮可从暂存区移除当前页选中项。"""
    cart = st.session_state.setdefault("admin_export_cart", {})
    
    # 只累积添加，不自动移除（避免 shift 框选时误剔已存项）
    for r in selected_records:
        if r.id not in cart:
            cart[r.id] = {
                "id": r.id, "name": r.name, "student_id": r.student_id, "major": r.major,
                "reason": r.reason, "punishment_date": r.punishment_date,
                "impact_start_date": r.impact_start_date, "impact_end_date": r.impact_end_date
            }
    
    if not selected_records:
        return
    
    st.caption(f"☑️ 已选中 {len(selected_records)} 条，已累积入暂存区。如需移除，请在下方暂存区面板操作。")
    
    if len(selected_records) == 1:
        rec = selected_records[0]
        if st.button("✏️ 快捷弹窗编辑", key="btn_edit_sel"):
            _show_edit_dialog(db, rec.id)


def _render_cart_panel():
    """渲染跨域导出暂存区面板，支持逐行勾选移除。"""
    cart = st.session_state.get("admin_export_cart", {})
    if not cart:
        return
    
    st.markdown("---")
    with st.container(border=True):
        st.markdown(f"#### 🗂️ 跨域导出暂存区（已收集 {len(cart)} 条）")
        st.caption("勾选左侧方框可标记单条，点击下方按钮移除已标记项。")
        
        cart_list = list(cart.values())
        cart_ids = [c["id"] for c in cart_list]
        
        view_df = pd.DataFrame([{
            "姓名": c["name"],
            "学号/工号": c["student_id"],
            "单位": c["major"] or ""
        } for c in cart_list])
        
        sel_event = st.dataframe(
            view_df,
            use_container_width=True,
            height=min(260, max(1, len(view_df)) * 35 + 39),
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="cart_sel"
        )
        
        selected_rows = sel_event.selection.rows if sel_event else []
        to_remove = [cart_ids[i] for i in selected_rows if i < len(cart_ids)]
        if to_remove:
            if st.button(f"🗑️ 从暂存区移除已选中的 {len(to_remove)} 条", type="primary", key="btn_remove_from_cart"):
                for rid in to_remove:
                    cart.pop(rid, None)
                st.session_state["cart_clear_nonce"] = st.session_state.get("cart_clear_nonce", 0) + 1
                st.session_state.pop("cart_export_hash", None)
                st.session_state.pop("cart_export_data", None)
                st.session_state.pop("cart_sel", None)
                st.rerun()
        
        c_cart1, c_cart2 = st.columns([2, 1])
        with c_cart1:
            cart_hash = hash(frozenset(cart.keys()))
            if st.session_state.get("cart_export_hash") != cart_hash or st.session_state.get("cart_export_data") is None:
                if st.button("⚡ 准备打包暂存区记录（不影响浏览）", use_container_width=True, key="btn_prep_cart"):
                    export_df = pd.DataFrame([
                        {
                            "序号": i,
                            "姓名": sanitize_for_export(r["name"]),
                            "工号/学号": r["student_id"],
                            "所在单位": sanitize_for_export(r["major"] or ""),
                            "认定结论(文件路径)": sanitize_for_export(r["reason"] or ""),
                            "认定日期": str(r["punishment_date"]) if r["punishment_date"] else "",
                            "处理起至时间": f"{r['impact_start_date']} 至 {r['impact_end_date']}" if r["impact_start_date"] and r["impact_end_date"] else (str(r["impact_start_date"]) if r["impact_start_date"] else (str(r["impact_end_date"]) if r["impact_end_date"] else "")),
                        }
                        for i, r in enumerate(cart_list, 1)
                    ])
                    buf = BytesIO()
                    export_df.to_excel(buf, index=False, engine="openpyxl")
                    st.session_state["cart_export_hash"] = cart_hash
                    st.session_state["cart_export_data"] = buf.getvalue()
                    st.rerun()
            else:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    label="⬇️ 导出暂存区所有记录为 Excel",
                    data=st.session_state["cart_export_data"],
                    file_name=f"导出暂存区_{stamp}.xlsx",
                    mime=MIME_XLSX,
                    key="btn_export_cart",
                    use_container_width=True,
                    type="primary"
                )
        with c_cart2:
            if st.button("🧹 清空暂存区", use_container_width=True, key="btn_clear_cart"):
                st.session_state["admin_export_cart"] = {}
                # 递增 nonce，强制表格重建为零选中，防止 selected_records 回填
                st.session_state["cart_clear_nonce"] = st.session_state.get("cart_clear_nonce", 0) + 1
                st.session_state.pop("cart_export_hash", None)
                st.session_state.pop("cart_export_data", None)
                st.session_state.pop("cart_editor", None)
                st.rerun()


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
    # 智能搜索式选择框：优先从标准院系列表选，也可自定义输入
    _cur_major = rec.major or ""
    _default_idx = UNIT_INPUT_OPTIONS.index(_cur_major) if _cur_major in UNIT_INPUT_OPTIONS else 0
    _major_sel = st.selectbox(LABEL_MAJOR, options=UNIT_INPUT_OPTIONS, index=_default_idx,
                              key="dialog_edit_major_sel")
    if _major_sel == LABEL_CUSTOM_INPUT:
        edit_major = st.text_input("自定义单位名称", value=_cur_major, key="dialog_edit_major_custom")
    elif _major_sel:
        edit_major = _major_sel
    else:
        edit_major = _cur_major
    if rec.reason:
        st.caption(f"当前已有文件：{rec.reason.split('/')[-1]}")
    edit_reason_file = st.file_uploader(f"更新{LABEL_REASON} (PDF)", type=["pdf"])
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
        submit_save = st.button("🚀 保存并更新", type="primary", use_container_width=True)
    with col_cancel:
        if st.button("取消关闭", use_container_width=True):
            st.rerun()

    if submit_save:
        try:
            rec.name = (edit_name or "").strip() or rec.name
            rec.major = (edit_major or "").strip() or None
            if edit_reason_file is not None:
                os.makedirs(os.path.join("static", "pdfs"), exist_ok=True)
                filename = f"{clean_student_id(rec.student_id)}_{int(time.time())}.pdf"
                file_path = os.path.join("static", "pdfs", filename)
                with open(file_path, "wb") as f:
                    f.write(edit_reason_file.getvalue())
                rec.reason = f"/app/static/pdfs/{filename}"
            rec.punishment_date = edit_date
            rec.impact_start_date = edit_impact_start
            rec.impact_end_date = edit_impact_end
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
    
    # 选中操作台（勾选即入库）
    _render_selected_actions(db, selected_records, page_records)
    
    # 暂存区（紧跟快捷操作台正下方）
    _render_cart_panel()
    
    st.markdown("---")
    
    st.caption("需要备份或对账？您也可以选择一次性全量导出👇")
    render_blacklist_export_button(db, 1, fn, fs, fm, sort_key, sort_asc, total, "生效名单", "admin_export_effective")
