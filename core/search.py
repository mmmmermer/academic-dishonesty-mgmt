from __future__ import annotations

"""
名单查询核心：统一处理姓名规范化、拼音匹配、多人输入拆分与教师端单条查询匹配。
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from core.models import Blacklist
from core.search_config import (
    PINYIN_ABBR_EXACT_MIN_LEN,
    PINYIN_PREFIX_MIN_LEN,
    PINYIN_SUBSTRING_MIN_LEN,
    SEARCH_INPUT_MAX_LENGTH,
    SEARCH_MATCH_RANKS,
    SEARCH_RESULT_HARD_LIMIT,
    SEARCH_TERM_MAX_COUNT,
    TEACHER_TERM_CANDIDATE_LIMIT,
)
from core.student_id import clean_student_id

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - 运行环境缺依赖时退化为仅中文搜索
    Style = None
    lazy_pinyin = None


_RE_CJK = re.compile(r"[\u4e00-\u9fff]")
_RE_ALPHA = re.compile(r"[A-Za-z]")
_RE_EXPLICIT_SEPARATORS = re.compile(r"[\n,，、;；]+")
_RE_ID_TOKEN = re.compile(r"[A-Za-z0-9]{6,}")

MATCH_STUDENT_ID_EXACT = "student_id_exact"
MATCH_NAME_EXACT = "name_exact"
MATCH_NAME_PARTIAL = "name_partial"
MATCH_PINYIN_FULL = "pinyin_full"
MATCH_PINYIN_ABBR = "pinyin_abbr"
MATCH_PINYIN_PREFIX = "pinyin_prefix"
MATCH_PINYIN_ABBR_PREFIX = "pinyin_abbr_prefix"
MATCH_PINYIN_SUBSTRING = "pinyin_substring"

MATCH_RANKS = SEARCH_MATCH_RANKS
_BLACKLIST_COLUMNS_CACHE: dict[str, set[str]] = {}


@dataclass(frozen=True)
class SearchInput:
    raw: str
    name_query: str | None = None
    student_id: str | None = None


def _like_escape(text: str) -> str:
    if not text:
        return text
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sql_like_escape(text: str) -> str:
    """Escape %/_ with a stable one-char escape marker for text SQL filters."""
    if not text:
        return text
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def normalize_name_text(text: str) -> str:
    """姓名统一去空白，避免「张 伟」与「张伟」被视为不同输入。"""
    return "".join((text or "").split())


def normalize_pinyin_text(text: str) -> str:
    """拼音搜索统一移除空格与非字母字符，并转为小写。"""
    return re.sub(r"[^a-z]", "", (text or "").lower())


def normalize_search_input_text(text: str) -> str:
    """统一处理搜索输入长度与首尾空白，避免异常超长输入拖慢查询。"""
    value = (text or "").strip()
    if len(value) > SEARCH_INPUT_MAX_LENGTH:
        return value[:SEARCH_INPUT_MAX_LENGTH]
    return value


def split_search_terms(text: str) -> list[str]:
    """
    将用户输入按明确分隔符拆成多个检索词。
    仅使用换行、逗号、顿号、分号分隔，保留姓名内部空格。
    """
    normalized_input = normalize_search_input_text(text)
    if not normalized_input:
        return []
    normalized = _RE_EXPLICIT_SEPARATORS.sub("\n", normalized_input)
    terms: list[str] = []
    for part in normalized.split("\n"):
        val = " ".join(part.split())
        if not val:
            continue
        terms.append(val)
        if len(terms) >= SEARCH_TERM_MAX_COUNT:
            break
    return terms


def split_student_id_terms(text: str) -> list[str]:
    """学号/工号筛选允许空白和常见标点分隔。"""
    normalized_input = normalize_search_input_text(text)
    if not normalized_input:
        return []
    normalized = _RE_EXPLICIT_SEPARATORS.sub(" ", normalized_input)
    values: list[str] = []
    seen: set[str] = set()
    for part in normalized.split():
        sid = clean_student_id(part)
        if sid and sid not in seen:
            values.append(sid)
            seen.add(sid)
            if len(values) >= SEARCH_TERM_MAX_COUNT:
                break
    return values


def detect_input_type(text: str) -> str:
    """判断输入更接近中文姓名还是拼音。"""
    has_cjk = bool(_RE_CJK.search(text or ""))
    has_alpha = bool(_RE_ALPHA.search(text or ""))
    if has_cjk and not has_alpha:
        return "chinese"
    if has_alpha and not has_cjk:
        return "pinyin"
    return "mixed"


def has_pinyin_terms(terms: Iterable[str]) -> bool:
    return any(detect_input_type(term) != "chinese" for term in terms if term)


def should_use_python_name_scan(terms: Iterable[str]) -> bool:
    """拼音和混合输入需要在 Python 层做匹配。"""
    return has_pinyin_terms(terms)


def build_chinese_name_sql_conditions(name_terms: list[str]):
    """
    为纯中文/中文含空格检索构建 SQL LIKE 条件。
    遇到拼音或混合输入时返回 None，由上层改走 Python 扫描。
    """
    from sqlalchemy import func, or_

    conditions = []
    for term in name_terms:
        if detect_input_type(term) != "chinese":
            return None
        normalized = normalize_name_text(term)
        if normalized:
            conditions.append(
                func.replace(Blacklist.name, " ", "").like(
                    f"%{_like_escape(normalized)}%",
                    escape="\\",
                )
            )
    return or_(*conditions) if conditions else None


def get_blacklist_column_names(bind) -> set[str]:
    """Inspect database columns for blacklist table with a small in-process cache."""
    from sqlalchemy import inspect

    key = str(getattr(bind, "url", ""))
    cached = _BLACKLIST_COLUMNS_CACHE.get(key)
    if cached is not None:
        return cached

    columns = {col["name"] for col in inspect(bind).get_columns("blacklist")}
    _BLACKLIST_COLUMNS_CACHE[key] = columns
    return columns


def has_search_helper_columns(bind) -> bool:
    names = get_blacklist_column_names(bind)
    return {"name_norm", "name_pinyin_full", "name_abbr"}.issubset(names)


def build_name_terms_sql_filter(
    name_terms: list[str],
    *,
    include_helper_columns: bool,
    prefix_min_len: int | None = None,
):
    """
    Build SQL filter + bound parameters for mixed Chinese/Pinyin search terms.
    Returns (None, {}) when no usable condition can be built.
    """
    from sqlalchemy import text

    clauses: list[str] = []
    params: dict[str, str] = {}
    prefix_threshold = PINYIN_PREFIX_MIN_LEN if prefix_min_len is None else max(1, prefix_min_len)

    for idx, term in enumerate(name_terms):
        kind = detect_input_type(term)

        if kind == "chinese":
            normalized = normalize_name_text(term)
            if not normalized:
                continue
            like_val = f"%{_sql_like_escape(normalized)}%"
            key = f"cn_{idx}"
            params[key] = like_val
            if include_helper_columns:
                clauses.append(
                    f"(replace(name, ' ', '') LIKE :{key} ESCAPE '!' OR name_norm LIKE :{key} ESCAPE '!')"
                )
            else:
                clauses.append(f"(replace(name, ' ', '') LIKE :{key} ESCAPE '!')")
            continue

        if kind == "pinyin":
            normalized = normalize_pinyin_text(term)
            if not normalized:
                continue

            per_term: list[str] = []

            # 始终添加姓名直接 LIKE 匹配降级（支持纯字母姓名如 "ASD"）
            key_name_like = f"nl_{idx}"
            params[key_name_like] = f"%{_sql_like_escape(term.strip())}%"
            per_term.append(f"name LIKE :{key_name_like} ESCAPE '!'")

            if include_helper_columns:
                key_full = f"pf_eq_{idx}"
                params[key_full] = normalized
                per_term.append(f"name_pinyin_full = :{key_full}")

                if len(normalized) >= PINYIN_ABBR_EXACT_MIN_LEN:
                    key_abbr = f"pa_eq_{idx}"
                    params[key_abbr] = normalized
                    per_term.append(f"name_abbr = :{key_abbr}")

                if len(normalized) >= prefix_threshold:
                    key_full_prefix = f"pf_pre_{idx}"
                    key_abbr_prefix = f"pa_pre_{idx}"
                    params[key_full_prefix] = f"{_sql_like_escape(normalized)}%"
                    params[key_abbr_prefix] = f"{_sql_like_escape(normalized)}%"
                    per_term.append(f"name_pinyin_full LIKE :{key_full_prefix} ESCAPE '!'")
                    per_term.append(f"name_abbr LIKE :{key_abbr_prefix} ESCAPE '!'")

                if len(normalized) >= PINYIN_SUBSTRING_MIN_LEN:
                    key_full_sub = f"pf_sub_{idx}"
                    params[key_full_sub] = f"%{_sql_like_escape(normalized)}%"
                    per_term.append(f"name_pinyin_full LIKE :{key_full_sub} ESCAPE '!'")

            if per_term:
                clauses.append("(" + " OR ".join(per_term) + ")")

    if not clauses:
        return None, {}

    sql_expr = text("(" + " OR ".join(clauses) + ")")
    return sql_expr, params


def _is_probable_student_id(token: str) -> bool:
    cleaned = clean_student_id(token)
    return len(cleaned) >= 6 and any(ch.isdigit() for ch in cleaned)


def parse_teacher_search_line(line: str) -> SearchInput | None:
    """
    解析教师端一行输入：
    - 含数字的 6 位以上字母数字串视为学号/工号
    - 纯字母输入始终视为拼音姓名，不误判为学号
    """
    normalized_line = " ".join((line or "").split())
    if not normalized_line:
        return None

    for match in _RE_ID_TOKEN.finditer(normalized_line):
        candidate = match.group(0)
        if not _is_probable_student_id(candidate):
            continue
        sid = clean_student_id(candidate)
        name_query = " ".join(
            (normalized_line[: match.start()] + " " + normalized_line[match.end() :]).split()
        )
        return SearchInput(
            raw=normalized_line,
            name_query=name_query or None,
            student_id=sid or None,
        )

    return SearchInput(raw=normalized_line, name_query=normalized_line, student_id=None)


def parse_teacher_search_inputs(text: str) -> list[SearchInput]:
    results: list[SearchInput] = []
    for term in split_search_terms(text):
        item = parse_teacher_search_line(term)
        if item is not None:
            results.append(item)
    return results


@lru_cache(maxsize=4096)
def compute_name_fields(name: str) -> dict[str, str]:
    """为姓名预计算标准化、全拼和首字母，供模糊查询复用。"""
    name_normalized = normalize_name_text(name)
    if not name_normalized or lazy_pinyin is None or Style is None:
        return {
            "name_normalized": name_normalized,
            "name_pinyin": "",
            "name_abbr": "",
        }

    name_pinyin = "".join(lazy_pinyin(name_normalized)).lower()
    name_abbr = "".join(
        lazy_pinyin(name_normalized, style=Style.FIRST_LETTER)
    ).lower()
    return {
        "name_normalized": name_normalized,
        "name_pinyin": name_pinyin,
        "name_abbr": name_abbr,
    }


def sync_blacklist_search_helper_fields(
    db,
    *,
    name: str,
    record_id: int | None = None,
    student_id: str | None = None,
) -> bool:
    """Sync search helper columns for one blacklist row when columns exist."""
    if not name:
        return False

    bind = db.get_bind()
    if not has_search_helper_columns(bind):
        return False

    if record_id is None and not student_id:
        return False

    from sqlalchemy import text

    fields = compute_name_fields(name)
    params: dict[str, str | int] = {
        "name_norm": fields["name_normalized"],
        "name_pinyin_full": fields["name_pinyin"],
        "name_abbr": fields["name_abbr"],
    }

    if record_id is not None:
        params["record_id"] = record_id
        db.execute(
            text(
                """
                UPDATE blacklist
                SET name_norm = :name_norm,
                    name_pinyin_full = :name_pinyin_full,
                    name_abbr = :name_abbr
                WHERE id = :record_id
                """
            ),
            params,
        )
        return True

    sid = clean_student_id(student_id or "")
    if not sid:
        return False
    params["student_id"] = sid
    db.execute(
        text(
            """
            UPDATE blacklist
            SET name_norm = :name_norm,
                name_pinyin_full = :name_pinyin_full,
                name_abbr = :name_abbr
            WHERE id_card = :student_id
            """
        ),
        params,
    )
    return True


def sync_blacklist_record_search_helper_fields(db, record: Blacklist) -> bool:
    """Sync helper columns by ORM row object; flushes only when row id is missing."""
    if not record:
        return False
    if not has_search_helper_columns(db.get_bind()):
        return False
    if record.id is None:
        db.flush()
    return sync_blacklist_search_helper_fields(
        db,
        name=record.name or "",
        record_id=record.id,
    )


def match_name_query(
    record_name: str,
    query: str,
    *,
    prefix_min_len: int = PINYIN_PREFIX_MIN_LEN,
) -> tuple[int, str] | None:
    """对单条记录姓名做中文 / 拼音 / 首字母匹配。"""
    if not query or not query.strip():
        return None

    query_type = detect_input_type(query)
    fields = compute_name_fields(record_name)

    if query_type == "chinese":
        normalized_query = normalize_name_text(query)
        if not normalized_query:
            return None
        if normalized_query == fields["name_normalized"]:
            return MATCH_RANKS[MATCH_NAME_EXACT], MATCH_NAME_EXACT
        if normalized_query in fields["name_normalized"]:
            return MATCH_RANKS[MATCH_NAME_PARTIAL], MATCH_NAME_PARTIAL
        return None

    if query_type == "pinyin":
        normalized_query = normalize_pinyin_text(query)
        if not normalized_query:
            return None

        # 直接姓名子串匹配降级（支持纯字母姓名如 "ASD"）
        name_upper = fields["name_normalized"].upper()
        query_upper = query.strip().upper()
        if query_upper and query_upper in name_upper:
            if query_upper == name_upper:
                return MATCH_RANKS[MATCH_NAME_EXACT], MATCH_NAME_EXACT
            return MATCH_RANKS[MATCH_NAME_PARTIAL], MATCH_NAME_PARTIAL

        # 拼音匹配（需要拼音字段非空）
        if fields["name_pinyin"]:
            prefix_threshold = max(1, prefix_min_len)
            if normalized_query == fields["name_pinyin"]:
                return MATCH_RANKS[MATCH_PINYIN_FULL], MATCH_PINYIN_FULL
            if len(normalized_query) >= PINYIN_ABBR_EXACT_MIN_LEN and normalized_query == fields["name_abbr"]:
                return MATCH_RANKS[MATCH_PINYIN_ABBR], MATCH_PINYIN_ABBR
            if len(normalized_query) >= prefix_threshold and fields["name_pinyin"].startswith(normalized_query):
                return MATCH_RANKS[MATCH_PINYIN_PREFIX], MATCH_PINYIN_PREFIX
            if len(normalized_query) >= prefix_threshold and fields["name_abbr"].startswith(normalized_query):
                return MATCH_RANKS[MATCH_PINYIN_ABBR_PREFIX], MATCH_PINYIN_ABBR_PREFIX
            if len(normalized_query) >= PINYIN_SUBSTRING_MIN_LEN and normalized_query in fields["name_pinyin"]:
                return MATCH_RANKS[MATCH_PINYIN_SUBSTRING], MATCH_PINYIN_SUBSTRING
        return None

    return None


def match_teacher_input(record: Blacklist, item: SearchInput) -> tuple[int, str] | None:
    """将教师端一行输入与一条记录做匹配。"""
    if item.student_id:
        if record.student_id != item.student_id:
            return None
        if not item.name_query:
            return MATCH_RANKS[MATCH_STUDENT_ID_EXACT], MATCH_STUDENT_ID_EXACT
        name_match = match_name_query(record.name, item.name_query)
        if name_match is None:
            return None
        rank, mode = name_match
        return rank + 1, f"{MATCH_STUDENT_ID_EXACT}+{mode}"

    if item.name_query:
        return match_name_query(record.name, item.name_query)
    return None


def search_teacher_records(
    records: Iterable[Blacklist], search_inputs: list[SearchInput], result_limit: int = SEARCH_RESULT_HARD_LIMIT
) -> tuple[list[Blacklist], set[str]]:
    """在已取出的记录集合上执行教师端搜索，并返回命中的模式集合。"""
    matched: list[tuple[int, str, str, Blacklist]] = []
    modes: set[str] = set()

    for record in records:
        best_match: tuple[int, str] | None = None
        for item in search_inputs:
            current = match_teacher_input(record, item)
            if current is None:
                continue
            if best_match is None or current[0] < best_match[0]:
                best_match = current
        if best_match is None:
            continue
        rank, mode = best_match
        modes.add(mode)
        matched.append(
            (
                rank,
                compute_name_fields(record.name)["name_normalized"],
                record.student_id,
                record,
            )
        )

    matched.sort(key=lambda item: (item[0], item[1], item[2]))
    sorted_records = [item[3] for item in matched]
    if result_limit > 0:
        sorted_records = sorted_records[:result_limit]
    return sorted_records, modes


def fetch_teacher_candidate_records(
    db,
    search_inputs: list[SearchInput],
    *,
    status: int = 1,
    per_input_limit: int = TEACHER_TERM_CANDIDATE_LIMIT,
) -> list[Blacklist]:
    """
    Fetch candidate rows from DB first, then let Python matcher do final scoring.
    This avoids loading the entire active list for each teacher query.
    """
    helper_columns = has_search_helper_columns(db.get_bind())
    need_full_scan = False
    candidate_by_id: dict[int, Blacklist] = {}

    for item in search_inputs:
        query = db.query(Blacklist).filter(Blacklist.status == status)

        if item.student_id:
            query = query.filter(Blacklist.student_id == item.student_id)
            if item.name_query:
                cond, params = build_name_terms_sql_filter(
                    [item.name_query],
                    include_helper_columns=helper_columns,
                )
                if cond is not None:
                    query = query.filter(cond).params(**params)
            rows = query.limit(max(10, min(per_input_limit, 100))).all()
            for row in rows:
                candidate_by_id[row.id] = row
            continue

        if not item.name_query:
            continue

        cond, params = build_name_terms_sql_filter(
            [item.name_query],
            include_helper_columns=helper_columns,
        )
        if cond is None:
            if detect_input_type(item.name_query) != "chinese":
                # SQLite (or old schema) without helper columns still needs pinyin fallback.
                need_full_scan = True
            continue

        rows = query.filter(cond).params(**params).limit(per_input_limit).all()
        for row in rows:
            candidate_by_id[row.id] = row

    if need_full_scan:
        for row in db.query(Blacklist).filter(Blacklist.status == status).all():
            candidate_by_id[row.id] = row

    return list(candidate_by_id.values())


def filter_record_ids_by_name_terms(
    records: Iterable[Blacklist],
    name_terms: list[str],
    *,
    prefix_min_len: int = PINYIN_PREFIX_MIN_LEN,
) -> list[int]:
    """管理员名单查询在 Python 层匹配拼音/首字母时使用。"""
    matched: list[tuple[int, int]] = []
    for record in records:
        best_match: tuple[int, str] | None = None
        for term in name_terms:
            current = match_name_query(record.name, term, prefix_min_len=prefix_min_len)
            if current is None:
                continue
            if best_match is None or current[0] < best_match[0]:
                best_match = current
        if best_match is not None:
            matched.append((best_match[0], record.id))

    matched.sort(key=lambda item: (item[0], item[1]))
    return [item[1] for item in matched]
