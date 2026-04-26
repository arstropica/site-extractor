"""Scraper service configuration."""

import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    DATA_DIR: str = os.getenv("DATA_DIR", "/data")
    # Gateway URL — used for posting page/resource records and final-state
    # updates to the gateway's /api/internal/* endpoints. The gateway is the
    # sole owner of SQLite; the scraper is an HTTP producer.
    GATEWAY_URL: str = os.getenv("GATEWAY_URL", "http://api-gateway:8000")
    MAX_DOWNLOAD_SIZE: int = int(os.getenv("MAX_DOWNLOAD_SIZE", "524288000"))
    HTTP_PROXY: str = os.getenv("HTTP_PROXY", "")
    HTTPS_PROXY: str = os.getenv("HTTPS_PROXY", "")
    DEFAULT_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # Default retry attempts for transient failures (5xx, timeout). Per-job override available.
    SCRAPER_RETRY_LIMIT: int = int(os.getenv("SCRAPER_RETRY_LIMIT", "1"))
    SCRAPER_RETRY_BACKOFF_MS: int = int(os.getenv("SCRAPER_RETRY_BACKOFF_MS", "2000"))
    # Browser context pool size — number of concurrent Playwright contexts per browser
    BROWSER_POOL_SIZE: int = int(os.getenv("BROWSER_POOL_SIZE", "5"))
    # Per-asset size limit (skip files larger than this without downloading)
    MAX_ASSET_SIZE: int = int(os.getenv("MAX_ASSET_SIZE", str(50 * 1024 * 1024)))  # 50 MB default


settings = Settings()
