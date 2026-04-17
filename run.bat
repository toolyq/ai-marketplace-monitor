@echo off
setlocal enableextensions

rem Always run from repository root (the directory containing this script).
cd /d "%~dp0"

rem Activate project virtual environment.
if not exist ".venv\Scripts\activate.bat" (
	echo [ERROR] Missing virtual environment at .venv\Scripts\activate.bat
	echo         Create it first, then re-run this script.
	pause
	exit /b 1
)
call ".venv\Scripts\activate.bat"

rem Telegram credentials expected by config placeholders.
if not defined TELEGRAM_BOT_TOKEN set "TELEGRAM_BOT_TOKEN=8674991709:****************************"
if not defined TELEGRAM_CHAT_ID set "TELEGRAM_CHAT_ID=***********"

rem Ensure local package source is importable.
if defined PYTHONPATH (
	set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
) else (
	set "PYTHONPATH=%CD%\src"
)

@REM rem Start Chrome with CDP endpoint required by monitor.cdp_url (127.0.0.1:9222).
@REM set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
@REM if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"

@REM if exist "%CHROME_EXE%" (
@REM 	start "Chrome CDP" "%CHROME_EXE%" --remote-debugging-port=9222 --new-window "https://www.facebook.com/"
@REM ) else (
@REM 	echo [WARN] Chrome executable not found in default location. Trying PATH...
@REM 	start "Chrome CDP" chrome --remote-debugging-port=9222 --new-window "https://www.facebook.com/"
@REM )

@REM rem Give browser a brief moment to open the debug endpoint.
@REM timeout /t 2 /nobreak >nul

rem Run monitor from installed console command when available, otherwise module fallback.
where ai-marketplace-monitor >nul 2>&1
if %errorlevel%==0 (
	ai-marketplace-monitor
) else (
	python -m ai_marketplace_monitor.cli
)

set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
	echo.
	echo [ERROR] ai-marketplace-monitor exited with code %EXIT_CODE%.
	pause
)

endlocal & exit /b %EXIT_CODE%