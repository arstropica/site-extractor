"""API Gateway configuration."""

import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/data/extractor.db")
    DATA_DIR: str = os.getenv("DATA_DIR", "/data")
    SCRAPER_SERVICE_URL: str = os.getenv("SCRAPER_SERVICE_URL", "http://scraper-service:8001")
    EXTRACTION_SERVICE_URL: str = os.getenv("EXTRACTION_SERVICE_URL", "http://extraction-service:8002")
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "change-me-in-production")
    MAX_DOWNLOAD_SIZE: int = int(os.getenv("MAX_DOWNLOAD_SIZE", "524288000"))


settings = Settings()
