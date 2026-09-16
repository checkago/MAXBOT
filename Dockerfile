FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
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

COPY russian_trusted_root_ca.cer /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
COPY russian_trusted_sub_ca.cer /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt
RUN update-ca-certificates

WORKDIR /app

COPY max_bot_core.py library_service.py library_ai.py book_service.py books_balashiha.json docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh \
    && useradd -r -u 10001 -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
