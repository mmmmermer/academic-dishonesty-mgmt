"""
认证工具：BCrypt 密码校验
与 Phase 1 中 init_db 使用的 bcrypt.hashpw 存储方式一致。
"""
from typing import Union

import bcrypt

try:
    from .config import PASSWORD_MAX_BYTES
except ImportError:
    PASSWORD_MAX_BYTES = 72


def verify_password(plain_password: str, hashed_password: Union[str, bytes]) -> bool:
    """
    校验明文密码与哈希是否匹配。
    与 init_db 中 bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8") 的存储方式一致。

    :param plain_password: 用户输入的明文密码
    :param hashed_password: 数据库中存储的哈希（str 或 bytes）
    :return: 匹配返回 True，否则 False
    """
    if not plain_password:
        return False
    try:
        plain_bytes = plain_password.encode("utf-8")
        # BCrypt 有效输入有上限，超长直接拒绝，避免无谓计算与异常
        if len(plain_bytes) > PASSWORD_MAX_BYTES:
            return False
        if isinstance(hashed_password, str):
            hashed_bytes = hashed_password.encode("utf-8")
        else:
            hashed_bytes = hashed_password
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except (ValueError, TypeError, AttributeError):
        return False
