import os
import sys
import threading
import time

os.environ["ALLOW_SQLITE_FALLBACK"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///test_database.db"

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from core.database import db_session, engine
from core.models import Blacklist

def upload_massive_excel_and_crash():
    print("\n" + "=" * 60)
    print("[阶段 2.1] 灾难模拟：大批量导入时中途断电/崩溃拦截")
    print("=" * 60)
    
    # 我们故意不用 ctx manager 或者尝试在中间用异常主动打断
    try:
        with db_session() as db:
            print("   >>> 教师发起了 5,000 人的 Excel 查重导入任务...")
            records = []
            for i in range(1, 5001):
                bl = Blacklist(
                    name=f"死产者{i}",
                    student_id=f"D2024{i:05d}",
                    major="计算机学院",
                    status=1
                )
                db.add(bl)
                if i == 2500:
                    print("   💥 [致命故障] 导入进行到 2500 条时，服务器突然断网宕机/电源被拔下！(RuntimeError引发)")
                    raise RuntimeError("服务器机房起火，进程非正常死亡！")
            db.commit() # 这句话永远执行不到
    except RuntimeError as e:
        print(f"   捕获到硬件级异常宕机: {e}")
        
    print("   🔌 服务器重新通电复现拉起，进入检验库容状态...")
    with db_session() as db:
        # 查询是否有半吊子数据
        dirty_records = db.query(Blacklist).filter(Blacklist.name.like("死产者%")).count()
        print(f"   [检验报告] 查获断电脏数据残留量: {dirty_records} 条 (如果不为 0 则说明架构穿透，存在逻辑损坏！)")
        
        if dirty_records == 0:
            print("   ✅ 原子性防御成功！数据库无一例外彻底回滚了那不完整的 2500 人，保持了数据高度纯净。")

if __name__ == "__main__":
    upload_massive_excel_and_crash()
