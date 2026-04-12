import streamlit as st
from core.database import db_session
from core.search import (
    fetch_teacher_candidate_records,
    MATCH_NAME_EXACT,
    MATCH_STUDENT_ID_EXACT,
    normalize_search_input_text,
    parse_teacher_search_inputs,
    search_teacher_records,
)
from core.search_config import SEARCH_INPUT_MAX_LENGTH
from views.components import render_blacklist_table, render_record_detail_card, render_pagination, clamp_page
from core.student_id import validate_student_id
from core.audit_logger import log_audit_action
from core.config import (
    AUDIT_QUERY_SINGLE,
    LABEL_STUDENT_ID,
    MSG_NO_RECORD_GOOD,
    MSG_TRY_AGAIN,
    TERM_DISHONEST_RECORD,
)

# 单条查询结果每页条数
_SINGLE_SEARCH_PAGE_SIZE = 20

# session_state 键名
_SS_RESULTS = "_single_search_results"
_SS_MODES = "_single_search_modes"
_SS_QUERY_TEXT = "_single_search_query"
_SS_PARSED_COUNT = "_single_search_parsed_count"


def _do_search(raw_text: str):
    """执行搜索并将结果存入 session_state，后续 rerun 时仍可渲染。"""
    parsed_inputs = parse_teacher_search_inputs(raw_text)
    if not parsed_inputs:
        st.error(f"请输入至少一个姓名或{LABEL_STUDENT_ID}。")
        return

    for item in parsed_inputs:
        if item.student_id:
            ok, err = validate_student_id(item.student_id)
            if not ok:
                st.error(err or f"{LABEL_STUDENT_ID}格式有误，请检查后重试。")
                return

    with db_session() as db:
        try:
            with st.spinner("正在查询..."):
                candidate_records = fetch_teacher_candidate_records(
                    db,
                    parsed_inputs,
                    status=1,
                )
                unique_records, matched_modes = search_teacher_records(candidate_records, parsed_inputs)
        except Exception:
            st.error("查询失败，" + MSG_TRY_AGAIN)
            return

    # 审计日志
    _query_names = ", ".join(
        (p.name_query or p.student_id or "?")[:10] for p in parsed_inputs[:5]
    )
    if len(parsed_inputs) > 5:
        _query_names += f" 等{len(parsed_inputs)}人"
    log_audit_action(
        AUDIT_QUERY_SINGLE,
        target=_query_names,
        details=f"查询 {len(parsed_inputs)} 人，命中 {len(unique_records)} 人",
    )

    # 存入 session_state
    st.session_state[_SS_RESULTS] = unique_records
    st.session_state[_SS_MODES] = matched_modes
    st.session_state[_SS_QUERY_TEXT] = raw_text
    st.session_state[_SS_PARSED_COUNT] = len(parsed_inputs)


def _render_results():
    """从 session_state 取出结果并渲染（支持 rerun 后仍显示）。"""
    unique_records = st.session_state.get(_SS_RESULTS)
    matched_modes = st.session_state.get(_SS_MODES, set())
    parsed_count = st.session_state.get(_SS_PARSED_COUNT, 0)

    if unique_records is None:
        return

    st.toast(f"查询完成：命中 {len(unique_records)} 条")

    if not unique_records:
        st.success(MSG_NO_RECORD_GOOD)
        return

    non_exact_modes = {
        mode
        for mode in matched_modes
        if not (
            mode == MATCH_STUDENT_ID_EXACT
            or mode.endswith(f"+{MATCH_NAME_EXACT}")
            or mode == MATCH_NAME_EXACT
        )
    }
    if non_exact_modes:
        st.info("当前结果包含模糊或拼音匹配，请结合学号/工号进一步核实。")

    st.warning(f"共查 {parsed_count} 人，命中 {len(unique_records)} 条{TERM_DISHONEST_RECORD}，请核实。")

    # 分页展示
    total = len(unique_records)
    page_size = _SINGLE_SEARCH_PAGE_SIZE
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = clamp_page("teacher_single_page", total_pages)
    page_start = page * page_size
    page_end = min(page_start + page_size, total)
    page_records = unique_records[page_start:page_end]

    # 表格总览
    render_blacklist_table(page_records, page_size, page)

    if total_pages > 1:
        render_pagination("teacher_single_page", page, total_pages, total, len(page_records))

    # 结果仅 1 条：自动展开详情卡片（含 PDF 预览），无需额外点击
    if total == 1:
        render_record_detail_card(unique_records[0], key_prefix="teacher_single_detail")
    elif total > 1:
        # 多条结果：每条一个可展开的详情面板
        st.caption("点击下方展开按钮查看完整详情与 PDF 公示文件：")
        for idx, rec in enumerate(page_records):
            with st.expander(f"📋 {rec.name}（{rec.student_id}）— {rec.major or '未填写'}", expanded=False):
                render_record_detail_card(rec, key_prefix=f"teacher_single_detail_{page}_{idx}")


def render_single_search():
    """单条查询：支持中文姓名、学号/工号、拼音/首字母；多人请用显式分隔符。"""
    with st.form("teacher_single_search_form"):
        search_input = st.text_area(
            f"请输入姓名或{LABEL_STUDENT_ID}",
            key="teacher_search",
            placeholder=(
                f"支持姓名、拼音全拼、拼音首字母、{LABEL_STUDENT_ID}\n"
                f"多人请用换行、逗号、顿号或分号分隔；如需联合检索可写「姓名 {LABEL_STUDENT_ID}」"
            ),
            height=120,
            max_chars=SEARCH_INPUT_MAX_LENGTH,
        )
        search_clicked = st.form_submit_button("查询")

    if search_clicked:
        raw_text = normalize_search_input_text(search_input)
        if not raw_text:
            st.error(f"请输入姓名或{LABEL_STUDENT_ID}后再查询。")
            return
        _do_search(raw_text)

    # 渲染结果（无论是刚搜的还是 rerun 时从 session_state 取的）
    _render_results()
