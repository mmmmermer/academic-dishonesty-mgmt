# MySQL / PostgreSQL 上线部署指南

本文档说明如何将系统从默认的 SQLite 切换到 MySQL 或 PostgreSQL，并在生产环境中运行。

---

## 一、前置条件

| 项目 | 要求 |
|------|------|
| Python | 3.9+ |
| MySQL | 5.7+ 或 8.0+（推荐 8.0）；建库时指定 `utf8mb4` 字符集 |
| PostgreSQL | 12+（推荐 15+） |
| pip 依赖 | MySQL 需 `pymysql`；PostgreSQL 需 `psycopg2-binary`（均已在 requirements.txt 中） |

---

## 二、配置 DATABASE_URL

系统通过环境变量 `DATABASE_URL` 决定数据库后端。未设置时默认使用 SQLite。

### 2.1 MySQL

```bash
# Linux / macOS
export DATABASE_URL="mysql+pymysql://用户名:密码@主机:3306/库名?charset=utf8mb4"

# Windows (PowerShell)
$env:DATABASE_URL = "mysql+pymysql://用户名:密码@主机:3306/库名?charset=utf8mb4"

# Windows (CMD)
set DATABASE_URL=mysql+pymysql://用户名:密码@主机:3306/库名?charset=utf8mb4
```

> **重要**：连接串中务必带 `?charset=utf8mb4`，否则中文写入可能乱码。

### 2.2 PostgreSQL

```bash
# Linux / macOS
export DATABASE_URL="postgresql+psycopg2://用户名:密码@主机:5432/库名"

# Windows (PowerShell)
$env:DATABASE_URL = "postgresql+psycopg2://用户名:密码@主机:5432/库名"
```

### 2.3 使用 .env 文件（推荐）

项目提供 `.env.example`，复制并填写：

```bash
cp .env.example .env
# 编辑 .env，填入真实连接串
```

若使用 `python-dotenv` 或在 systemd 中配置 `EnvironmentFile`，可自动加载。

> ⚠️ `.env` 已在 `.gitignore` 中，**切勿提交到版本库**。

---

## 三、新建数据库

### 3.1 MySQL

```sql
CREATE DATABASE hustsystem
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 创建专用用户（按需）
CREATE USER 'hustuser'@'%' IDENTIFIED BY '你的密码';
GRANT ALL PRIVILEGES ON hustsystem.* TO 'hustuser'@'%';
FLUSH PRIVILEGES;
```

对应 `DATABASE_URL`：

```
mysql+pymysql://hustuser:你的密码@127.0.0.1:3306/hustsystem?charset=utf8mb4
```

### 3.2 PostgreSQL

```sql
CREATE DATABASE hustsystem ENCODING 'UTF8';

-- 创建专用用户（按需）
CREATE USER hustuser WITH PASSWORD '你的密码';
GRANT ALL PRIVILEGES ON DATABASE hustsystem TO hustuser;
```

对应 `DATABASE_URL`：

```
postgresql+psycopg2://hustuser:你的密码@127.0.0.1:5432/hustsystem
```

---

## 四、初始化表结构

设置好 `DATABASE_URL` 后，在项目根目录执行：

```bash
python init_db.py
```

该脚本会：
1. 根据 `core/models.py` 创建所有表（`users`、`blacklist`、`audit_logs`）及索引；
2. 创建默认管理员账号（`admin` / `123456`）。

> **上线后务必立即登录并修改默认密码。**

---

## 五、从现有 SQLite 迁移数据

如果本地已有 `database.db` 中的数据需要迁移到 MySQL/PostgreSQL：

```bash
# 1. 确保 database.db 在项目根目录
# 2. 设置目标 DATABASE_URL（不能指向 SQLite）
# 3. 执行迁移脚本
python migrate_sqlite_to_external.py
```

脚本行为：
- 只读打开 SQLite，按 User → Blacklist → AuditLog 顺序分批写入目标库；
- 目标库已有用户数据时跳过，避免重复插入；
- 迁移完成后，启动应用并验证数据。

---

## 六、启动应用

```bash
# 单实例
python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501

# 多实例（见 deploy/start_streamlit_workers.sh）
bash deploy/start_streamlit_workers.sh
```

### 常驻运行（Linux systemd 示例）

```ini
[Unit]
Description=学术失信管理系统
After=network.target mysql.service

[Service]
User=www
WorkingDirectory=/opt/hustsystem
EnvironmentFile=/opt/hustsystem/.env
ExecStart=/opt/hustsystem/venv/bin/python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable hustsystem
sudo systemctl start hustsystem
```

---

## 七、生产备份与恢复

使用 MySQL/PostgreSQL 后，管理后台的「备份下载 / 数据恢复」按钮将**不可用**（界面已提示），需通过数据库原生工具备份。

### 7.1 MySQL 备份

```bash
# 全量备份
mysqldump -u hustuser -p hustsystem > /opt/backups/hustsystem_$(date +%Y%m%d_%H%M%S).sql

# 仅结构
mysqldump -u hustuser -p --no-data hustsystem > /opt/backups/schema_$(date +%Y%m%d).sql
```

### 7.2 PostgreSQL 备份

```bash
# 全量备份
pg_dump -U hustuser hustsystem -f /opt/backups/hustsystem_$(date +%Y%m%d_%H%M%S).sql

# 仅结构
pg_dump -U hustuser -s hustsystem -f /opt/backups/schema_$(date +%Y%m%d).sql
```

### 7.3 定时备份（cron 示例）

```bash
# 每天凌晨 3 点全量备份，保留 30 天

# MySQL
0 3 * * * mysqldump -u hustuser -p'你的密码' hustsystem | gzip > /opt/backups/hustsystem_$(date +\%Y\%m\%d).sql.gz && find /opt/backups -name "hustsystem_*.sql.gz" -mtime +30 -delete

# PostgreSQL（通过 .pgpass 免密）
0 3 * * * pg_dump -U hustuser hustsystem | gzip > /opt/backups/hustsystem_$(date +\%Y\%m\%d).sql.gz && find /opt/backups -name "hustsystem_*.sql.gz" -mtime +30 -delete
```

### 7.4 恢复

```bash
# MySQL
mysql -u hustuser -p hustsystem < backup.sql

# PostgreSQL
psql -U hustuser hustsystem < backup.sql
```

---

## 八、多实例部署

多实例部署（多个 Streamlit 进程 + Nginx 反向代理）时：

- **必须使用 MySQL 或 PostgreSQL**，禁止 SQLite（SQLite 写操作串行，多进程写会锁库）。
- 设置 `MULTI_INSTANCE=1`，应用启动时会检查是否为 SQLite 并打告警日志。
- Nginx 配置示例见 `deploy/nginx_example.conf`（含 WebSocket 转发和 `ip_hash` 会话粘滞）。
- 多进程启动脚本见 `deploy/start_streamlit_workers.sh`。

---

## 九、安全注意事项

- `DATABASE_URL` 含有数据库密码，**不得**写入日志、暴露给前端或提交到版本库。
- 数据库用户权限应仅授予本应用所需的库，不使用 root/超级用户连接。
- 上线后立即修改默认管理员密码（`admin` / `123456`）。
- 防火墙仅放行 Streamlit 端口（或 Nginx 80/443），不暴露数据库端口。

---

## 十、快速清单

| 步骤 | 命令/操作 |
|------|-----------|
| 1. 安装依赖 | `pip install -r requirements.txt` |
| 2. 创建数据库 | MySQL: `CREATE DATABASE ... utf8mb4`；PG: `CREATE DATABASE ...` |
| 3. 配置连接 | 设置环境变量 `DATABASE_URL` 或编辑 `.env` |
| 4. 建表 | `python init_db.py` |
| 5. 迁移数据（可选） | `python migrate_sqlite_to_external.py` |
| 6. 启动 | `python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501` |
| 7. 修改默认密码 | 登录后在管理后台修改 |
| 8. 配置备份 | cron + mysqldump / pg_dump |
| 9. 多实例（可选） | Nginx + 多进程 + `MULTI_INSTANCE=1` |
