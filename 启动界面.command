#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo '请先按 ENHANCED.md 安装 Python 环境与依赖。'
  read -r '?按回车退出'
  exit 1
fi
exec .venv/bin/python -m enhanced gui
