# ── Stage 1: Build UI ──────────────────────────────────────────────────────────
FROM node:20-alpine AS ui-build

WORKDIR /app
COPY services/ui/package*.json ./
RUN npm ci --cache /tmp/npm-cache
COPY services/ui/ .
RUN npm run build

# ── Stage 2: API Gateway + static UI ──────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY services/extractor-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY services/extractor-gateway/app/ ./app/
COPY services/shared/ ./shared/

# Copy UI build output
COPY --from=ui-build /app/dist ./static

# Create data directories
RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
