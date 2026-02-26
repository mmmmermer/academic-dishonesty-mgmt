# 部署示例（阶段三多实例 + 阶段四备份）

本目录提供多实例部署与生产备份的配置与脚本示例。

| 文件 | 说明 |
|------|------|
| `nginx_example.conf` | Nginx 反向代理示例：多后端、会话粘滞、WebSocket。详见 **docs/阶段实施与审计.md**。 |
| `start_streamlit_workers.sh` | 单机多进程启动脚本（Linux/macOS），默认端口 8501/8502/8503。 |
| `backup_example.sh` | 生产备份命令示例（mysqldump / pg_dump），需替换用户、库名、路径后使用。详见 **docs/阶段实施与审计.md**。 |

**运行前**：请先完成阶段一（MySQL/PostgreSQL + DATABASE_URL）、阶段二（索引与分页）；多实例下禁止使用 SQLite。
