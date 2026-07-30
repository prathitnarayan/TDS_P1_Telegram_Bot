#!/bin/sh
set -e

echo "[start] launching Telegram Bot API server on localhost:8081"
telegram-bot-api \
  --api-id="$TELEGRAM_API_ID" \
  --api-hash="$TELEGRAM_API_HASH" \
  --http-port=8081 \
  --dir=/tmp/tbap \
  --temp-dir=/tmp/tbap &

echo "[start] waiting for the API server to come up..."
sleep 4

echo "[start] launching bot on :7860 (Telegram base -> $TELEGRAM_API_BASE)"
exec uvicorn bot:app --host 0.0.0.0 --port 7860