cd /d "%~dp0"

call ".venv\Scripts\activate.bat"

rem Telegram credentials expected by config placeholders.
set "TELEGRAM_BOT_TOKEN=***"
set "TELEGRAM_CHAT_ID=***"

python monitor.py -v %*


cmd /k