# ── Extraction Service ─────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/extraction-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/extraction-service/app/ ./app/
COPY services/shared/ ./shared/

RUN mkdir -p /data

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8002/extraction/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
