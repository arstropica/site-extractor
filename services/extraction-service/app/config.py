"""Extraction service configuration."""

import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    DATA_DIR: str = os.getenv("DATA_DIR", "/data")


settings = Settings()
