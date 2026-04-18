@echo off
setlocal

set "PY=d:\git\ai-marketplace-monitor\.venv\Scripts\python.exe"
set "CDP_URL=http://127.0.0.1:9222"

set "TELEGRAM_BOT_TOKEN="
set "TELEGRAM_CHAT_ID="

if "%TELEGRAM_BOT_TOKEN%"=="" (
  echo [WARN] TELEGRAM_BOT_TOKEN is empty. Set env var before running.
)

if "%TELEGRAM_CHAT_ID%"=="" (
  echo [WARN] TELEGRAM_CHAT_ID is empty. Set env var before running.
)

"%PY%" amazon_laptop_monitor.py ^
  --cdp-url "%CDP_URL%" ^
  --query "macbook pro mac mini studio" ^
  --min-price 100 ^
  --max-price 1600 ^
  --interval 600 ^
  --filter-file "amazon_mac_32gb_filters.json" ^
  --telegram-bot-token "%TELEGRAM_BOT_TOKEN%" ^
  --telegram-chat-id "%TELEGRAM_CHAT_ID%" ^
  %*

endlocal
