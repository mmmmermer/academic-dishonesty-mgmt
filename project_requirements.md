# 项目需求文档：校园学术失信人员管理系统
# Project Name: Academic Dishonesty Management System

## 1. 项目概述 (Overview)
这是一个用于高校内部管理的 Web 系统，旨在记录、公示和查询学术失信人员名单。
系统由**个人开发者**使用 AI 辅助开发，核心目标是**稳定、可靠、操作简单**。
- **核心逻辑**: Excel 导入名单库 -> 自动清洗 -> 本地 SQLite 存储 -> 前端公示与比对。
- **部署环境**: 学校办公室局域网 (Windows/Linux Server)。

## 2. 技术栈 (Tech Stack)
- **编程语言**: Python 3.9+
- **Web 框架**: Streamlit (界面构建，使用 `st.session_state` 管理状态)
- **数据处理**: Pandas (Excel 读取与清洗), OpenPyxl
- **数据库**: SQLite (单文件数据库，无需额外安装)
- **ORM 工具**: SQLAlchemy (用于数据库交互)
- **安全工具**: BCrypt (密码加密), Hashlib
- **图表工具**: Plotly Express (用于管理员仪表盘)

## 3. 数据库设计 (Database Schema)
请在 `models.py` 中严格定义以下表结构：

### 3.1 用户表 (users)
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer | 主键 |
| username | String | 登录账号 (工号/ID) |
| password_hash | String | BCrypt 加密后的密码 |
| full_name | String | 真实姓名 (用于显示水印) |
| role | String | 角色: 'admin' (管理员) 或 'teacher' (普通教师) |
| is_active | Boolean | 账号状态 (默认 True) |

### 3.2 失信名单主表 (blacklist)
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer | 主键 |
| name | String | 姓名 (索引) |
| id_card | String | 身份证号 (建议清洗后存储，必须唯一) |
| major | String | **所学专业** (新增字段，用于区分重名) |
| reason | Text | 失信/违规具体原因 |
| punishment_date | Date | 处分日期 |
| status | Integer | 状态: 1=生效中, 0=已撤销/软删除 |
| created_at | DateTime | 创建时间 |

### 3.3 审计日志表 (audit_logs)
*说明：此表只增不减，记录所有关键操作。*
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer | 主键 |
| operator_name | String | 操作人姓名 (冗余存储，防用户被删后查不到) |
| action_type | String | 类型枚举: LOGIN, QUERY_BATCH, IMPORT, ADD, DELETE, BACKUP |
| target | String | 操作对象简述 (如文件名或人名) |
| details | JSON/Text | 变更详情 (如修改前后的值) |
| timestamp | DateTime | 操作时间 |

## 4. 功能模块与权限 (Features & Roles)

### A. 普通教师端 (Teacher Role)
**设计原则**: 只读、查询、隐私保护。

1.  **全员名单公示 (List View)**:
    -   以表格形式分页展示所有 `status=1` 的人员。
    -   **隐私强制**: 身份证号列必须脱敏 (显示为 `320******1234`)。
    -   **筛选**: 支持按 `姓名`、`专业`、`年份` 进行组合筛选。
    -   **验证**: 提供“详情/验证”按钮，输入完整身份证号可验证是否匹配（但不直接显示完整号）。
2.  **批量智能比对 (Batch Check)**:
    -   **输入**: 仅支持上传 Excel 文件 (.xlsx)。
    -   **逻辑**: 系统在内存中读取上传文件，与数据库进行全量比对（基于 身份证号）。
    -   **反馈**: 
        -   若无命中：显示绿色成功提示。
        -   若有命中：显示红色警告，并列出命中人员详情。
    -   **导出**: 允许下载“比对结果报告” (Excel)，包含命中的人员及数据库中的详细信息。
3.  **个人记录**: 查看自己最近的操作历史。

### B. 管理员端 (Admin Role)
拥有 Teacher 所有功能，并额外包含：

1.  **数据仪表盘 (Dashboard)**:
    -   位于首页顶部。
    -   显示：总人数统计、按“专业”分布的饼图、按“年份”分布的柱状图。
2.  **名单数据库管理**:
    -   **批量导入**: 上传 Excel 更新数据库。
        -   *必须包含数据清洗*: 自动去除身份证空格、全角转半角、小写x转大写X。
        -   *容错*: 如果 Excel 列名不匹配，抛出友好的中文错误提示。
    -   **单条操作**: 手动新增人员、编辑现有人员信息。
    -   **软删除**: 点击删除时，将 `status` 设为 0（不物理删除）。
3.  **系统维护与备份 (Maintenance)**:
    -   **自动备份**: 系统启动时自动备份 `database.db` 到 `backups/` 目录。
    -   **手动全库备份 (重要)**: 提供一个 `st.download_button`，允许管理员下载当前的 `.db` 数据库文件（文件名带时间戳）。
4.  **日志审计**: 查看所有用户的操作日志表格。
5.  **用户管理**: 简单的用户注册审批或密码重置。

## 5. UI/UX 设计规范 (UI Guidelines)
1.  **水印防护**: 全局背景必须包含浅灰色水印（内容：当前登录人姓名 + 工号），防止截屏泄露。
2.  **交互反馈**:
    -   所有耗时操作（如导入、比对）必须包裹在 `with st.spinner('正在处理...'):` 中。
    -   导入成功显示 `st.balloons()`。
3.  **容错性**: 
    -   禁止显示代码层面的 Traceback 报错。所有 `try-except` 捕获后需通过 `st.error` 显示中文提示。

## 6. 开发建议 (Development Instructions for Cursor)
请按照以下步骤生成代码：
1.  **Phase 1 (Infrastructure)**: 创建 `database.py` (连接), `models.py` (表定义), `auth.py` (BCrypt 加密与校验)。
2.  **Phase 2 (Utils)**: 创建 `utils.py`，实现 Excel 解析、身份证清洗 (`clean_id_card`)、脱敏 (`mask_id_card`)、备份逻辑。
3.  **Phase 3 (UI - Auth)**: 创建 `app.py` 和登录页面，实现 Session 状态管理。
4.  **Phase 4 (UI - Features)**: 创建 `views/teacher_page.py` (查询/比对) 和 `views/admin_page.py` (管理/备份/图表)。
5.  **Phase 5 (Testing)**: 创建 `init_db.py` 初始化管理员账号 (admin / 123456)。

## 7. 特别约束
- **Excel 依赖**: 使用 `pandas` 读取 Excel，`engine='openpyxl'`。
- **环境**: 生成 `requirements.txt`。
- **备份**: 确保 `get_database_file` 函数以二进制模式 ('rb') 读取文件以供下载。