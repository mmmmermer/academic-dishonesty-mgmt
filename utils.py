"""
工具模块：学号处理、Excel 解析、备份逻辑
"""
import os
import shutil
from datetime import datetime
from io import BytesIO
from typing import Any, Tuple

import pandas as pd  # type: ignore[reportMissingImports]

from database import DATABASE_DIR

try:
    from config import MAX_IMPORT_ROWS, STUDENT_ID_MAX_LEN, STUDENT_ID_MIN_LEN
except ImportError:
    STUDENT_ID_MIN_LEN = 1
    STUDENT_ID_MAX_LEN = 32
    MAX_IMPORT_ROWS = 10000

# 数据库文件路径
DATABASE_PATH = os.path.join(DATABASE_DIR, "database.db")
BACKUPS_DIR = os.path.join(DATABASE_DIR, "backups")

# Excel 黑名单导入必需列
REQUIRED_EXCEL_COLUMNS = ["姓名", "学号", "专业", "原因", "处分时间"]

# 全角数字到半角映射（学号中可能出现的）
FULL_TO_HALF_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def clean_student_id(text: Any) -> str:
    """
    清洗学号：去除所有空白、全角数字转半角。
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    if isinstance(text, str) and text.strip().lower() in ("nan", ""):
        return ""
    s = str(text).strip()
    s = "".join(s.split())
    s = s.translate(FULL_TO_HALF_DIGITS)
    return s


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


def mask_student_id(text: Any) -> str:
    """
    学号脱敏：保留前 3 位与后 4 位，中间用 * 代替。
    例如：202***********1234
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    s = str(text).strip()
    if len(s) <= 7:
        return "*" * len(s) if len(s) > 0 else ""
    return s[:3] + "*" * (len(s) - 7) + s[-4:]


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


def parse_blacklist_excel(uploaded_file: Any) -> pd.DataFrame:
    """
    解析黑名单 Excel：校验必需列、清洗学号后返回 DataFrame。
    使用 pandas + openpyxl 读取。

    :param uploaded_file: 上传的文件对象（支持 .read() 返回 bytes）或文件路径
    :return: 清洗后的 DataFrame
    :raises ValueError: 缺少必要列时抛出中文说明
    """
    try:
        if hasattr(uploaded_file, "read"):
            raw = uploaded_file.read()
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            io = BytesIO(raw)
        else:
            io = uploaded_file
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


# 批量比对用 Excel 至少需要一列学号
BATCH_CHECK_ID_COLUMN = "学号"


def parse_batch_check_excel(uploaded_file: Any) -> pd.DataFrame:
    """
    解析批量比对用 Excel：至少包含「学号」列，清洗后返回。
    可选列：姓名（用于报告显示）。
    """
    try:
        if hasattr(uploaded_file, "read"):
            raw = uploaded_file.read()
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            io = BytesIO(raw)
        else:
            io = uploaded_file
        engine = _get_excel_engine(uploaded_file)
        df = pd.read_excel(io, engine=engine)
    except Exception as e:
        raise ValueError(f"无法读取 Excel 文件，请确认格式为 .xlsx 或 .xls。错误信息：{e!s}") from e

    if BATCH_CHECK_ID_COLUMN not in df.columns:
        raise ValueError("缺少「学号」列。请确保表格至少包含一列：学号。")
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

    :return: 新备份文件的路径
    :raises OSError: 文件操作失败时抛出可读说明
    """
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

    :return: 数据库文件内容（bytes）
    :raises FileNotFoundError: 文件不存在
    :raises OSError: 读取失败
    """
    if not os.path.isfile(DATABASE_PATH):
        raise FileNotFoundError(f"数据库文件不存在：{DATABASE_PATH}")
    try:
        with open(DATABASE_PATH, "rb") as f:
            return f.read()
    except OSError as e:
        raise OSError(f"读取数据库文件失败：{e!s}") from e
