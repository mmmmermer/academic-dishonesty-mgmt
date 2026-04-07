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
from views.components import render_blacklist_table
from core.student_id import validate_student_id
from core.audit_logger import log_audit_action
from core.config import (
    AUDIT_QUERY_SINGLE,
    LABEL_STUDENT_ID,
    MSG_NO_RECORD_GOOD,
    MSG_TRY_AGAIN,
    TERM_DISHONEST_RECORD,
)

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

    if not search_clicked:
        return

    raw_text = normalize_search_input_text(search_input)
    if not raw_text:
        st.error(f"请输入姓名或{LABEL_STUDENT_ID}后再查询。")
        return

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

    log_audit_action(
        AUDIT_QUERY_SINGLE,
        target="",
        details=f"查询 {len(parsed_inputs)} 人，命中 {len(unique_records)} 人",
    )

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

    st.error(f"共查 {len(parsed_inputs)} 人，命中 {len(unique_records)} 条{TERM_DISHONEST_RECORD}，请核实。")
    render_blacklist_table(unique_records, page_size=max(1, len(unique_records)), current_page=0)
