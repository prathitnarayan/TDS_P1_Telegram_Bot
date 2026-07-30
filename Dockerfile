# Base = the Telegram Bot API server (Alpine, binary at /usr/local/bin/telegram-bot-api).
# We add Python on top and run BOTH in one container: the API server on
# localhost:8081, and the bot on the public port 7860 talking to it via localhost.
FROM aiogram/telegram-bot-api:latest

USER root
RUN apk add --no-cache python3 py3-pip

# HF Spaces run the container as uid 1000
RUN adduser -D -u 1000 user 2>/dev/null || true
WORKDIR /home/user/app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .
RUN mkdir -p /tmp/tbap && chown -R user /home/user /tmp/tbap

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    TELEGRAM_API_BASE=http://localhost:8081 \
    PORT=7860
EXPOSE 7860
# Reset the base image's entrypoint (it chowns /var/lib as root and fails on
# HF's uid 1000) so our start.sh runs directly.
ENTRYPOINT []
CMD ["sh", "start.sh"]