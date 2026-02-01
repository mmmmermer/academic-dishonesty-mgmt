"""
数据库连接与会话配置。使用 SQLAlchemy 配置 SQLite 引擎和会话。
db_session() 为上下文管理器，推荐在 with 块内使用；SessionLocal() 需调用方自行 close。
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 数据库文件路径（项目根目录下）
DATABASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = f"sqlite:///{os.path.join(DATABASE_DIR, 'database.db')}"

# 创建引擎：SQLite 启用外键、echo 可设为 True 便于调试
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite 多线程需要
    echo=False,
)

# 会话工厂
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


def get_db():
    """获取数据库会话（生成器），用于依赖注入。使用完毕后需 close。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
