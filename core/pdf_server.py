"""
安全 PDF 服务模块：通过 UUID 文件名 + 直链 iframe 提供快速 PDF 预览。

安全特性：
- 文件名为 UUID（128 位随机，不可枚举）
- robots.txt 禁止爬虫索引
- PDF 链接仅在登录后的详情卡片中显示
- 校验文件路径在 static/pdfs/ 内（防路径穿越）
- 超大文件（>10MB）提供下载按钮代替内嵌
"""
import os
import logging
from typing import Optional

import streamlit as st

from .file_safe_guard import _PDF_DIR

_logger = logging.getLogger(__name__)

# 超过此大小的 PDF 提供下载按钮
PDF_MAX_PREVIEW_BYTES = 10 * 1024 * 1024  # 10MB


def _resolve_pdf_path(reason_field: str) -> Optional[str]:
    """
    从数据库 reason 字段解析出安全的本地文件路径。
    返回 None 表示非法或不存在。
    """
    if not reason_field or not reason_field.strip().lower().endswith(".pdf"):
        return None

    filename = reason_field.rsplit("/", 1)[-1] if "/" in reason_field else reason_field
    if not filename or ".." in filename:
        return None

    filepath = os.path.join(_PDF_DIR, filename)
    abs_path = os.path.abspath(filepath)
    abs_pdf_dir = os.path.abspath(_PDF_DIR)

    if not abs_path.startswith(abs_pdf_dir + os.sep):
        _logger.warning("PDF 路径安全校验失败: %s", abs_path)
        return None

    if not os.path.isfile(abs_path):
        return None

    return abs_path


def render_pdf_preview(reason_field: str, key_suffix: str = "") -> None:
    """
    根据数据库 reason 字段值，安全渲染 PDF 预览。
    使用 Streamlit 静态文件直链 + iframe，浏览器原生 PDF 渲染，毫秒级加载。
    """
    abs_path = _resolve_pdf_path(reason_field)
    if abs_path is None:
        if reason_field and reason_field.strip().lower().endswith(".pdf"):
            st.caption("📄 PDF 文件不存在或已被清理")
        return

    file_size = os.path.getsize(abs_path)

    if file_size > PDF_MAX_PREVIEW_BYTES:
        with open(abs_path, "rb") as f:
            pdf_bytes = f.read()
        st.download_button(
            "📥 下载 PDF 公示文件（文件较大）",
            data=pdf_bytes,
            file_name=os.path.basename(abs_path),
            mime="application/pdf",
            key=f"pdf_dl_{key_suffix}",
        )
        return

    # 直链 iframe：浏览器原生渲染，速度快
    # reason_field 格式为 /app/static/pdfs/UUID.pdf，Streamlit 直接服务
    url = reason_field.strip()
    st.markdown("**认定结论（PDF 公示文件）**：")
    st.markdown(
        f'<iframe src="{url}" '
        f'width="100%" height="500px" '
        f'style="border:1px solid #ddd;border-radius:4px">'
        f'</iframe>',
        unsafe_allow_html=True,
    )
