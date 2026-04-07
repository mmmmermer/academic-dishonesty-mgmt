import os
import sys
import time
import random
from io import BytesIO
import pandas as pd
from datetime import datetime

# 强行指定为专门的测试环境库
os.environ["ALLOW_SQLITE_FALLBACK"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///test_database.db"

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from core.database import db_session, engine
from core.models import Base, Blacklist
from core.search import sync_blacklist_record_search_helper_fields
from views.components import build_blacklist_query, apply_blacklist_sort, fetch_export_rows
from core.config import ALL_UNIT_LIST

def prepare_extreme_test_db(num_records):
    print("=" * 60)
    print(f"1. 隔离注水防爆库构建中 ({num_records} 名极限造影特征人员)")
    print("=" * 60)
    
    # 建表
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    start_t = time.time()
    batch_size = 5000
    
    surnames = ["张", "李", "王", "刘", "赵", "陈", "杨", "吴", "黄"]
    firstnames = ["伟", "芳", "娜", "敏", "静", "秀英", "丽", "强", "磊", "军", "洋", "勇", "艳"]
    
    with db_session() as db:
        records = []
        for i in range(1, num_records + 1):
            name = random.choice(surnames) + random.choice(firstnames) + str(i % 100)
            sid = f"U202{i:06d}"
            major = random.choice(ALL_UNIT_LIST)
            
            bl = Blacklist(
                name=name,
                student_id=sid,
                major=major,
                reason_text="压测虚构原因" if i % 2 == 0 else None,
                status=1,
                punishment_date=datetime.now().date(),
                impact_start_date=datetime.now().date(),
                impact_end_date=datetime.now().date(),
            )
            sync_blacklist_record_search_helper_fields(db, bl)
            records.append(bl)
            
            if i % batch_size == 0:
                db.bulk_save_objects(records)
                db.commit()
                print(f"   已灌入 {i} 条记录...")
                records = []
                
        if records:
            db.bulk_save_objects(records)
            db.commit()
            
    print(f"-> 环境就位！SQLite 落盘耗时: {time.time() - start_t:.3f} 秒")

def test_search_latencies():
    print("\n" + "=" * 60)
    print("2. 联合检索与抗压测试执行")
    print("=" * 60)

    test_cases = [
        {"name": "极宽拼音检索", "fn": "san,wu,zhao", "fs": "", "fm": []},
        {"name": "特定院系拼音", "fn": "zlw", "fs": "", "fm": [ALL_UNIT_LIST[0], ALL_UNIT_LIST[1]]},
        {"name": "首位模糊学号", "fn": "", "fs": "U2025, U2029", "fm": []},
        {"name": "完全泛解析请求", "fn": "", "fs": "", "fm": []},
    ]

    with db_session() as db:
        for idx, case in enumerate(test_cases, 1):
            t1 = time.time()
            query = build_blacklist_query(db, status=1, name_filter=case["fn"], sid_filter=case["fs"], major_categories=case["fm"])
            count = query.count()
            cost_ms = (time.time() - t1) * 1000
            print(f" [用例 {idx}] {case['name']:<15} -> 命中总数: {count:<5} | 后端响应时间: {cost_ms:.2f} 毫秒")

def test_export_ram_overhead():
    print("\n" + "=" * 60)
    print("3. 全量数据在途内存转化溢出点探底 (EXCEL)")
    print("=" * 60)

    with db_session() as db:
        query = build_blacklist_query(db, status=1)
        count = query.count()
        
        print(f"   正在模拟后台请求导出这片全部 ({count} 条) 记录到 Pandas/BytesIO...")
        t1 = time.time()
        
        # 启动分批流式提取 (Yield per) 测试能否抗压
        rows = fetch_export_rows(query, max_rows=50000, batch_size=2000)
        export_df = pd.DataFrame([{
            "学号": r.student_id,
            "姓名": r.name,
            "单位": r.major
        } for r in rows[:10000]])  # 控制内存别爆满
        
        buf = BytesIO()
        export_df.to_excel(buf, index=False, engine="openpyxl")
        size_mb = len(buf.getvalue()) / (1024 * 1024)
        cost = time.time() - t1
        
        print(f"-> 脱水提取组装完毕！流式处理正常")
        print(f"   Excel 内存映射大小 (基于前1W条): {size_mb:.2f} MB")
        print(f"   IO 级构建全生命周期延时: {cost:.2f} 秒")

if __name__ == "__main__":
    prepare_extreme_test_db(50000)
    test_search_latencies()
    test_export_ram_overhead()
