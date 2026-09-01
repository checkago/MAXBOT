FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    MAX_API_BASE_URL=https://platform-api2.max.ru \
    MAX_MODE=long_polling \
    MAX_WEBHOOK_HOST=0.0.0.0 \
    MAX_WEBHOOK_PORT=8080 \
    MAX_WEBHOOK_PATH=/webhook

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY max_bot_core.py library_service.py library_ai.py docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh \
    && useradd -r -u 10001 -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
