# 华中科技大学学术道德监督管理系统

基于 Streamlit + SQLAlchemy 的校内学术失信名单管理、公示与查询系统。支持 Excel 导入、拼音/首字母/学号多维搜索、PDF 认定结论上传与预览、生效/已撤销名单管理、批量比对、审计日志与备份恢复。

## 环境要求

- Python 3.9+
- 依赖详见 `requirements.txt`
- 生产环境推荐 PostgreSQL 16+；本地开发可使用 SQLite（需设置 `ALLOW_SQLITE_FALLBACK=1`）

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 首次使用：初始化数据库与默认管理员（admin / 123456）
python init_db.py

# 启动应用
python -m streamlit run app.py
```

浏览器访问 http://localhost:8501，使用默认管理员登录。**部署后请立即修改默认密码**。

## 可选配置（环境变量）

未设置时使用代码内默认值，详见 `.env.example`。

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DATABASE_URL` | 数据库连接字符串 | SQLite (`database.db`) |
| `ALLOW_SQLITE_FALLBACK` | 允许使用 SQLite（开发用） | `0` |
| `SESSION_TIMEOUT_MINUTES` | 无操作多少分钟后自动退出登录 | `30` |
| `LOGIN_FAIL_MAX` | 同一账号连续失败多少次后进入冷却 | `5` |
| `LOGIN_COOLDOWN_SECONDS` | 登录冷却时长（秒） | `300` |
| `LIST_PAGE_SIZE` | 管理员名单每页条数 | `20` |
| `BATCH_IMPORT_COMMIT_EVERY` | 批量导入每多少条提交一次 | `100` |

## 项目结构

```
hustsystem/
├── app.py                      # 应用入口（路由、会话、水印、超时）
├── init_db.py                  # 数据库初始化脚本
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量配置示例
├── start_system.bat            # Windows 一键启动（PostgreSQL）
├── stop_system.bat             # Windows 一键停止
│
├── core/                       # 核心层（业务逻辑、数据访问、配置）
│   ├── config.py               # 常量、文案、阈值
│   ├── models.py               # ORM 模型（User/Blacklist/AuditLog）
│   ├── database.py             # 数据库连接池、db_session()
│   ├── auth.py                 # BCrypt 密码校验
│   ├── search.py               # 搜索引擎（拼音/首字母/学号/多项）
│   ├── session_store.py        # 服务端会话存储（JSON + 文件锁）
│   ├── student_id.py           # 学号清洗与校验
│   ├── excel_processor.py      # Excel 导入/导出/防注入
│   ├── file_safe_guard.py      # PDF 上传校验/路径穿越防护
│   ├── pdf_server.py           # PDF 安全提供服务
│   ├── audit_logger.py         # 审计日志写入
│   └── system_operations.py    # 数据库备份/恢复
│
├── views/                      # 视图层（UI 渲染）
│   ├── login.py                # 登录页
│   ├── components.py           # 通用 UI 组件（表格/分页/筛选/导出）
│   ├── admin/                  # 管理员子模块
│   │   ├── __init__.py         # 管理员路由分发
│   │   ├── list_query.py       # 名单查询（搜索+详情+PDF预览）
│   │   ├── management.py       # 名单管理（导入/增删改/状态切换）
│   │   ├── system.py           # 系统维护（审计日志/备份/恢复/归档）
│   │   └── user_mgmt.py        # 用户管理（增删/密码/启用禁用）
│   └── teacher/                # 教师端子模块
│       ├── __init__.py         # 教师路由分发
│       ├── single_search.py    # 单条查询（拼音/详情卡片/PDF预览）
│       ├── batch_check.py      # 批量智能比对
│       ├── list_query.py       # 名单查询（只读）
│       └── my_logs.py          # 个人操作记录
│
├── docs/                       # 项目文档
│   ├── 系统需求与架构总文档.md   # 需求、架构、部署总览
│   ├── 项目架构与模块说明.md     # 开发者指南、修改指南
│   ├── 部署与运维综合手册.md     # 三种部署路线详解
│   ├── 用户使用说明.md / .pdf   # 用户操作手册
│   └── UI设计布局与交互说明.md  # UI/UX 文档
│
├── deploy/                     # 部署配置
│   └── nginx_hustsystem.conf   # Nginx 反向代理（安全头+限流+WSS）
│
├── scripts/                    # 运维脚本
│   ├── start_app_postgres_bg.ps1
│   ├── stop_app_postgres_bg.ps1
│   └── ...
│
└── static/                     # 静态文件
    ├── robots.txt              # 禁止爬虫
    └── pdf_files/              # PDF 认定结论存储
```

## 文档索引

| 文档 | 说明 |
|------|------|
| [系统需求与架构总文档](docs/系统需求与架构总文档.md) | 需求、数据模型、技术栈、部署总览 |
| [项目架构与模块说明](docs/项目架构与模块说明.md) | 开发者指南：分层、依赖、修改指南 |
| [部署与运维综合手册](docs/部署与运维综合手册.md) | 局域网/云服务器/高可用集群部署 |
| [用户使用说明](docs/用户使用说明.md) | 管理员与教师操作手册 |
| [UI设计布局与交互说明](docs/UI设计布局与交互说明.md) | UI/UX 设计文档 |

## 安全特性

- BCrypt 密码哈希 + 登录暴力破解防护
- 参数化 SQL + LIKE 转义
- PDF 上传魔数校验 + 路径穿越防护
- Excel 导出防公式注入
- 全局水印 + XSS 转义
- 审计日志全操作覆盖（只增不删）
- Nginx 安全头 + IP 限流
