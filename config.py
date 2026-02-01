"""
应用常量：审计操作类型、业务文案、分页与学号校验等，便于维护与统一引用。
所有 UI 文案与可调参数集中在此，便于 i18n 与运维配置。
"""

# ---------- 审计日志操作类型（与 models.AuditLog.action_type 一致） ----------
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

# 名单分页：默认每页条数、可选每页条数（供用户选择）
LIST_PAGE_SIZE = 20
LIST_PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

# 批量导入：每处理多少条执行一次 commit，降低长事务与内存占用
BATCH_IMPORT_COMMIT_EVERY = 100
# 单次导入最大行数，超过则拒绝，避免内存耗尽
MAX_IMPORT_ROWS = 10000

# ---------- 业务文案常量（便于统一修改与 i18n） ----------
PLACEHOLDER_FILTER_EMPTY = "留空不限制"
MSG_ENTER_VALID_SID = "请输入有效学号。"
LABEL_INIT_LIST = "初始化名单"
LABEL_PAGE_SIZE = "每页条数"
LABEL_SORT_COLUMN = "按列"
LABEL_SORT_ORDER = "顺序"
LABEL_TABLE_SORT = "表格排序"
LABEL_DISPLAY_AND_SORT = "显示与排序"
# 排序顺序选项（与 True/False 对应）
SORT_ORDER_ASC = "升序"
SORT_ORDER_DESC = "降序"
SORT_ORDER_OPTIONS = [SORT_ORDER_ASC, SORT_ORDER_DESC]

# 学号校验：长度限制（清洗后）
STUDENT_ID_MIN_LEN = 1
STUDENT_ID_MAX_LEN = 32

# 用户与密码：与 models.User 字段一致，便于校验与安全
USERNAME_MAX_LEN = 64
PASSWORD_MIN_LEN = 6
# BCrypt 有效输入上限 72 字节，超长输入直接拒绝避免无谓计算
PASSWORD_MAX_BYTES = 72

# Excel 下载 MIME 类型
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
