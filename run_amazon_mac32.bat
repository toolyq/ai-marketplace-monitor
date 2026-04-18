@echo off
setlocal

call "%~dp0run_amazon_common.bat"

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
  --ai-base-url "%AI_BASE_URL%" ^
  --ai-api-key "%AI_API_KEY%" ^
  --ai-model "%AI_MODEL%" ^
  --ai-description "Apple Mac computer (MacBook Pro, Mac Studio, Mac Mini, or iMac) with Apple Silicon chip (M1/M2/M3/M4 series) and at least 32GB unified memory." ^
  --ai-extra-prompt "Must be Apple Silicon (M1/M2/M3/M4). Intel Macs are NOT acceptable. Unified memory must be at least 32GB. Rate 1 if only 8GB or 16GB." ^
  --min-rating 3 ^
  --telegram-bot-token "%TELEGRAM_BOT_TOKEN%" ^
  --telegram-chat-id "%TELEGRAM_CHAT_ID%" ^
  %*

endlocal
