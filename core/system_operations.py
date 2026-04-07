"""
系统级操作模块：包含数据库热备和直接文件操纵
"""
import os
import shutil
from datetime import datetime

from .database import DATABASE_DIR, IS_SQLITE

# 数据库文件路径
DATABASE_PATH = os.path.join(DATABASE_DIR, "database.db")
BACKUPS_DIR = os.path.join(DATABASE_DIR, "backups")


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

    # 只保留最新 7 份——按文件名排序（含时间戳），不依赖 mtime
    try:
        backups = [
            os.path.join(BACKUPS_DIR, f)
            for f in os.listdir(BACKUPS_DIR)
            if f.startswith("database_") and f.endswith(".db")
        ]
        backups.sort(reverse=True)  # 文件名含时间戳，字母序 == 时间序
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
