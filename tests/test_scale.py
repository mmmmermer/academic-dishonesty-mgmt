import os
import sys
import time
import random
from datetime import datetime

os.environ["ALLOW_SQLITE_FALLBACK"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///test_database.db"

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from core.database import db_session, engine
from core.models import Base, Blacklist
from core.search import sync_blacklist_record_search_helper_fields
from views.components import build_blacklist_query
from core.config import ALL_UNIT_LIST

def generate_massive_data(num_records=200000):
    print("=" * 60)
    print(f"[阶段 1.1] 构建大型深水区实验库: 注入 {num_records} 条数据")
    print("=" * 60)
    
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    start_t = time.time()
    batch_size = 10000
    
    surnames = ["张", "李", "王", "刘", "赵", "陈", "杨", "吴", "黄", "周", "徐", "孙", "马", "朱", "胡", "林", "郭", "何", "高", "罗"]
    firstnames = ["伟", "芳", "娜", "敏", "静", "秀英", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "秀兰", "霞"]
    
    with db_session() as db:
        records = []
        for i in range(1, num_records + 1):
            name = random.choice(surnames) + random.choice(firstnames) + str(i % 100)
            sid = f"U202{i:06d}"
            
            bl = Blacklist(
                name=name,
                student_id=sid,
                major=random.choice(ALL_UNIT_LIST),
                reason_text="百万级压测填充物" if i % 2 == 0 else None,
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
                print(f"   >>> 进度冲刺: 已灌入 {i} 条...")
                records = []
                
        if records:
            db.bulk_save_objects(records)
            db.commit()
            
    print(f"✅ 生成完毕！总耗时: {time.time() - start_t:.2f} 秒")

def test_index_degradation_and_pagination():
    print("\n" + "=" * 60)
    print("[阶段 1.2] B树索引衰退与深层翻页 (Pagination Penalty) 测探")
    print("=" * 60)
    
    with db_session() as db:
        # 测试在浅水区查询
        t1 = time.time()
        record_shallow = db.query(Blacklist).filter(Blacklist.student_id == "U202000050").first()
        ms_shallow = (time.time() - t1) * 1000
        print(f"[精准命中] 浅水区索引探测 (U202000050) -> 耗时: {ms_shallow:.2f} ms")
        
        # 测试在深水区查询
        t1 = time.time()
        record_deep = db.query(Blacklist).filter(Blacklist.student_id == "U202199950").first()
        ms_deep = (time.time() - t1) * 1000
        print(f"[精准命中] 深水池区底层探测 (U202199950) -> 耗时: {ms_deep:.2f} ms")
        
        # 测试深水区分页（Offset）的极其昂贵的操作
        t1 = time.time()
        # 跳过 150,000 条数据去取第 10 条，这通常是一切数据库的痛点
        deep_page_records = db.query(Blacklist).filter(Blacklist.status == 1).order_by(Blacklist.id).offset(150000).limit(10).all()
        ms_offset = (time.time() - t1) * 1000
        print(f"[全表碾压] 强制翻页至 Offset 150000 探底 -> 耗时: {ms_offset:.2f} ms")

if __name__ == "__main__":
    # 生成 20w 并推演 100w，防止让您等待太久
    generate_massive_data(200000)
    test_index_degradation_and_pagination()
