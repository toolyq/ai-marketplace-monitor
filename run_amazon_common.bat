@echo off
rem ============================================================
rem  Shared settings for all Amazon monitor launchers.
rem  Do NOT run this file directly — call it from a task bat.
rem ============================================================

set "PY=d:\git\ai-marketplace-monitor\.venv\Scripts\python.exe"
set "CDP_URL=http://127.0.0.1:9222"

set "TELEGRAM_BOT_TOKEN="
set "TELEGRAM_CHAT_ID="

set "AI_BASE_URL=https://integrate.api.nvidia.com/v1"
set "AI_API_KEY="
set "AI_MODEL=mistralai/mistral-small-4-119b-2603"
