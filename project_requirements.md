# 项目需求文档：校园学术失信人员管理系统
# Project Name: Academic Dishonesty Management System

## 1. 项目概述 (Overview)
这是一个用于高校内部管理的 Web 系统，旨在记录、公示和查询学术失信人员名单。
系统由**个人开发者**使用 AI 辅助开发，核心目标是**稳定、可靠、操作简单**。
- **核心逻辑**: Excel 导入名单库 → 自动清洗（学号等）→ 本地 SQLite 存储 → 前端公示与比对。
- **身份标识**: 名单以**学号**为唯一标识进行存储与比对（原需求为身份证号，已优化为学号）。
- **部署环境**: 学校办公室局域网 (Windows/Linux Server)。

## 2. 技术栈 (Tech Stack)
- **编程语言**: Python 3.9+
- **Web 框架**: Streamlit（界面构建，使用 `st.session_state` 管理状态）
- **数据处理**: Pandas（Excel 读取与清洗）、OpenPyxl
- **数据库**: SQLite（单文件数据库，无需额外安装）
- **ORM 工具**: SQLAlchemy（数据库交互）
- **安全工具**: BCrypt（密码加密与校验）
- **图表工具**: Plotly Express（管理员仪表盘饼图等）

## 3. 数据库设计 (Database Schema)
在 `models.py` 中定义以下表结构：

### 3.1 用户表 (users)
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer | 主键 |
| username | String | 登录账号（工号/ID） |
| password_hash | String | BCrypt 加密后的密码 |
| full_name | String | 真实姓名（用于显示水印） |
| role | String | 角色：'admin'（管理员）或 'teacher'（教师） |
| is_active | Boolean | 账号状态（默认 True） |

### 3.2 失信名单主表 (blacklist)
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer | 主键 |
| name | String | 姓名（索引） |
| student_id | String | **学号**（唯一，建议清洗后存储；实现中可与旧库列名 id_card 兼容） |
| major | String | 所学专业（用于区分重名） |
| reason | Text | 失信/违规具体原因 |
| punishment_date | Date | 处分日期 |
| status | Integer | 状态：1=生效中，0=已撤销/软删除 |
| created_at | DateTime | 创建时间 |

### 3.3 审计日志表 (audit_logs)
*说明：此表只增不减，记录所有关键操作。*
| 字段名 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer | 主键 |
| operator_name | String | 操作人姓名（冗余存储） |
| action_type | String | 类型：LOGIN, QUERY_SINGLE, QUERY_BATCH, IMPORT, ADD, DELETE, RESTORE, BACKUP |
| target | String | 操作对象简述（如文件名或人名） |
| details | Text | 变更详情（如修改前后值） |
| timestamp | DateTime | 操作时间 |

## 4. 功能模块与权限 (Features & Roles)

### A. 普通教师端 (Teacher Role)
**设计原则**: 只读、查询。

1. **单条查询**
   - 输入：学生**姓名**或**学号**（支持回车提交）。
   - 仅查询 `status=1` 的生效记录；学号在界面与报告中**完整显示**。
   - 未命中时提示「未查询到违规记录，该生信用良好」；命中时展示姓名、学号、专业、原因、处分日期。
2. **批量智能比对**
   - **输入**: 支持上传 Excel 文件 (.xlsx / .xls)，表格至少包含「**学号**」列。
   - **逻辑**: 在内存中读取上传文件，对学号列清洗后与数据库进行全量比对（基于学号）。
   - **反馈**:
     - 若无命中：绿色成功提示。
     - 若有命中：红色警告，命中结果以**表格**展示，支持**分页**（每页最多 10 条）。
   - **导出**: 可下载「比对结果报告」Excel，包含命中人员及数据库中的详细信息（学号完整显示）。

### B. 管理员端 (Admin Role)
拥有教师端所有功能（含单条查询、批量比对），并额外包含：

1. **数据仪表盘 (Dashboard)**
   - 位于管理员页第一个 Tab。
   - 显示：名单总数、生效中数量、已撤销数量；按「专业」分布的**饼图**。
2. **名单数据库管理**
   - **批量导入**: 上传 Excel 更新数据库。
     - 必需列：**姓名、学号、专业、原因、处分时间**。
     - *数据清洗*: 学号自动去空格、全角转半角。
     - *容错*: 列名不匹配时抛出友好中文错误提示；学号已存在则更新该条记录。
   - **单条操作**: 手动新增人员（姓名、学号、专业、原因、处分日期）；学号唯一，重复时提示。
   - **列表与软删除**: 表格展示所有生效记录；支持按学号软删除（将 `status` 设为 0）。
   - **已撤销名单与恢复**: 表格展示已撤销记录；支持按学号恢复为生效（将 `status` 设为 1）。
   - **批量查询**: 上传含「学号」列的 Excel (.xlsx / .xls)，与生效名单比对，可下载比对结果报告（学号完整显示）。
3. **系统维护与备份**
   - **审计日志**: 展示所有用户操作日志表格（最近 500 条）。
   - **手动全库备份**: 提供 `st.download_button`，下载当前 `.db` 文件（文件名带时间戳）；底层通过 `get_db_file_bytes()` 以二进制模式 ('rb') 读取。
   - （可选扩展）系统启动时自动备份 `database.db` 到 `backups/` 目录，由 `utils.auto_backup()` 实现。
4. **用户管理**
   - 用户列表（工号、姓名、角色、状态）。
   - 新增用户：工号、密码、真实姓名、角色（教师/管理员）。
   - 密码重置：选择用户并设置新密码。
   - 启用/禁用账号：切换用户状态（不可禁用当前登录账号）。

## 5. UI/UX 设计规范 (UI Guidelines)
1. **水印防护**: 全局背景浅灰色水印，内容为「当前登录人姓名 + 工号」，防止截屏泄露。
2. **交互反馈**:
   - 耗时操作（导入、比对、查询等）使用 `st.spinner('正在处理...')` 等提示。
   - 导入成功可显示 `st.balloons()`。
3. **容错性**: 禁止直接暴露代码 Traceback；`try-except` 捕获后通过 `st.error` 等展示中文提示。

## 6. 项目结构与开发阶段 (Structure & Phases)
- **Phase 1 (Infrastructure)**: `database.py`（连接与会话）、`models.py`（表定义）、`auth.py`（BCrypt 校验）、`init_db.py`（建表与默认管理员 admin/123456）。
- **Phase 2 (Utils)**: `utils.py` — 学号清洗 `clean_student_id`、黑名单 Excel 解析 `parse_blacklist_excel`（列：姓名、学号、专业、原因、处分时间；支持 .xlsx / .xls）、批量比对 Excel 解析 `parse_batch_check_excel`（至少学号列）、备份 `auto_backup`、`get_db_file_bytes`。
- **Phase 3 (UI - Auth)**: `app.py`（主入口、会话初始化、水印、导航）、`views/login.py`（登录页）。
- **Phase 4 (UI - Features)**: `views/teacher_page.py`（单条查询 + 批量比对，分页与报告下载）、`views/admin_page.py`（仪表盘、名单管理、系统维护、用户管理）。

**运行方式**: 在项目根目录执行 `python -m streamlit run app.py`；首次使用前执行 `python init_db.py` 初始化数据库与默认管理员。

## 7. 特别约束 (Constraints)
- **Excel**: 使用 `pandas` 读取，.xlsx 用 openpyxl、.xls 用 xlrd；导入与批量比对所需列见上文。
- **备份下载**: 提供 `get_db_file_bytes()`，以二进制模式 ('rb') 读取 `database.db` 供下载按钮使用。
- **环境**: 提供 `requirements.txt`，包含 streamlit、sqlalchemy、bcrypt、pandas、openpyxl、plotly 等依赖。
