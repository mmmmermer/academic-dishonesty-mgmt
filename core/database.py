"""
数据库连接与会话配置。
支持通过环境变量 DATABASE_URL 切换 SQLite / MySQL / PostgreSQL；
未设置时使用项目根目录下的 SQLite 文件。
db_session() 为上下文管理器，推荐在 with 块内使用；SessionLocal() 需调用方自行 close。

安全约定：DATABASE_URL 可能包含密码，禁止写入日志或向用户展示完整 URL。
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# 项目根目录（core 的上一级）
DATABASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 优先使用环境变量；未设置时使用默认 SQLite 路径（便于本地开发零配置）
_allow_sqlite_fallback = os.environ.get("ALLOW_SQLITE_FALLBACK", "").strip().lower() in {"1", "true", "yes"}
_env_url = os.environ.get("DATABASE_URL", "").strip()
if not _env_url:
    DATABASE_URL = f"sqlite:///{os.path.join(DATABASE_DIR, 'database.db')}"
else:
    DATABASE_URL = _env_url
    # 若显式配置为 SQLite，限制路径必须在项目目录内，防止路径穿越写入任意位置
    if "sqlite" in DATABASE_URL:
        _prefix = "sqlite:///"
        if DATABASE_URL.startswith(_prefix):
            _path = DATABASE_URL[len(_prefix) :].lstrip("/")
            if _path and _path != ":memory:":
                # 相对路径按项目根解析，确保仅允许项目目录内
                _abs = os.path.normpath(os.path.join(DATABASE_DIR, _path))
                _root = os.path.abspath(DATABASE_DIR)
                _abs = os.path.abspath(_abs)
                if not (_abs == _root or _abs.startswith(_root + os.sep)):
                    raise ValueError(
                        "当 DATABASE_URL 指向 SQLite 时，路径必须在项目目录内，禁止使用绝对路径或 .. 指向项目外。"
                    )

# 是否为 SQLite（供备份/下载等逻辑判断是否可操作本地 .db 文件）
if not _env_url and not _allow_sqlite_fallback:
    raise RuntimeError(
        "DATABASE_URL is required in PostgreSQL-only mode. "
        "Start app via start_system.bat or scripts/run_app_postgres.ps1. "
        "If you need temporary SQLite fallback, set ALLOW_SQLITE_FALLBACK=1 explicitly."
    )
if "sqlite" in DATABASE_URL and not _allow_sqlite_fallback:
    raise RuntimeError(
        "SQLite is disabled in PostgreSQL-only mode. "
        "Use a PostgreSQL DATABASE_URL, or set ALLOW_SQLITE_FALLBACK=1 for temporary maintenance."
    )

IS_SQLITE = "sqlite" in DATABASE_URL

# 创建引擎：按数据库类型选择参数
if IS_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        """每次获取新连接时开启 WAL 模式，大幅提升读写并发能力。"""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()
else:
    # MySQL/PostgreSQL：连接池 + 健康检查 + 连接回收，适配多请求并发与长时间运行
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def db_session():
    """
    数据库会话上下文管理器，确保使用完毕后自动 close。
    异常时未提交的变更会在 close 时由 Session 自动回滚，调用方可在 except 中显式 rollback 以保持可读性。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

