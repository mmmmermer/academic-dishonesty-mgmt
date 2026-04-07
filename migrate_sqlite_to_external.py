"""
从现有 SQLite (database.db) 迁移数据到当前 DATABASE_URL 指定的数据库（通常为 MySQL/PostgreSQL）。
使用前请先设置环境变量 DATABASE_URL，且目标应为非 SQLite。
示例：
  set DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4
  python migrate_sqlite_to_external.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import DATABASE_DIR, DATABASE_URL, IS_SQLITE
from core.models import Base, User, Blacklist, AuditLog

SQLITE_PATH = os.path.join(DATABASE_DIR, "database.db")
BATCH_SIZE = 500


def main():
    if not os.environ.get("DATABASE_URL", "").strip():
        print("请先设置环境变量 DATABASE_URL（例如 MySQL 或 PostgreSQL 连接串），再运行本脚本。")
        sys.exit(1)
    if IS_SQLITE:
        print("当前 DATABASE_URL 指向 SQLite，本脚本用于迁移到 MySQL/PostgreSQL。请设置外部数据库的 DATABASE_URL。")
        sys.exit(1)
    if not os.path.isfile(SQLITE_PATH):
        print(f"未找到 SQLite 文件：{SQLITE_PATH}，无需迁移。可直接在目标库执行 init_db.py 建表并创建默认管理员。")
        sys.exit(0)

    src_engine = create_engine(f"sqlite:///{SQLITE_PATH}", connect_args={"check_same_thread": False})
    src_session = sessionmaker(bind=src_engine, autocommit=False, autoflush=False)()

    dst_engine = create_engine(DATABASE_URL, pool_size=5, pool_pre_ping=True)
    Base.metadata.create_all(bind=dst_engine)
    dst_session = sessionmaker(bind=dst_engine, autocommit=False, autoflush=False)()

    try:
        # 防止重复迁移：目标库已有用户数据时视为已迁移，避免重复插入与唯一约束冲突
        if dst_session.query(User).count() > 0:
            print("目标库已有用户数据，视为已迁移。若需重新迁移请使用空库，或手动清空目标表后再运行。")
            return

        # 1. User（不复制 id，由目标库自增）
        users = src_session.query(User).all()
        for i in range(0, len(users), BATCH_SIZE):
            for u in users[i : i + BATCH_SIZE]:
                dst_session.add(
                    User(
                        username=u.username,
                        password_hash=u.password_hash,
                        full_name=u.full_name,
                        role=u.role,
                        is_active=u.is_active,
                    )
                )
            dst_session.commit()
        print(f"  User: 迁移 {len(users)} 条")

        # 2. Blacklist
        bl = src_session.query(Blacklist).order_by(Blacklist.id).all()
        for i in range(0, len(bl), BATCH_SIZE):
            for b in bl[i : i + BATCH_SIZE]:
                dst_session.add(
                    Blacklist(
                        name=b.name,
                        student_id=b.student_id,
                        major=b.major,
                        reason=b.reason,
                        reason_text=b.reason_text,
                        punishment_date=b.punishment_date,
                        impact_start_date=b.impact_start_date,
                        impact_end_date=b.impact_end_date,
                        status=b.status,
                        created_at=b.created_at,
                    )
                )
            dst_session.commit()
        print(f"  Blacklist: 迁移 {len(bl)} 条")

        # 3. AuditLog
        logs = src_session.query(AuditLog).order_by(AuditLog.id).all()
        for i in range(0, len(logs), BATCH_SIZE):
            for a in logs[i : i + BATCH_SIZE]:
                dst_session.add(
                    AuditLog(
                        operator_name=a.operator_name,
                        operator_username=a.operator_username,
                        action_type=a.action_type,
                        target=a.target,
                        details=a.details,
                        timestamp=a.timestamp,
                    )
                )
            dst_session.commit()
        print(f"  AuditLog: 迁移 {len(logs)} 条")

        print("迁移完成。请使用新 DATABASE_URL 启动应用并验证数据。")
    except Exception as e:
        dst_session.rollback()
        print(f"迁移失败：{e}")
        sys.exit(1)
    finally:
        src_session.close()
        dst_session.close()
        src_engine.dispose()
        dst_engine.dispose()


if __name__ == "__main__":
    main()
