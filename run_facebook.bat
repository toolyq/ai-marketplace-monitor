cd /d "%~dp0"

call ".venv\Scripts\activate.bat"

rem Telegram credentials expected by config placeholders.
call "%~dp0run_amazon_common.bat"

if "%TELEGRAM_BOT_TOKEN%"=="" (
  echo [WARN] TELEGRAM_BOT_TOKEN is empty. Set env var before running.
)

if "%TELEGRAM_CHAT_ID%"=="" (
  echo [WARN] TELEGRAM_CHAT_ID is empty. Set env var before running.
)


python monitor.py -v %*


cmd /k