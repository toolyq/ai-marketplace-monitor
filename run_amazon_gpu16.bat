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
  --query "gaming laptop PC AI desktop" ^
  --min-price 100 ^
  --max-price 1600 ^
  --interval 600 ^
  --filter-file "amazon_laptop_filters.json" ^
  --ai-base-url "%AI_BASE_URL%" ^
  --ai-api-key "%AI_API_KEY%" ^
  --ai-model "%AI_MODEL%" ^
  --ai-description "Gaming laptop or desktop PC with at least 16GB VRAM, powered by a high-end GPU such as RTX 4080, RTX 4090, RTX 4070 Ti Super, RX 6800, RX 7800 XT, RX 7900 XT, or RX 7900 XTX." ^
  --ai-extra-prompt "Make sure the GPU VRAM >=16GB. Rate 1 if GPU model is unclear or insufficient. Desktop bundles with monitor are acceptable." ^
  --min-rating 3 ^
  --telegram-bot-token "%TELEGRAM_BOT_TOKEN%" ^
  --telegram-chat-id "%TELEGRAM_CHAT_ID%" ^
  %*

endlocal
