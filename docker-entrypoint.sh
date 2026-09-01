#!/bin/sh
set -e

if [ -z "${MAX_BOT_TOKEN:-}" ]; then
  echo "ERROR: MAX_BOT_TOKEN is required" >&2
  exit 1
fi

export MAX_API_BASE_URL="${MAX_API_BASE_URL:-https://platform-api2.max.ru}"
export MAX_MODE="${MAX_MODE:-long_polling}"

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DeepSeek: configured"
else
  echo "DeepSeek: not configured (set DEEPSEEK_API_KEY for AI answers)"
fi

echo "MAX mode: ${MAX_MODE}"
exec python3 max_bot_core.py
