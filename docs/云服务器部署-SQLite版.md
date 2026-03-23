# 云服务器部署指南（SQLite 版）

适用于 2 核 2G 等轻量配置，无需安装 MySQL/PostgreSQL。

---

## 一、前置条件

- Linux 服务器（Ubuntu 20.04+ / CentOS 7+ / Debian 10+）
- Python 3.9+
- 已放行 80 端口（云厂商安全组 + 系统防火墙）

---

## 二、快速部署（推荐）

在服务器上执行：

```bash
# 1. 进入部署目录
cd /opt
sudo git clone https://github.com/mmmmermer/academic-dishonesty-mgmt.git
sudo chown -R $USER:$USER academic-dishonesty-mgmt
cd academic-dishonesty-mgmt

# 2. 创建虚拟环境并安装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 初始化数据库（创建表 + 默认管理员 admin/123456）
python init_db.py

# 4. 测试运行（Ctrl+C 退出后继续下一步）
python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501
# 浏览器访问 http://服务器IP:8501 验证
```

验证无误后，按下面步骤配置常驻运行和 Nginx。

---

## 三、配置 systemd 常驻运行

```bash
sudo nano /etc/systemd/system/hustsystem.service
```

写入（**替换** `你的用户名` 和实际路径）：

```ini
[Unit]
Description=华中科技大学学术道德监督管理系统
After=network.target

[Service]
User=你的用户名
WorkingDirectory=/opt/academic-dishonesty-mgmt
Environment="PATH=/opt/academic-dishonesty-mgmt/venv/bin"
ExecStart=/opt/academic-dishonesty-mgmt/venv/bin/python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true
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

---

## 四、配置 Nginx 反向代理（可选，推荐）

通过 80 端口访问，且支持 WebSocket：

```bash
sudo nano /etc/nginx/sites-available/hustsystem
```

写入（**替换** `你的域名或IP`）：

```nginx
server {
    listen 80;
    server_name 你的域名或IP;

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

启用配置：

```bash
# Ubuntu/Debian
sudo ln -sf /etc/nginx/sites-available/hustsystem /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# CentOS
sudo ln -sf /etc/nginx/sites-available/hustsystem /etc/nginx/conf.d/hustsystem.conf
sudo nginx -t && sudo systemctl reload nginx
```

---

## 五、防火墙

```bash
# Ubuntu (ufw)
sudo ufw allow 80
sudo ufw enable

# CentOS (firewalld)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload
```

云厂商控制台的安全组中放行 **80** 端口。

---

## 六、部署后必做

1. **修改默认密码**：用 `admin` / `123456` 登录后，在管理后台修改。
2. **数据目录**：`database.db`、`backups/`、`logs/` 均在项目根目录，定期备份 `database.db` 和 `backups/`。

---

## 七、常用命令

| 操作 | 命令 |
|------|------|
| 查看状态 | `sudo systemctl status hustsystem` |
| 重启 | `sudo systemctl restart hustsystem` |
| 查看日志 | `journalctl -u hustsystem -f` |
| 更新代码 | `cd /opt/academic-dishonesty-mgmt && git pull && sudo systemctl restart hustsystem` |

---

## 八、目录结构（部署后）

```
/opt/academic-dishonesty-mgmt/
├── app.py
├── core/
├── views/
├── venv/
├── database.db      # SQLite 数据文件（自动生成）
├── backups/         # 自动备份目录（启动时生成）
├── logs/            # 应用日志
└── sessions.json    # 会话文件（自动生成）
```
 