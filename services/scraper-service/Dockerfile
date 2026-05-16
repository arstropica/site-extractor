# ── Scraper Service with Playwright ───────────────────────────────────────────
FROM python:3.11-slim

# Install system dependencies for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdrm2 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libxshmfence1 \
    libx11-xcb1 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY services/scraper-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install patchright's patched Chromium browser. patchright ships a separate
# CLI; the binary itself lives in the same Playwright cache dir, so any future
# code that imports `playwright.async_api` would still find a browser — we use
# patchright in crawler.py for the stealth patches it bundles.
RUN patchright install chromium

# Copy application code
COPY services/scraper-service/app/ ./app/
COPY services/shared/ ./shared/

# Create data directories
RUN mkdir -p /data

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8001/scraper/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
