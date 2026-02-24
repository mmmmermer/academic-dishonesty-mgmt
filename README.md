# 校园学术失信人员管理系统

基于 Streamlit + SQLite 的校内学术失信名单管理、公示与查询系统。支持 Excel 导入、学号清洗、生效/已撤销名单管理、单条与批量查询、审计日志与备份恢复。

## 环境要求

- Python 3.9+
- 见 `requirements.txt`

## 安装与运行

```bash
# 安装依赖
pip install -r requirements.txt

# 首次使用：初始化数据库与默认管理员（admin / 123456）
python init_db.py

# 启动应用
python -m streamlit run app.py
```

浏览器访问默认地址（如 http://localhost:8501），使用默认管理员登录后可进行名单管理、用户管理等操作。

## 可选配置（环境变量）

未设置时使用代码内默认值，部署时可按需覆盖：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SESSION_TIMEOUT_MINUTES` | 无操作多少分钟后自动退出登录 | 30 |
| `LOGIN_FAIL_MAX` | 同一账号连续失败多少次后进入冷却 | 5 |
| `LOGIN_COOLDOWN_SECONDS` | 登录冷却时长（秒） | 300 |
| `LIST_PAGE_SIZE` | 管理员名单每页条数 | 20 |
| `BATCH_IMPORT_COMMIT_EVERY` | 批量导入每多少条提交一次 | 100 |

## 可选：运行测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## 项目结构（简要）

- `app.py` — 主入口、会话、水印、导航（表现层 + 应用控制层）
- `core/` — 核心业务与数据访问层
  - `config.py` — 常量与环境变量配置、业务文案
  - `database.py` — SQLite 连接与 `db_session` 上下文
  - `models.py` — 用户、名单、审计日志表定义
  - `auth.py` — 密码校验
  - `utils.py` — 学号清洗、Excel 解析、备份等工具
  - `session_store.py` — 基于 JSON 的服务端会话存储
- `views/` — 登录、教师端、管理员端页面
- `project_requirements.md` — 详细需求与约束说明
- `docs/` — 架构、可扩展性、优化建议、上线部署等文档

数据库文件与备份位于项目目录下（`database.db`、`backups/`），部署时注意权限与定期备份。
