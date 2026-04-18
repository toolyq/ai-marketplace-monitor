@echo off
setlocal

call "%~dp0run_amazon_common.bat"

if "%TELEGRAM_BOT_TOKEN%"=="" (
  echo [WARN] TELEGRAM_BOT_TOKEN is empty. Set env var before running.
)

if "%TELEGRAM_CHAT_ID%"=="" (
  echo [WARN] TELEGRAM_CHAT_ID is empty. Set env var before running.
)

"%PY%" bestbuy_laptop_monitor.py ^
  --cdp-url "%CDP_URL%" ^
  --query "laptop computer" ^
  --min-price 100 ^
  --max-price 2000 ^
  --interval 600 ^
  --filter-file "bestbuy_laptop_filters.json" ^
  --ai-base-url "%AI_BASE_URL%" ^
  --ai-api-key "%AI_API_KEY%" ^
  --ai-model "%AI_MODEL%" ^
  --ai-description "Gaming laptop or desktop PC with at least 16GB VRAM, powered by a high-end GPU such as 4060 TI， RTX 4080, RTX 4090, RTX 4070 Ti Super, RX 6800, RX 7800 XT, RX 7900 XT, or RX 7900 XTX." ^
  --ai-extra-prompt "Rate 1 if it is clearly an accessory or peripheral, not a computer. Prefer models with at least 16GB RAM." ^
  --min-rating 3 ^
  --telegram-bot-token "%TELEGRAM_BOT_TOKEN%" ^
  --telegram-chat-id "%TELEGRAM_CHAT_ID%" ^
  %*

endlocal
