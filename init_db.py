"""
初始化数据库脚本
创建所有表，并添加默认管理员账号 (admin / 123456)。
密码使用 bcrypt 加密存储。
"""
import bcrypt

from core.database import engine, SessionLocal
from core.models import Base, User


def create_tables():
    """创建所有表"""
    Base.metadata.create_all(bind=engine)


def add_default_admin():
    """添加默认管理员：用户名 admin，密码 123456"""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").first()
        if existing:
            print("默认管理员已存在，跳过创建。")
            return
        password_hash = bcrypt.hashpw("123456".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        admin = User(
            username="admin",
            password_hash=password_hash,
            full_name="系统管理员",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        print("默认管理员已创建：用户名 admin，密码 123456")
    finally:
        db.close()


def main():
    create_tables()
    add_default_admin()
    print("数据库初始化完成。")


if __name__ == "__main__":
    main()
