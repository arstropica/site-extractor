"""Scraper service routes — start, pause, cancel, status."""

import asyncio
from fastapi import APIRouter, Request, HTTPException

from ..scraper.crawler import Crawler, CrawlState

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "healthy"}


@router.post("/start")
async def start_crawl(request: Request):
    body = await request.json()
    job_id = body.get("job_id")
    config = body.get("scrape_config", {})

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    # Check if already running
    if job_id in request.app.state.active_crawls:
        raise HTTPException(status_code=409, detail="Crawl already running for this job")

    crawler = Crawler(redis_client=request.app.state.redis)

    async def run_crawl():
        try:
            await crawler.crawl(job_id, config)
        except Exception as e:
            import json
            await request.app.state.redis.publish("scraper_events", json.dumps({
                "type": "SCRAPE_STATUS",
                "job_id": job_id,
                "data": {"status": "failed", "error": str(e)},
            }))
        finally:
            request.app.state.active_crawls.pop(job_id, None)

    task = asyncio.create_task(run_crawl())
    request.app.state.active_crawls[job_id] = task

    return {"job_id": job_id, "status": "started"}


@router.post("/pause/{job_id}")
async def pause_crawl(job_id: str, request: Request):
    task = request.app.state.active_crawls.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="No active crawl for this job")

    # Signal pause via Redis
    await request.app.state.redis.set(f"scraper:signal:{job_id}", "pause", ex=300)
    return {"job_id": job_id, "status": "pause_requested"}


@router.post("/cancel/{job_id}")
async def cancel_crawl(job_id: str, request: Request):
    # Signal cancel via Redis so the crawler loop picks it up gracefully
    await request.app.state.redis.set(f"scraper:signal:{job_id}", "cancel", ex=300)
    # Also force-cancel if the task is stuck
    task = request.app.state.active_crawls.get(job_id)
    if task:
        # Give the crawler 5 seconds to pick up the signal before force-cancelling
        await asyncio.sleep(0.1)
        if not task.done():
            task.cancel()
        request.app.state.active_crawls.pop(job_id, None)
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/status/{job_id}")
async def crawl_status(job_id: str, request: Request):
    is_active = job_id in request.app.state.active_crawls
    result = await request.app.state.redis.hgetall(f"scraper:result:{job_id}")
    return {
        "job_id": job_id,
        "active": is_active,
        "result": result or None,
    }
