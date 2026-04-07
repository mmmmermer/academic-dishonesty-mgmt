import os
import sys

# 必须首先设置环境变量，以绕过数据库保护
os.environ["ALLOW_SQLITE_FALLBACK"] = "1"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.student_id import clean_student_id, validate_student_id
from core.file_safe_guard import safe_filename, remove_old_pdf, _PDF_DIR

def run_boundary_tests():
    print("=" * 50)
    print("1. 核心边界测试: core.student_id (过滤掉异常输入)")
    print("-" * 50)
    
    extreme_ids = [
        "   123456   ",                 # 尾部空格
        "１２３４５６７８",           # 全角数字
        "    \t\n123456\r  ",          # 隐形特殊符号
        "U2025" * 10,                  # 超长 50 位字符超越上限
        "123",                          # 极短 (违规)
        "' OR '1'='1",                  # 显式 SQL 注入标签
    ]
    
    for case in extreme_ids:
        cleaned = clean_student_id(case)
        ok, err = validate_student_id(case)
        print(f"| 原文长度: {len(case):<4} | 清洗后: {cleaned[:30]:<30} | 校验: {'✅ 正常通过' if ok else '❌ 成功拦截 (' + err + ')'}")

    print("\n" + "=" * 50)
    print("2. 核心边界测试: core.file_safe_guard (文件系统穿越阻挡)")
    print("-" * 50)

    evil_paths = [
        "12345.pdf",
        "../../etc/passwd",
        "/root/.ssh/id_rsa",
        "../../../app/database/database.db",
        "http://evil.com/shell.pdf"
    ]
    
    for path in evil_paths:
        cleaned_path = safe_filename(path)
        print(f"| 恶意输入路径: {path:<35} | 安全转义降维: {cleaned_path}")
        
    print("\n[✓] 边界防御直推测试执行完毕！")

if __name__ == "__main__":
    run_boundary_tests()
