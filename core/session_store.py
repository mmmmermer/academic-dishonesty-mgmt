"""
服务端 Session 存储：登录后写入 token，刷新页面时根据 URL 中的 sid 恢复登录态。
使用 JSON 文件存储 token -> 用户信息，避免刷新导致 st.session_state 清空而掉线。

多实例部署：多进程共享同一 sessions.json 时，使用文件锁（fcntl，仅非 Windows）保证并发读写安全。
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time

# 非 Windows 平台使用 fcntl 做跨进程文件锁；Windows 下多实例少见，暂不加锁
if sys.platform != "win32":
    try:
        import fcntl
    except ImportError:
        fcntl = None
else:
    fcntl = None

# 与 database 同目录，便于备份时一起处理
# 向上跳一级：从 core/ 目录到项目根目录
DATABASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS_FILE = os.path.join(DATABASE_DIR, "sessions.json")

# 与 config 一致：token 有效时长（秒）
try:
    from .config import SESSION_TIMEOUT_MINUTES
except ImportError:
    SESSION_TIMEOUT_MINUTES = 30

TOKEN_EXPIRY_SECONDS = SESSION_TIMEOUT_MINUTES * 60


def _lock_shared(f):
    """对已打开的文件加共享锁（读锁）；仅非 Windows 且 fcntl 可用时执行。"""
    if fcntl and f is not None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        except (OSError, AttributeError):
            pass


def _lock_exclusive(f):
    """对已打开的文件加排他锁（写锁）；仅非 Windows 且 fcntl 可用时执行。"""
    if fcntl and f is not None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except (OSError, AttributeError):
            pass


def _unlock(f):
    """释放文件锁。"""
    if fcntl and f is not None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (OSError, AttributeError):
            pass


def _load_sessions():
    """从文件读取全部 session，并删除已过期的。多进程下读时使用共享锁；过期清理为 best-effort（多进程同时清理时 last-write-wins）。"""
    if not os.path.isfile(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            _lock_shared(f)
            try:
                data = json.load(f)
            finally:
                _unlock(f)
    except (json.JSONDecodeError, OSError):
        return {}
    now = time.time()
    valid = {k: v for k, v in data.items() if (v.get("expiry") or 0) > now}
    if len(valid) != len(data):
        _save_sessions(valid)
    return valid


def _save_sessions(sessions: dict):
    """将 session 字典写回文件。多进程下写时使用排他锁，避免并发写覆盖。OSError 时静默忽略以免影响调用方。"""
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            _lock_exclusive(f)
            try:
                json.dump(sessions, f, ensure_ascii=False, indent=0)
            finally:
                _unlock(f)
    except OSError:
        pass  # 磁盘满、权限等时不打断登录/登出流程


def create_session(user_id: int, username: str, role: str, full_name: str) -> str:
    """
    创建一条 session，返回 token。
    调用方需将 token 写入 URL（如 st.query_params["sid"] = token）。
    """
    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[token] = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "full_name": full_name,
        "expiry": time.time() + TOKEN_EXPIRY_SECONDS,
    }
    _save_sessions(sessions)
    return token


def get_session(token: str) -> dict | None:
    """
    根据 token 获取 session 数据；无效或过期返回 None。
    返回包含 user_id, username, role, full_name 的字典。
    """
    if not token:
        return None
    sessions = _load_sessions()
    return sessions.get(token)


def delete_session(token: str):
    """登出时删除该 token。"""
    if not token:
        return
    sessions = _load_sessions()
    if token in sessions:
        del sessions[token]
        _save_sessions(sessions)
