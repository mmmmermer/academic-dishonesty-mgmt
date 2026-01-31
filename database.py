"""
数据库连接与会话配置
使用 SQLAlchemy 配置 SQLite 引擎和会话。
"""
import os

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


def get_db():
    """获取数据库会话，用于依赖注入。使用完毕后需 close。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
