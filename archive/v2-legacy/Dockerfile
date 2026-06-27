FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src/ /app/src/
COPY config/ /app/config/
COPY scripts/ /app/scripts/

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

RUN mkdir -p /app/data

ENV BOT_MODE=paper
ENV INITIAL_BANKROLL_USD=25.00
ENV VERIFY_SSL=false
ENV CONFIG_DIR=/app/config
ENV PYTHONPATH=/app/src

EXPOSE 8080

CMD ["python", "scripts/daemon.py"]
