"""
工具模块：学号处理、Excel 解析、备份逻辑、审计日志写入
"""
import os
import re
import shutil
from datetime import datetime
from io import BytesIO
from typing import Any, Optional, Tuple

import pandas as pd  # type: ignore[reportMissingImports]
import streamlit as st

from .database import DATABASE_DIR, IS_SQLITE, db_session
from .models import AuditLog

try:
    from .config import MAX_IMPORT_ROWS, MAX_UPLOAD_FILE_BYTES, SESSION_KEY_USER_NAME, STUDENT_ID_MAX_LEN, STUDENT_ID_MIN_LEN, LABEL_STUDENT_ID
except ImportError:
    STUDENT_ID_MIN_LEN = 1
    STUDENT_ID_MAX_LEN = 32
    MAX_IMPORT_ROWS = 10000
    MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
    SESSION_KEY_USER_NAME = "user_name"
    LABEL_STUDENT_ID = "工号/学号"

# 数据库文件路径
DATABASE_PATH = os.path.join(DATABASE_DIR, "database.db")
BACKUPS_DIR = os.path.join(DATABASE_DIR, "backups")

# Excel 黑名单导入必需列
REQUIRED_EXCEL_COLUMNS = ["姓名", "学号", "专业", "原因", "处分时间"]

# 全角数字到半角映射（学号中可能出现的）
FULL_TO_HALF_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def cell_str(val: Any) -> str:
    """
    Excel 单元格转字符串，空值/NaN 返回空串。供导入、批量比对等统一使用。
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def sanitize_for_export(val: Any) -> str:
    """对要导出到 Excel 的文本字段进行防 Formula Injection 处理。"""
    s = cell_str(val)
    if s and s[0] in ("=", "+", "-", "@"):
        return f"'{s}"
    return s


def clean_student_id(text: Any) -> str:
    """
    清洗学号：去除所有空白、全角数字转半角，并转为大写（忽略大小写差异）。
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    if isinstance(text, str) and text.strip().lower() in ("nan", ""):
        return ""
    s = str(text).strip()
    s = "".join(s.split())
    s = s.translate(FULL_TO_HALF_DIGITS)
    return s.upper()


def safe_filename(raw: str) -> str:
    """
    将原始字符串净化为安全的文件名片段：仅保留字母、数字、下划线。
    防止路径遍历攻击（如 ../../etc/passwd）。
    """
    import re
    cleaned = clean_student_id(raw)
    return re.sub(r'[^a-zA-Z0-9_]', '', cleaned) or "unknown"


import logging as _logging
_logger = _logging.getLogger(__name__)

# PDF 存储根目录（项目根/static/pdfs）
_PDF_DIR = os.path.join(DATABASE_DIR, "static", "pdfs")


def remove_old_pdf(reason_field: Optional[str]) -> None:
    """
    安全删除旧的 PDF 文件。
    reason_field 值形如 '/app/static/pdfs/xxx.pdf'，需要转换为本地路径后删除。
    仅删除 static/pdfs/ 目录内的文件，防止路径穿越。
    文件不存在或删除失败时静默处理，不影响主流程。
    """
    if not reason_field or not reason_field.strip():
        return
    # 从 URL 路径提取文件名（如 /app/static/pdfs/12345_170000.pdf → 12345_170000.pdf）
    filename = reason_field.rsplit("/", 1)[-1] if "/" in reason_field else reason_field
    if not filename or ".." in filename:
        return
    filepath = os.path.join(_PDF_DIR, filename)
    # 安全校验：确保解析后的绝对路径仍在 static/pdfs 目录内
    abs_path = os.path.abspath(filepath)
    abs_pdf_dir = os.path.abspath(_PDF_DIR)
    if not abs_path.startswith(abs_pdf_dir + os.sep):
        _logger.warning("remove_old_pdf 路径安全校验失败: %s", abs_path)
        return
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            _logger.info("已清理旧 PDF: %s", filename)
    except OSError as e:
        _logger.warning("清理旧 PDF 失败 %s: %s", filename, e)

def validate_student_id(raw: Any) -> Tuple[bool, str]:
    """
    校验学号：清洗后检查长度在允许范围内。
    :param raw: 用户输入的学号（任意类型，会先清洗）
    :return: (是否有效, 错误提示)；有效时错误提示为空字符串
    """
    s = clean_student_id(raw)
    if len(s) < STUDENT_ID_MIN_LEN:
        return False, "学号不能为空。"
    if len(s) > STUDENT_ID_MAX_LEN:
        return False, f"学号长度不能超过 {STUDENT_ID_MAX_LEN} 位。"
    return True, ""


def _get_excel_engine(uploaded_file: Any) -> str:
    """
    根据文件名选择 Excel 引擎：.xls 使用 xlrd，其余使用 openpyxl。
    便于兼容旧版 .xls 格式。
    """
    name = getattr(uploaded_file, "name", None) or ""
    name_lower = (name or "").lower()
    if name_lower.endswith(".xls") and not name_lower.endswith(".xlsx"):
        return "xlrd"
    return "openpyxl"


def _check_file_size(uploaded_file: Any):
    """检查上传文件大小，超过限制则抛 ValueError。"""
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_UPLOAD_FILE_BYTES:
        limit_mb = MAX_UPLOAD_FILE_BYTES / (1024 * 1024)
        raise ValueError(f"上传文件过大（{size / (1024 * 1024):.1f} MB），单次最大 {limit_mb:.0f} MB，请精简后重试。")


def _read_excel_bytes(uploaded_file: Any) -> BytesIO:
    """统一将上传文件转为 BytesIO，供 pandas 读取。"""
    if hasattr(uploaded_file, "read"):
        raw = uploaded_file.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return BytesIO(raw)
    return uploaded_file


# ---------- 智能表头/列侦测工具 ----------

# 已知的学号列名变体（覆盖高校教务系统常见导出格式）
_ID_COLUMN_ALIASES = {"学号", "工号", "工号/学号", "编号", "考号", "考生号", "准考证号", "证件号", "ID", "id", "学工号"}

# 学号特征正则：6~32 位纯数字或字母数字混合串
_RE_LOOKS_LIKE_ID = re.compile(r"^\s*[A-Za-z0-9]{6,32}\s*$")


def _detect_id_column_index(df_raw: pd.DataFrame) -> Optional[int]:
    """
    在无表头（header=None）的 DataFrame 中，通过数据特征推断哪一列最可能是学号列。
    策略：取前 5 行样本，对每一列统计"看起来像学号"的比例，取最高者。
    仅当匹配率 >= 60% 时才认定，避免误判。
    返回列索引（int），无法判定时返回 None。
    """
    if df_raw.empty:
        return None
    sample = df_raw.head(min(5, len(df_raw)))
    best_col = None
    best_rate = 0.0
    for col_idx in range(len(df_raw.columns)):
        col_data = sample.iloc[:, col_idx].astype(str)
        match_count = sum(1 for v in col_data if _RE_LOOKS_LIKE_ID.match(v))
        rate = match_count / len(col_data)
        if rate > best_rate:
            best_rate = rate
            best_col = col_idx
    if best_rate >= 0.6 and best_col is not None:
        return best_col
    return None


def _try_find_header_row(df_raw: pd.DataFrame) -> Optional[int]:
    """
    在无表头的 DataFrame 前 3 行中查找是否存在已知的列名关键字（如"学号""工号"等）。
    若找到，返回该行的行号索引；否则返回 None。
    """
    scan_rows = min(3, len(df_raw))
    for row_idx in range(scan_rows):
        row_values = {str(v).strip() for v in df_raw.iloc[row_idx]}
        if row_values & _ID_COLUMN_ALIASES:
            return row_idx
    return None


# ---------- 黑名单导入解析 ----------

def parse_blacklist_excel(uploaded_file: Any) -> pd.DataFrame:
    """
    解析黑名单 Excel：校验必需列、清洗学号后返回 DataFrame。
    使用 pandas + openpyxl 读取。

    :param uploaded_file: 上传的文件对象（支持 .read() 返回 bytes）或文件路径
    :return: 清洗后的 DataFrame
    :raises ValueError: 缺少必要列时抛出中文说明
    """
    _check_file_size(uploaded_file)
    try:
        io = _read_excel_bytes(uploaded_file)
        engine = _get_excel_engine(uploaded_file)
        df = pd.read_excel(io, engine=engine)
    except Exception as e:
        raise ValueError(f"无法读取 Excel 文件，请确认格式为 .xlsx 或 .xls。错误信息：{e!s}") from e

    missing = [c for c in REQUIRED_EXCEL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列：{', '.join(missing)}。请确保表格包含：{', '.join(REQUIRED_EXCEL_COLUMNS)}。")
    if len(df) > MAX_IMPORT_ROWS:
        raise ValueError(f"单次导入不得超过 {MAX_IMPORT_ROWS} 行，请分批导入。")

    df["学号"] = df["学号"].astype(str).map(clean_student_id)
    return df


# ---------- 批量比对解析（智能启发式侦测） ----------

# 批量比对用 Excel 至少需要一列学号
BATCH_CHECK_ID_COLUMN = "学号"


def parse_batch_check_excel(uploaded_file: Any) -> pd.DataFrame:
    """
    解析批量比对用 Excel，支持三级智能侦测：
      1. 标准模式：第一行为表头，直接定位"学号"列。
      2. 表头偏移：前 3 行中隐含已知列名关键字（学号/工号/编号等），自动识别。
      3. 盲推断：无任何表头时，通过正则特征扫描自动识别最像学号的列。
    """
    _check_file_size(uploaded_file)
    try:
        io = _read_excel_bytes(uploaded_file)
        engine = _get_excel_engine(uploaded_file)
        # 第一序列：标准读取（pandas 默认将第一行作为表头）
        df = pd.read_excel(io, engine=engine)
    except Exception as e:
        raise ValueError(f"无法读取 Excel 文件，请确认格式为 .xlsx 或 .xls。错误信息：{e!s}") from e

    # 标准模式命中：列名中直接包含已知别名
    matched_col = None
    for alias in _ID_COLUMN_ALIASES:
        if alias in df.columns:
            matched_col = alias
            break

    if matched_col is not None:
        # 直接命中标准表头
        if matched_col != BATCH_CHECK_ID_COLUMN:
            df = df.rename(columns={matched_col: BATCH_CHECK_ID_COLUMN})
    else:
        # 未命中标准表头 → 回退为无表头盲读
        _logger.info("批量比对：标准表头未命中，启动智能侦测...")
        try:
            io.seek(0)
            df_raw = pd.read_excel(io, engine=engine, header=None)
        except Exception as e:
            raise ValueError(f"无法读取 Excel 文件：{e!s}") from e

        # 第二序列：在前 3 行搜索已知列名关键字
        header_row = _try_find_header_row(df_raw)
        if header_row is not None:
            # 找到了隐藏的表头行，将其晋升为表头
            new_headers = [str(v).strip() for v in df_raw.iloc[header_row]]
            df = df_raw.iloc[header_row + 1:].reset_index(drop=True)
            df.columns = new_headers
            # 再次查找匹配的列名
            for alias in _ID_COLUMN_ALIASES:
                if alias in df.columns:
                    if alias != BATCH_CHECK_ID_COLUMN:
                        df = df.rename(columns={alias: BATCH_CHECK_ID_COLUMN})
                    break
            else:
                raise ValueError(
                    f"表格检测到表头行，但仍未找到{LABEL_STUDENT_ID}列。"
                    f"请确保表头包含以下任一列名：{', '.join(sorted(_ID_COLUMN_ALIASES))}。"
                )
        else:
            # 第三序列：盲推断，通过数据特征识别学号列
            id_col_idx = _detect_id_column_index(df_raw)
            if id_col_idx is not None:
                _logger.info("批量比对：盲推断命中第 %d 列为学号列", id_col_idx)
                # 将盲读的 DataFrame 重新命名，学号列命名为标准列名
                df = df_raw.copy()
                col_names = []
                # 尝试基于 REQUIRED_EXCEL_COLUMNS 和学号列的相对位置来猜测周围列的含义
                id_idx_in_req = REQUIRED_EXCEL_COLUMNS.index(BATCH_CHECK_ID_COLUMN) if BATCH_CHECK_ID_COLUMN in REQUIRED_EXCEL_COLUMNS else 1
                for i in range(len(df.columns)):
                    if i == id_col_idx:
                        col_names.append(BATCH_CHECK_ID_COLUMN)
                    else:
                        req_idx = id_idx_in_req + (i - id_col_idx)
                        if 0 <= req_idx < len(REQUIRED_EXCEL_COLUMNS):
                            col_names.append(REQUIRED_EXCEL_COLUMNS[req_idx])
                        else:
                            col_names.append(f"未知列_{i + 1}")
                df.columns = col_names
            else:
                raise ValueError(
                    f"未能在表格中识别出{LABEL_STUDENT_ID}列。\n"
                    f"请确保表格第一行为表头（包含「{LABEL_STUDENT_ID}」），"
                    f"或数据中至少有一列为 6 位以上的数字编号。"
                )

    # 统一校验与清洗
    if BATCH_CHECK_ID_COLUMN not in df.columns:
        raise ValueError(
            f"缺少「{LABEL_STUDENT_ID}」列。请确保表格至少包含一列：{LABEL_STUDENT_ID}。"
        )
    if len(df) > MAX_IMPORT_ROWS:
        raise ValueError(f"批量比对单次不得超过 {MAX_IMPORT_ROWS} 行，请分批上传。")

    df = df.copy()
    df[BATCH_CHECK_ID_COLUMN] = df[BATCH_CHECK_ID_COLUMN].astype(str).map(clean_student_id)
    df = df[df[BATCH_CHECK_ID_COLUMN].str.len() > 0].reset_index(drop=True)
    return df


def auto_backup() -> str:
    """
    将 database.db 复制到 backups/ 目录，文件名带时间戳。
    仅保留最近 7 份备份，删除更早的。
    使用 MySQL/PostgreSQL 时本函数直接返回，不执行文件备份（请使用 mysqldump/pg_dump 等）。

    :return: 新备份文件的路径（SQLite）；非 SQLite 时返回空字符串
    :raises OSError: 文件操作失败时抛出可读说明
    """
    if not IS_SQLITE:
        return ""

    try:
        if not os.path.isdir(BACKUPS_DIR):
            os.makedirs(BACKUPS_DIR, exist_ok=True)
    except OSError as e:
        raise OSError(f"无法创建备份目录 {BACKUPS_DIR}：{e!s}") from e

    if not os.path.isfile(DATABASE_PATH):
        raise FileNotFoundError(f"数据库文件不存在：{DATABASE_PATH}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"database_{stamp}.db"
    backup_path = os.path.join(BACKUPS_DIR, backup_name)

    try:
        shutil.copy2(DATABASE_PATH, backup_path)
    except OSError as e:
        raise OSError(f"复制数据库到备份失败：{e!s}") from e

    # 只保留最新 7 份
    try:
        backups = [
            os.path.join(BACKUPS_DIR, f)
            for f in os.listdir(BACKUPS_DIR)
            if f.startswith("database_") and f.endswith(".db")
        ]
        backups.sort(key=os.path.getmtime, reverse=True)
        for old in backups[7:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass

    return backup_path


def get_db_file_bytes() -> bytes:
    """
    以二进制模式读取当前 database.db 文件，供下载按钮使用。
    仅在使用 SQLite 时有效；使用 MySQL/PostgreSQL 时请通过 mysqldump/pg_dump 备份。

    :return: 数据库文件内容（bytes）
    :raises FileNotFoundError: 文件不存在
    :raises OSError: 读取失败
    :raises NotImplementedError: 当前使用非 SQLite，不支持文件下载备份
    """
    if not IS_SQLITE:
        raise NotImplementedError(
            "当前数据库为 MySQL/PostgreSQL，不支持下载 .db 文件。请使用 mysqldump 或 pg_dump 进行备份。"
        )
    if not os.path.isfile(DATABASE_PATH):
        raise FileNotFoundError(f"数据库文件不存在：{DATABASE_PATH}")
    try:
        with open(DATABASE_PATH, "rb") as f:
            return f.read()
    except OSError as e:
        raise OSError(f"读取数据库文件失败：{e!s}") from e


def log_audit_action(action_type: str, target: str = "", details: str = ""):
    """写入审计日志，供所有视图层统一调用。操作人从 session_state 获取。"""
    with db_session() as db:
        try:
            name = st.session_state.get(SESSION_KEY_USER_NAME, "未知")
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
