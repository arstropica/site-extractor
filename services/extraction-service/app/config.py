"""Extraction service configuration."""

import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    DATA_DIR: str = os.getenv("DATA_DIR", "/data")
    # Gateway URL — used to fetch the authoritative page / resource list
    # from SQLite. Disk holds the blobs records reference; the gateway
    # holds the records.
    GATEWAY_URL: str = os.getenv("GATEWAY_URL", "http://api-gateway:8000")


settings = Settings()
