#!/usr/bin/env bash
# 启动多个 Streamlit 进程（阶段三多实例部署用）。
# 用法：在项目根目录执行 ./deploy/start_streamlit_workers.sh 或 bash deploy/start_streamlit_workers.sh
# 可通过环境变量 STREAMLIT_PORTS 指定端口列表，默认 "8501 8502 8503"。

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PORTS="${STREAMLIT_PORTS:-8501 8502 8503}"
for port in $PORTS; do
  echo "Starting Streamlit on port $port ..."
  streamlit run app.py --server.port="$port" --server.address=0.0.0.0 &
done
echo "Workers started. Use Nginx or another reverse proxy with session affinity."
wait
