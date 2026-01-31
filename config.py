"""
应用常量：审计操作类型、业务文案等，便于维护与统一引用。
"""

# 审计日志操作类型（与 models.AuditLog.action_type 一致）
AUDIT_LOGIN = "LOGIN"
AUDIT_QUERY_SINGLE = "QUERY_SINGLE"
AUDIT_QUERY_BATCH = "QUERY_BATCH"
AUDIT_IMPORT = "IMPORT"
AUDIT_ADD = "ADD"
AUDIT_DELETE = "DELETE"
AUDIT_RESTORE = "RESTORE"
AUDIT_BACKUP = "BACKUP"

# 审计类型列表（用于筛选等）
AUDIT_ACTION_TYPES = [
    AUDIT_LOGIN,
    AUDIT_QUERY_SINGLE,
    AUDIT_QUERY_BATCH,
    AUDIT_IMPORT,
    AUDIT_ADD,
    AUDIT_DELETE,
    AUDIT_RESTORE,
    AUDIT_BACKUP,
]

# 名单状态
BLACKLIST_STATUS_EFFECTIVE = 1
BLACKLIST_STATUS_REVOKED = 0

# 会话超时（分钟）：无操作超过此时长将自动退出登录
SESSION_TIMEOUT_MINUTES = 30

# 登录失败限制：同一账号连续失败次数超过此值将进入冷却
LOGIN_FAIL_MAX = 5
# 登录冷却时间（秒）：冷却期内该账号不可再次尝试
LOGIN_COOLDOWN_SECONDS = 300

# 名单分页：每页条数（管理员生效/已撤销名单）
LIST_PAGE_SIZE = 20

# 批量导入：每处理多少条执行一次 commit，降低长事务与内存占用
BATCH_IMPORT_COMMIT_EVERY = 100

# ---------- 业务文案常量（便于统一修改与 i18n） ----------
PLACEHOLDER_FILTER_EMPTY = "留空不限制"
MSG_ENTER_VALID_SID = "请输入有效学号。"
LABEL_INIT_LIST = "初始化名单"
