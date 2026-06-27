FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ARCHITECTURE.md /app/
COPY src/ /app/src/
COPY config/ /app/config/
COPY scripts/ /app/scripts/

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

RUN mkdir -p /app/data

ENV BOT_MODE=paper
ENV INITIAL_BANKROLL_USD=25.00
ENV CONFIG_DIR=/app/config
ENV PYTHONPATH=/app/src
ENV LOG_FORMAT=json
ENV HTTP_HOST=0.0.0.0
ENV HTTP_PORT=8082

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8082/api/health || exit 1

CMD ["python", "scripts/daemon.py"]
