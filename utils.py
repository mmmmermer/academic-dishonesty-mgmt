"""
工具模块：身份证处理、Excel 解析、备份逻辑
"""
import os
import shutil
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd

from database import DATABASE_DIR

# 数据库文件路径
DATABASE_PATH = os.path.join(DATABASE_DIR, "database.db")
BACKUPS_DIR = os.path.join(DATABASE_DIR, "backups")

# Excel 黑名单导入必需列
REQUIRED_EXCEL_COLUMNS = ["姓名", "身份证号", "专业", "原因", "处分时间"]

# 全角数字/字母到半角映射（身份证中可能出现的）
FULL_TO_HALF_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
FULL_TO_HALF_X = str.maketrans("ｘＸ", "XX")  # 全角 x/X 统一为半角 X


def clean_id_card(text: Any) -> str:
    """
    清洗身份证号：去除所有空白、全角数字转半角、小写 x 转大写 X。
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    if isinstance(text, str) and text.strip().lower() in ("nan", ""):
        return ""
    s = str(text).strip()
    # 去除所有空白（空格、制表、换行等）
    s = "".join(s.split())
    # 全角数字转半角
    s = s.translate(FULL_TO_HALF_DIGITS)
    # 全角 x/X 转半角 X
    s = s.translate(FULL_TO_HALF_X)
    # 半角小写 x 转大写 X
    s = s.replace("x", "X")
    return s


def mask_id_card(text: Any) -> str:
    """
    身份证脱敏：保留前 3 位与后 4 位，中间用 * 代替。
    例如：320***********1234
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    s = str(text).strip()
    if len(s) <= 7:
        return "*" * len(s) if len(s) > 0 else ""
    return s[:3] + "*" * (len(s) - 7) + s[-4:]


def parse_blacklist_excel(uploaded_file: Any) -> pd.DataFrame:
    """
    解析黑名单 Excel：校验必需列、清洗身份证号后返回 DataFrame。
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
        df = pd.read_excel(io, engine="openpyxl")
    except Exception as e:
        raise ValueError(f"无法读取 Excel 文件，请确认格式为 .xlsx。错误信息：{e!s}") from e

    missing = [c for c in REQUIRED_EXCEL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列：{', '.join(missing)}。请确保表格包含：{', '.join(REQUIRED_EXCEL_COLUMNS)}。")

    # 对身份证号列立即清洗
    df["身份证号"] = df["身份证号"].astype(str).map(clean_id_card)
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
