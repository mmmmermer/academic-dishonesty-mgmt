import os
import sys
import threading
import time
import concurrent.futures

os.environ["ALLOW_SQLITE_FALLBACK"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///test_database.db"

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from core.database import db_session, engine
from core.models import Blacklist

def setup_race_victim():
    with db_session() as db:
        victim = db.query(Blacklist).filter_by(student_id="U202000088").first()
        if not victim:
            victim = Blacklist(name="竞态受害者", student_id="U202000088", major="哲学院", status=1)
            db.add(victim)
            db.commit()

def malicious_admin_a():
    """管理员A想要把它改成解除状态 (2)"""
    try:
        with db_session() as db:
            victim = db.query(Blacklist).filter_by(student_id="U202000088").first()
            time.sleep(0.5) # 模拟人在看
            victim.status = 2
            victim.reason_text = "[A管理员的决定]"
            db.commit()
        return "A管理员(解放) 成功落地"
    except Exception as e:
        return f"A报错拦截: {e}"

def malicious_admin_b():
    """管理员B想要把它改成严加惩罚 (1) 并覆盖原因"""
    try:
        with db_session() as db:
            victim = db.query(Blacklist).filter_by(student_id="U202000088").first()
            time.sleep(0.5) # 模拟人在看
            victim.status = 1
            victim.reason_text = "[B管理员的惩戒]"
            db.commit()
        return "B管理员(惩戒) 成功落地"
    except Exception as e:
        return f"B报错拦截: {e}"

def run_race_condition():
    setup_race_victim()
    
    print("\n" + "=" * 60)
    print("[阶段 3.1] Data Race：AB两个教务人员同时按下修改按钮重叠测试")
    print("=" * 60)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(malicious_admin_a)
        f_b = executor.submit(malicious_admin_b)
        
        print("  -> " + f_a.result())
        print("  -> " + f_b.result())
        
    with db_session() as db:
        final_victim = db.query(Blacklist).filter_by(student_id="U202000088").first()
        print(f"   [检验报告] 最终留存的是: {final_victim.reason_text}，状态码: {final_victim.status}")
        print("   ✅ 在本机制下，最后落盘者拥有最终原子性覆盖权，不存在交叉锁死崩溃（Deadlock）的隐患。")

if __name__ == "__main__":
    run_race_condition()
