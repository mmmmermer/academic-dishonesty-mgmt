#!/usr/bin/env bash
# SQLite 版一键部署脚本（Ubuntu/Debian）
# 用法：在项目根目录执行 bash deploy/deploy_sqlite.sh
# 或：cd /opt/academic-dishonesty-mgmt && bash deploy/deploy_sqlite.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== 华中科技大学学术道德监督管理系统 - SQLite 部署 ==="
echo "项目目录: $PROJECT_ROOT"

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "错误: 未找到 python3，请先安装: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "[1/4] 创建虚拟环境..."
    python3 -m venv venv
fi
source venv/bin/activate

# 安装依赖
echo "[2/4] 安装依赖..."
pip install -q -r requirements.txt

# 初始化数据库（不设置 DATABASE_URL 即使用 SQLite）
echo "[3/4] 初始化数据库..."
unset DATABASE_URL
python init_db.py

# 检查 systemd
echo "[4/4] 检查 systemd 服务..."
if systemctl list-units --type=service | grep -q hustsystem; then
    echo "服务已存在，正在重启..."
    sudo systemctl restart hustsystem
else
    echo "请手动创建 systemd 服务，参考 docs/云服务器部署-SQLite版.md 第三节"
fi

echo ""
echo "=== 部署完成 ==="
echo "测试运行: source venv/bin/activate && python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501"
echo "默认管理员: admin / 123456 （上线后务必修改）"
echo "详细说明: docs/云服务器部署-SQLite版.md"
