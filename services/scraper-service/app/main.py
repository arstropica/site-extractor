"""
Site Extractor Scraper Service

Handles HTTP and browser-based web crawling with real-time progress
updates via Redis pub/sub.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis

from .config import settings
from .routes import scraper


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    app.state.active_crawls = {}  # job_id -> CrawlTask
    yield
    # Shutdown — cancel active crawls
    for job_id, task in app.state.active_crawls.items():
        task.cancel()
    await app.state.redis.close()


app = FastAPI(
    title="Site Extractor Scraper",
    version="0.1.0",
    description="Web crawling engine with HTTP and browser modes",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scraper.router, prefix="/scraper", tags=["Scraper"])
