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
# 审计类型中文名（用于界面展示）
AUDIT_TYPE_NAMES = {
    AUDIT_LOGIN: "登录",
    AUDIT_QUERY_SINGLE: "单条查询",
    AUDIT_QUERY_BATCH: "批量查询",
    AUDIT_IMPORT: "批量导入",
    AUDIT_ADD: "新增/编辑",
    AUDIT_DELETE: "删除/初始化",
    AUDIT_RESTORE: "恢复",
    AUDIT_BACKUP: "备份/恢复",
}

# 名单状态
BLACKLIST_STATUS_EFFECTIVE = 1
BLACKLIST_STATUS_REVOKED = 0

# 会话超时（分钟）：无操作超过此时长将自动退出登录
SESSION_TIMEOUT_MINUTES = 30
# 会话超时前提醒（分钟）：剩余时间少于此值时在页面上提示用户
SESSION_TIMEOUT_WARN_MINUTES = 5

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
LABEL_DISPLAY_OPTIONS_EXPANDER = "显示选项（每页条数、排序列、顺序）"
# 教师端批量比对仅含每页条数，与管理员名单的「显示选项」区分
LABEL_BATCH_PAGE_OPTIONS = "每页条数"
# 排序顺序选项（与 True/False 对应）
SORT_ORDER_ASC = "升序"
SORT_ORDER_DESC = "降序"
SORT_ORDER_OPTIONS = [SORT_ORDER_ASC, SORT_ORDER_DESC]

# ---------- 界面用语与术语（全系统统一） ----------
# 术语：学生标识用「学号」，登录账号用「工号」；名单=整体，记录=单条；状态用「生效」「已撤销」
LABEL_STUDENT_ID = "学号"
LABEL_STAFF_ID = "工号"
LABEL_NAME = "姓名"
LABEL_MAJOR = "专业"
LABEL_REASON = "原因"
LABEL_PUNISHMENT_DATE = "处分日期"
LABEL_PUNISHMENT_TIME = "处分时间"
# 通用提示句尾（失败类）
MSG_TRY_AGAIN = "请稍后重试。"
MSG_TRY_AGAIN_OR_ADMIN = "请稍后重试或联系管理员。"
# 登录页
TITLE_APP = "校园学术失信人员管理系统"
SUBTITLE_LOGIN = "请登录"
LABEL_LOGIN_USERNAME = "工号"
LABEL_LOGIN_PASSWORD = "密码"
BTN_LOGIN = "登录"
MSG_ENTER_USERNAME_PASSWORD = "请输入工号和密码。"
MSG_USERNAME_TOO_LONG = "工号长度超出限制，请检查后重试。"
MSG_LOGIN_TOO_MANY_FAIL = "登录失败次数过多，请 5 分钟后再试。"
MSG_LOGIN_WRONG = "工号或密码错误。"
MSG_ACCOUNT_DISABLED = "该账号已停用，请联系管理员。"
MSG_LOGIN_ERROR = "登录过程出错，请稍后重试。"
# 侧栏与全局
LABEL_CURRENT_USER = "当前用户"
LABEL_ROLE = "角色"
LABEL_LOGOUT = "退出登录"
LABEL_SELECT_FEATURE = "选择功能"
LABEL_SELECT_PANEL = "选择功能板块"
ROLE_ADMIN = "管理员"
ROLE_TEACHER = "教师"
MSG_SESSION_TIMEOUT = "登录已超时，请重新登录。"
MSG_SESSION_TIMEOUT_SOON = "登录即将超时（约 {mins} 分钟后），请及时操作以保持登录。"
MSG_UNKNOWN_ROLE = "未知角色，请联系管理员。"
MSG_SYSTEM_ERROR = "系统遇到异常，请稍后重试或联系管理员。"
# 教师端：查询与比对
TITLE_TEACHER = "学术诚信档案查询"
CAPTION_TEACHER = "仅查询生效中的失信记录。"
TERM_DISHONEST_RECORD = "失信记录"  # 统一用「失信记录」，不再混用「失信/违规」
MSG_NO_RECORD_GOOD = "未查询到失信记录，所查学生信用良好。"
MSG_BATCH_NO_HIT_GOOD = "未查询到失信记录，名单内学生信用良好。下方为本次上传的未命中名单，可查看或下载。"
MSG_HAVE_HIT = "共命中 {n} 条失信记录，请核实。"
CAPTION_BATCH_INTRO = "上传包含「学号」列的 Excel（.xlsx / .xls），与生效名单比对；可下载比对结果。"
# 空状态与操作反馈
EMPTY_NO_RECORDS = "暂无记录。"
EMPTY_NO_USER = "暂无用户。"
EMPTY_CANNOT_GET_USER = "无法获取当前用户。"
EMPTY_NO_OPERATION_LOG = "暂无操作记录。"
SUCCESS_SAVED = "已保存修改。"
SUCCESS_ADDED = "已添加。"
SUCCESS_IMPORT_DONE = "导入完成，请查看下方「上次导入结果」了解详情。"
SUCCESS_PWD_RESET = "密码已重置。"
SUCCESS_DB_RESTORED = "数据库已恢复，即将刷新页面。"
# 管理员：名单与操作
EMPTY_NO_EFFECTIVE = "暂无生效记录。"
EMPTY_NO_REVOKED = "暂无已撤销记录。"
CAPTION_FILTER_BY_NAME_SID_MAJOR = "可按姓名、学号、专业筛选（留空表示不限制）。"
MSG_NOT_FOUND_EFFECTIVE = "未找到该学号的生效记录。"
MSG_NOT_FOUND_REVOKED = "未找到该学号的已撤销记录。"
MSG_CONFIRM_INIT_LIST = "确定要初始化名单吗？此操作将把所有生效记录设为已撤销，生效名单将为空。"
SUCCESS_INIT_LIST = "名单已初始化，生效名单已清空。"
CAPTION_CONFIRM_RESTORE_DB = "请先勾选上方确认框后再执行恢复。"
MSG_UPLOAD_EMPTY = "上传文件为空，无法恢复。"
MSG_DB_RESTORE_FAIL = "恢复失败，请确认上传的是有效的 .db 备份文件。"
MSG_DB_READ_FAIL = "读取数据库文件失败，请检查备份目录或联系管理员。"

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

# ---------- Session 键名（app/login 用，减少魔法字符串） ----------
SESSION_KEY_LOGGED_IN = "logged_in"
SESSION_KEY_USER_ROLE = "user_role"
SESSION_KEY_USER_NAME = "user_name"
SESSION_KEY_USER_ID = "user_id"
SESSION_KEY_USERNAME = "username"
SESSION_KEY_LAST_ACTIVITY = "last_activity_at"
SESSION_KEY_LOGIN_FAIL_RECORDS = "login_fail_records"
SESSION_KEY_AUTO_BACKUP_DONE = "auto_backup_done"
