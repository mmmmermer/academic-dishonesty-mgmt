"""
文件安全防御模块：处理防路径穿越与 PDF 文件安全生命周期
"""
import os
import re
import uuid
import logging
from typing import Optional, Tuple

from .student_id import clean_student_id
from .database import DATABASE_DIR

_logger = logging.getLogger(__name__)

# PDF 存储根目录（项目根/static/pdfs）
_PDF_DIR = os.path.join(DATABASE_DIR, "static", "pdfs")

# PDF 文件头魔数（所有合法 PDF 以 %PDF 开头）
_PDF_MAGIC = b"%PDF"
# 上传最大字节数（10MB）
PDF_UPLOAD_MAX_BYTES = 10 * 1024 * 1024


def validate_pdf_upload(file_data: bytes) -> Tuple[bool, str]:
    """
    校验上传的 PDF 文件：
    1. 文件大小 ≤ 10MB
    2. 文件头为 %PDF（防止伪装文件）
    返回 (是否合法, 错误信息)。
    """
    if len(file_data) > PDF_UPLOAD_MAX_BYTES:
        return False, f"文件大小超过限制（最大 {PDF_UPLOAD_MAX_BYTES // 1024 // 1024}MB）"
    if not file_data[:4].startswith(_PDF_MAGIC):
        return False, "文件内容不是有效的 PDF 格式，请上传真实的 PDF 文件"
    return True, ""


def generate_pdf_filename() -> str:
    """生成不可猜测的 UUID 文件名，防止文件名枚举攻击。"""
    return f"{uuid.uuid4().hex}.pdf"


def save_pdf_file(file_data: bytes) -> Tuple[str, str]:
    """
    将 PDF 数据保存到分桶子目录中。
    按 UUID 文件名前 2 位分桶：static/pdfs/a3/a3f7e2b1xxx.pdf
    万级文件分散到 256 个子目录，每目录平均几十个文件。

    返回 (本地文件绝对路径, 数据库存储路径)。
    数据库路径格式：/app/static/pdfs/a3/a3f7e2b1xxx.pdf
    """
    filename = generate_pdf_filename()
    bucket = filename[:2]
    subdir = os.path.join(_PDF_DIR, bucket)
    os.makedirs(subdir, exist_ok=True)
    file_path = os.path.join(subdir, filename)
    with open(file_path, "wb") as f:
        f.write(file_data)
    db_path = f"/app/static/pdfs/{bucket}/{filename}"
    return file_path, db_path


def safe_filename(raw: str) -> str:
    """
    将原始字符串净化为安全的文件名片段：仅保留字母、数字、下划线。
    防止路径遍历攻击（如 ../../etc/passwd）。
    """
    cleaned = clean_student_id(raw)
    return re.sub(r'[^a-zA-Z0-9_]', '', cleaned) or "unknown"


def _resolve_pdf_local_path(reason_field: str) -> Optional[str]:
    """
    从数据库 reason 字段解析出本地文件路径。
    兼容两种格式：
      旧格式：/app/static/pdfs/xxx.pdf（平级目录）
      新格式：/app/static/pdfs/a3/a3xxx.pdf（分桶子目录）
    返回 None 表示非法或文件不存在。
    """
    if not reason_field or not reason_field.strip():
        return None

    # 提取 /app/static/pdfs/ 之后的相对路径
    marker = "/app/static/pdfs/"
    idx = reason_field.find(marker)
    if idx >= 0:
        rel_path = reason_field[idx + len(marker):]
    else:
        rel_path = reason_field.rsplit("/", 1)[-1] if "/" in reason_field else reason_field

    if not rel_path or ".." in rel_path:
        return None

    filepath = os.path.join(_PDF_DIR, rel_path.replace("/", os.sep))
    abs_path = os.path.abspath(filepath)
    abs_pdf_dir = os.path.abspath(_PDF_DIR)

    if not abs_path.startswith(abs_pdf_dir + os.sep):
        _logger.warning("PDF 路径安全校验失败: %s", abs_path)
        return None

    if os.path.isfile(abs_path):
        return abs_path

    # 兼容：旧文件可能在平级目录
    filename = os.path.basename(rel_path)
    flat_path = os.path.join(_PDF_DIR, filename)
    if os.path.isfile(flat_path):
        abs_flat = os.path.abspath(flat_path)
        if abs_flat.startswith(abs_pdf_dir + os.sep):
            return abs_flat

    return None


def remove_old_pdf(reason_field: Optional[str]) -> None:
    """
    安全删除旧的 PDF 文件（兼容平级和分桶两种目录结构）。
    文件不存在或删除失败时静默处理，不影响主流程。
    """
    abs_path = _resolve_pdf_local_path(reason_field or "")
    if abs_path is None:
        return
    try:
        os.remove(abs_path)
        _logger.info("已清理旧 PDF: %s", os.path.basename(abs_path))
    except OSError as e:
        _logger.warning("清理旧 PDF 失败: %s", e)
