#!/usr/bin/env bash
set -euo pipefail

# 切换到脚本所在目录
cd "$(dirname "$0")"

# 1. 拉取最新代码
# echo ">>> 拉取最新代码..."
# git pull

# 2. 安装/升级 uv（如果未安装）
if ! command -v uv &>/dev/null; then
    echo ">>> 安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 3. 同步依赖
# echo ">>> 同步依赖..."
# uv sync

# 4. 安装 playwright 浏览器（首次或更新后可能需要）
# echo ">>> 确认 playwright 浏览器已安装..."
# uv run playwright install chromium

# 5. Telegram 凭据（config.toml 中的占位符会读取这些环境变量）
export TELEGRAM_BOT_TOKEN="***"
export TELEGRAM_CHAT_ID="***"

# 6. 检查并重启 Chromium（远程调试模式）
if pgrep -x "chromium" &>/dev/null; then
    echo ">>> 检测到 Chromium 进程，正在终止..."
    pkill -x "chromium"
    sleep 1
fi
echo ">>> 启动 Chromium（远程调试模式）..."
chromium --remote-debugging-port=9222 --remote-allow-origins=* --enable-features=WebUIDarkMode &

# 8. 运行监控程序
echo ">>> 启动监控..."
uv run python monitor.py -v "$@"
