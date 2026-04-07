from typing import Any, Tuple
import pandas as pd

try:
    from .config import STUDENT_ID_MAX_LEN, STUDENT_ID_MIN_LEN
except ImportError:
    STUDENT_ID_MIN_LEN = 1
    STUDENT_ID_MAX_LEN = 32

# 全角数字到半角映射（学号中可能出现的）
FULL_TO_HALF_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

def clean_student_id(text: Any) -> str:
    """
    清洗学号：去除所有空白、全角数字转半角，并转为大写（忽略大小写差异）。
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    if isinstance(text, str) and text.strip().lower() in ("nan", ""):
        return ""
    s = str(text).strip()
    s = "".join(s.split())
    s = s.translate(FULL_TO_HALF_DIGITS)
    return s.upper()

def validate_student_id(raw: Any) -> Tuple[bool, str]:
    """
    校验学号：清洗后检查长度在允许范围内。
    """
    s = clean_student_id(raw)
    if len(s) < STUDENT_ID_MIN_LEN:
        return False, "学号不能为空。"
    if len(s) > STUDENT_ID_MAX_LEN:
        return False, f"学号长度不能超过 {STUDENT_ID_MAX_LEN} 位。"
    return True, ""
