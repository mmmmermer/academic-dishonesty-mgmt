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


def safe_filename(raw: str) -> str:
    """
    将原始字符串净化为安全的文件名片段：仅保留字母、数字、下划线。
    防止路径遍历攻击（如 ../../etc/passwd）。
    """
    cleaned = clean_student_id(raw)
    return re.sub(r'[^a-zA-Z0-9_]', '', cleaned) or "unknown"


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
