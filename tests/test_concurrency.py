import os
import sys
import time
import concurrent.futures

os.environ["ALLOW_SQLITE_FALLBACK"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///test_database.db"

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from core.database import db_session, engine
from core.models import Blacklist

def worker_query(worker_id):
    """模拟一个老师的动作：开机，点击系统，进行查阅"""
    try:
        t1 = time.time()
        with db_session() as db:
            # 执行一个稍微复杂的查表并分页提取
            results = db.query(Blacklist).filter(Blacklist.status == 1).order_by(Blacklist.id).limit(10).all()
            if results:
                pass
        return time.time() - t1, None
    except Exception as e:
        return 0, str(e)

def simulate_morning_rush(total_teachers=100):
    print("=" * 60)
    print(f"[阶段 1.3] 模拟早高峰：{total_teachers} 名教师在 1 秒内同时抢占数据库连接")
    print("=" * 60)
    
    start_t = time.time()
    latencies = []
    errors = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=total_teachers) as executor:
        # 同时下达发射任务
        futures = {executor.submit(worker_query, i): i for i in range(total_teachers)}
        
        for future in concurrent.futures.as_completed(futures):
            cost, err = future.result()
            if err:
                errors.append(err)
            else:
                latencies.append(cost)
                
    total_time = time.time() - start_t
    print(f"-> 突变测试全量发送完毕。系统总抗压承载时间: {total_time:.2f} 秒")
    print(f"-> 成功处理的并发请求: {len(latencies)} 个。")
    if latencies:
        print(f"   [指标] 极速响应(最快): {min(latencies)*1000:.2f} ms")
        print(f"   [指标] 垫底响应(最慢): {max(latencies)*1000:.2f} ms")
        print(f"   [指标] 平均响应耗时:   {sum(latencies)/len(latencies)*1000:.2f} ms")
    print(f"-> 崩溃/抛弃的连接异常数: {len(errors)} 个 (数据库连接池是否耗尽？)")
    if errors:
        print(f"   [致命] 引爆的首个错误: {errors[0]}")

if __name__ == "__main__":
    simulate_morning_rush(100)
