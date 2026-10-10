#!/usr/bin/env bash
# botkit 配置页面一键启动（macOS / Linux）
#
#   chmod +x start-ui.sh
#   ./start-ui.sh
#
# 具体的检查和启动逻辑都在 start_ui.py 里，和 Windows 的 start.bat 共用一套，
# 免得两边行为漂移。

set -euo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  exec python3 start_ui.py
elif command -v python >/dev/null 2>&1; then
  exec python start_ui.py
else
  echo "[x] 没有找到 python3。请先安装 Python 3.11 或更高版本。"
  exit 1
fi
