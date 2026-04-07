"""
主入口与路由整合模块，将各个模块暴露供 app.py 调用。
"""
from .single_search import render_single_search
from .batch_check import render_batch_check
from .my_logs import render_my_logs
from .list_query import render_teacher_list_query

__all__ = [
    "render_single_search",
    "render_batch_check",
    "render_my_logs",
    "render_teacher_list_query",
]
