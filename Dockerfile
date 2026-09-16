FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    EXTRA_CA_DIR=/usr/local/share/ca-certificates \
    MAX_API_BASE_URL=https://platform-api2.max.ru \
    MAX_MODE=long_polling \
    MAX_WEBHOOK_HOST=0.0.0.0 \
    MAX_WEBHOOK_PORT=8080 \
    MAX_WEBHOOK_PATH=/webhook

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        ca-certificates \
        openssl \
    && rm -rf /var/lib/apt/lists/*

COPY certs/*.crt /usr/local/share/ca-certificates/
COPY russian_trusted_root_ca.cer /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
COPY russian_trusted_sub_ca.cer /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt

RUN for f in /usr/local/share/ca-certificates/*.crt /usr/local/share/ca-certificates/*.cer; do \
        [ -f "$f" ] || continue; \
        openssl x509 -inform PEM -in "$f" -out "$f.tmp" && mv "$f.tmp" "${f%.*}.crt"; \
    done \
    && update-ca-certificates --fresh

WORKDIR /app

COPY max_bot_core.py library_service.py library_ai.py book_service.py ssl_util.py books_balashiha.json docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh \
    && useradd -r -u 10001 -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
