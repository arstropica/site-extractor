"""
Site Extractor Extraction Service

Handles CSS selector execution, boundary-scoped extraction,
and file pattern matching against scraped content.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis

from .config import settings
from .routes import extraction


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    yield
    await app.state.redis.close()


app = FastAPI(
    title="Site Extractor Extraction Service",
    version="0.1.0",
    description="CSS selector execution and content extraction engine",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extraction.router, prefix="/extraction", tags=["Extraction"])
