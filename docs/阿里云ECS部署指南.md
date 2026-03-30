# 阿里云服务器部署指南（本系统 / Streamlit）

适用于将本仓库（学术道德监督管理系统）部署到 **阿里云 ECS** 或 **轻量应用服务器**。单机先用 **SQLite** 即可跑通；若需多实例或更高可用，请改用 **阿里云 RDS MySQL/PostgreSQL**，并设置 `DATABASE_URL`（详见 `docs/MySQL-PostgreSQL-上线指南.md`）。

---

## 一、阿里云侧准备

| 项目 | 说明 |
|------|------|
| 实例 | 建议 **2 核 4G** 及以上；系统可选 **Ubuntu 22.04 LTS** 或 **Alibaba Cloud Linux 3** |
| 公网 IP | 分配并绑定弹性公网 IP（或轻量自带公网） |
| 安全组 | 入方向放行：**22**（SSH）、**80**（HTTP）、**443**（HTTPS，若配置证书）；调试期可临时放行 **8501**（直连 Streamlit） |
| 域名（可选） | 备案域名解析到服务器公网 IP，便于配置 HTTPS |

> 安全组仅开放必要端口；生产环境建议关闭对公网的 8501，只通过 Nginx 的 80/443 访问。

---

## 二、连接服务器并安装基础环境

使用 SSH 登录（将 `root@你的公网IP` 换成实际用户与 IP）：

```bash
ssh root@你的公网IP
```

**Ubuntu / Debian：**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx
```

**Alibaba Cloud Linux / CentOS：**

```bash
sudo yum install -y python3 python3-pip git nginx
# 若需 venv：python3 -m ensurepip
```

确保 Python **3.9+**：

```bash
python3 --version
```

---

## 三、上传或克隆代码

**方式 A：Git（推荐）**

```bash
sudo mkdir -p /opt/hustsystem
sudo chown $USER:$USER /opt/hustsystem
cd /opt/hustsystem
git clone <你的仓库地址> .
```

**方式 B：本机打包上传**

在本机项目根目录打包（排除 `venv`、`.git` 等），用 `scp` 上传到服务器 `/opt/hustsystem/` 并解压。

---

## 四、虚拟环境与依赖

```bash
cd /opt/hustsystem
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 五、数据库初始化（默认 SQLite）

未设置 `DATABASE_URL` 时，使用项目根目录下的 `database.db`：

```bash
source venv/bin/activate
python init_db.py
```

默认管理员：**admin / 123456**（登录后请立即修改密码）。

---

## 六、手动试跑

```bash
source venv/bin/activate
python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501
```

浏览器访问 `http://服务器公网IP:8501` 验证。确认无误后按 **Ctrl+C** 停止，继续配置 systemd。

---

## 七、systemd 常驻（生产必做）

创建服务文件（路径按实际用户与目录修改）：

```bash
sudo nano /etc/systemd/system/hustsystem.service
```

示例内容：

```ini
[Unit]
Description=HUST 学术道德监督管理系统 (Streamlit)
After=network.target

[Service]
User=你的Linux用户名
WorkingDirectory=/opt/hustsystem
Environment="PATH=/opt/hustsystem/venv/bin"
# 若使用 MySQL/PostgreSQL，取消下一行注释并填写真实连接串（勿提交到 Git）
# Environment="DATABASE_URL=mysql+pymysql://用户:密码@127.0.0.1:3306/库名?charset=utf8mb4"
ExecStart=/opt/hustsystem/venv/bin/python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable hustsystem
sudo systemctl start hustsystem
sudo systemctl status hustsystem
```

查看日志：

```bash
journalctl -u hustsystem -f
```

---

## 八、Nginx 反向代理（推荐：80 端口访问）

将 `server_name` 换成你的域名或公网 IP：

```bash
sudo nano /etc/nginx/sites-available/hustsystem
```

```nginx
server {
    listen 80;
    server_name 你的域名或公网IP;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Sec-WebSocket-Key $http_sec_websocket_key;
        proxy_set_header Sec-WebSocket-Version $http_sec_websocket_version;
        proxy_read_timeout 86400;
    }
}
```

Ubuntu/Debian 启用站点并重载：

```bash
sudo ln -sf /etc/nginx/sites-available/hustsystem /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

之后用户通过 `http://域名或IP` 访问即可（无需 `:8501`）。若已配置 Nginx，可在阿里云安全组中**撤销**对 8501 的公网放行。

---

## 九、HTTPS（可选，需已备案域名）

在服务器安装 certbot 插件（以 Ubuntu + Nginx 为例）：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

按提示完成证书申请与自动续期。

---

## 十、使用阿里云 RDS（生产建议）

1. 在阿里云控制台创建 **RDS MySQL** 或 **PostgreSQL**，白名单加入 ECS **内网 IP**。
2. 在 RDS 中创建数据库与用户，字符集选 **utf8mb4**（MySQL）。
3. 在 ECS 上设置环境变量 `DATABASE_URL`（见 `docs/MySQL-PostgreSQL-上线指南.md`），并写入 **systemd** 的 `Environment=` 或独立 env 文件。
4. 再次执行 `python init_db.py`（在已激活 venv 且已 export `DATABASE_URL` 的前提下）。
5. 若计划 **多进程/多实例** Streamlit，需使用 MySQL/PostgreSQL，并设置 `MULTI_INSTANCE=1`；多实例 Nginx 示例见 `deploy/nginx_example.conf` 与 `deploy/README.md`。

---

## 十一、部署后必做

1. 修改默认管理员密码。
2. 定期备份：SQLite 时备份 `database.db` 与 `backups/`；使用 RDS 时用 RDS 自动备份与 `deploy/backup_example.sh` 思路做逻辑备份。
3. 项目根目录的 `logs/`、`database.db`（或 RDS）权限仅给运行服务的用户。

---

## 十二、相关文档索引

| 文档 | 内容 |
|------|------|
| `docs/云服务器部署-SQLite版.md` | 通用 Linux + SQLite + systemd + Nginx，可与本文对照 |
| `docs/MySQL-PostgreSQL-上线指南.md` | 切换 MySQL/PostgreSQL、连接串、`init_db` |
| `.env.example` | 环境变量示例（**勿**把真实密码提交仓库） |
| `deploy/README.md` | 多实例、备份脚本说明 |

---

## 十三、常见问题

**1. 浏览器打不开页面**  
检查：ECS 安全组、本机防火墙、`systemctl status hustsystem`、`nginx -t`、进程是否监听 `8501`。

**2. WebSocket / 页面一直加载**  
确认 Nginx 配置里包含 `Upgrade`、`Connection "upgrade"` 与较长的 `proxy_read_timeout`（见上文）。

**3. 上传文件失败**  
调大 Nginx `client_max_body_size`（已示例 50M），并确认 Streamlit 与业务侧限制（见 `core/config.py` 中上传大小等）。
