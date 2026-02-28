"""
管理员页面代理模块：保持 app.py 导入路径不变，实际逻辑已拆分至 views/admin/ 子模块。
"""
from views.admin import render_admin_page, render_admin_sidebar_nav

__all__ = ["render_admin_page", "render_admin_sidebar_nav"]
