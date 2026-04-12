#!/usr/bin/env bash
# ============================================================
# PostgreSQL 安装与初始化脚本（阿里云 CentOS/Alibaba Cloud Linux）
# ============================================================
# 用法：
#   chmod +x setup_postgresql.sh
#   sudo bash setup_postgresql.sh
#
# 执行后你需要手动：
#   1. 记住脚本输出的数据库密码
#   2. 将密码填入 hustsystem_postgresql.service 的 DATABASE_URL
#   3. 运行数据迁移（如需保留 SQLite 数据）
#
# 注意：此脚本需要 root 权限。

set -e

echo "=== HUST 学术道德监督管理系统 - PostgreSQL 安装 ==="

# ------- 1. 安装 PostgreSQL -------
echo "[1/5] 安装 PostgreSQL..."
if command -v yum &>/dev/null; then
    # CentOS / Alibaba Cloud Linux
    yum install -y postgresql-server postgresql-contrib
elif command -v apt &>/dev/null; then
    # Ubuntu / Debian
    apt update && apt install -y postgresql postgresql-contrib
else
    echo "错误：不支持的发行版，请手动安装 PostgreSQL。"
    exit 1
fi

# ------- 2. 初始化数据库 -------
echo "[2/5] 初始化数据库集群..."
if [ ! -f /var/lib/pgsql/data/PG_VERSION ] && [ ! -d /var/lib/postgresql ]; then
    postgresql-setup --initdb 2>/dev/null || true
fi

# ------- 3. 启动服务 -------
echo "[3/5] 启动 PostgreSQL 服务..."
systemctl enable --now postgresql

# ------- 4. 修改认证方式 -------
echo "[4/5] 配置认证方式..."
PG_HBA=$(find /var/lib/pgsql /etc/postgresql -name "pg_hba.conf" 2>/dev/null | head -1)
if [ -z "$PG_HBA" ]; then
    echo "警告：未找到 pg_hba.conf，请手动配置。"
else
    # 备份原文件
    cp "$PG_HBA" "${PG_HBA}.bak.$(date +%Y%m%d%H%M%S)"
    # 将 local all all peer 改为 local all all md5（密码认证）
    sed -i 's/^local\s\+all\s\+all\s\+peer/local   all             all                                     md5/' "$PG_HBA"
    sed -i 's/^local\s\+all\s\+all\s\+ident/local   all             all                                     md5/' "$PG_HBA"
    systemctl restart postgresql
    echo "  已修改 $PG_HBA 为 md5 认证"
fi

# ------- 5. 创建数据库和用户 -------
echo "[5/5] 创建数据库用户和库..."

# 生成 16 位随机密码
DB_PASSWORD=$(openssl rand -base64 16 | tr -d '/+=' | head -c 16)

sudo -u postgres psql <<EOF
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hustsys') THEN
        CREATE ROLE hustsys WITH LOGIN PASSWORD '${DB_PASSWORD}';
    ELSE
        ALTER ROLE hustsys WITH PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;

SELECT 'CREATE DATABASE hustsystem OWNER hustsys'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hustsystem');
\gexec
EOF

echo ""
echo "============================================"
echo "  PostgreSQL 安装完成！"
echo "============================================"
echo ""
echo "  数据库用户: hustsys"
echo "  数据库密码: ${DB_PASSWORD}"
echo "  数据库名称: hustsystem"
echo ""
echo "  连接串（填入 systemd 服务文件 DATABASE_URL）："
echo "  postgresql+psycopg2://hustsys:${DB_PASSWORD}@localhost:5432/hustsystem"
echo ""
echo "  下一步操作："
echo "    1. pip install psycopg2-binary  # 安装 Python 驱动"
echo "    2. 将上面的连接串写入 hustsystem_postgresql.service"
echo "    3. 如需迁移 SQLite 数据：export DATABASE_URL=上面的连接串 && python migrate_sqlite_to_external.py"
echo "    4. 如不迁移：export DATABASE_URL=上面的连接串 && python init_db.py"
echo ""
echo "  ⚠️  请立即记录密码，此密码不会再次显示！"
echo "============================================"
