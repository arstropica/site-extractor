"""Extraction service routes — extract, preview, validate."""

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, HTTPException

from ..config import settings
from ..extractor.engine import ExtractionEngine

logger = logging.getLogger(__name__)
router = APIRouter()

engine = ExtractionEngine(settings.DATA_DIR)


@router.get("/health")
async def health():
    return {"status": "healthy"}


@router.post("/extract")
async def extract(request: Request):
    """Run full extraction and persist results."""
    body = await request.json()
    job_id = body.get("job_id")
    extraction_config = body.get("extraction_config", {})
    schema_fields = body.get("schema_fields", [])

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    mode = extraction_config.get("mode", "document")
    redis_client = request.app.state.redis

    # Publish start event
    await _publish(redis_client, job_id, "EXTRACTION_STATUS", {"status": "extracting"})

    try:
        if mode == "document":
            pages = await _load_pages(redis_client, job_id)
            if not pages:
                raise HTTPException(status_code=400, detail="No scraped pages found")

            results = engine.extract_from_pages(
                job_id=job_id,
                pages=pages,
                schema_fields=schema_fields,
                config=extraction_config,
            )

            # Publish progress
            await _publish(redis_client, job_id, "EXTRACTION_PROGRESS", {
                "rows_extracted": len(results),
            })

        elif mode == "file":
            resources = await _load_resources(redis_client, job_id)
            file_patterns = extraction_config.get("file_patterns", [])
            categorized = engine.extract_file_patterns(job_id, resources, file_patterns)

            # Convert to row format for consistent storage
            results = [{"data": categorized}]

        else:
            raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

        # Persist results
        result_id = str(uuid.uuid4())
        result_path = Path(settings.DATA_DIR) / "jobs" / job_id / "results" / f"{result_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(results, indent=2))

        # Store result reference in Redis for the API gateway to pick up
        await redis_client.hset(f"extraction:result:{job_id}", mapping={
            "result_id": result_id,
            "row_count": str(len(results)),
            "result_path": str(result_path),
        })

        await _publish(redis_client, job_id, "EXTRACTION_STATUS", {
            "status": "completed",
            "rows_extracted": len(results),
            "result_id": result_id,
        })

        return {
            "job_id": job_id,
            "result_id": result_id,
            "rows_extracted": len(results),
            "status": "completed",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Extraction failed for job {job_id}: {e}", exc_info=True)
        await _publish(redis_client, job_id, "EXTRACTION_STATUS", {
            "status": "failed",
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
async def preview(request: Request):
    """Run extraction on a subset of pages for preview."""
    body = await request.json()
    job_id = body.get("job_id")
    extraction_config = body.get("extraction_config", {})
    schema_fields = body.get("schema_fields", [])
    limit = body.get("limit", 20)

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    mode = extraction_config.get("mode", "document")
    redis_client = request.app.state.redis

    try:
        if mode == "document":
            pages = await _load_pages(redis_client, job_id)
            results = engine.extract_from_pages(
                job_id=job_id,
                pages=pages,
                schema_fields=schema_fields,
                config=extraction_config,
                limit=limit,
            )
        elif mode == "file":
            resources = await _load_resources(redis_client, job_id)
            file_patterns = extraction_config.get("file_patterns", [])
            categorized = engine.extract_file_patterns(job_id, resources, file_patterns)
            results = [{"data": categorized}]
        else:
            results = []

        return {
            "job_id": job_id,
            "preview": results,
            "total_matched": len(results),
            "limit": limit,
        }

    except Exception as e:
        logger.error(f"Preview failed for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate-selector")
async def validate_selector(request: Request):
    """Validate a CSS selector against stored pages."""
    body = await request.json()
    job_id = body.get("job_id")
    selector = body.get("selector", "")
    limit = body.get("limit", 10)

    if not job_id or not selector:
        raise HTTPException(status_code=400, detail="job_id and selector are required")

    redis_client = request.app.state.redis
    pages = await _load_pages(redis_client, job_id)

    result = engine.validate_selector(job_id, selector, pages, limit)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_pages(redis_client, job_id: str) -> list:
    """Load the canonical page list from the gateway DB.

    SQLite (via the gateway) is the source of truth — the scraper writes
    each page record there as it captures it. Disk only holds the HTML
    blobs that records reference. We pull paginated until exhausted so
    large jobs don't truncate at the default page-size cap.
    """
    return await _fetch_paginated(f"/api/pages/{job_id}", "pages")


async def _load_resources(redis_client, job_id: str) -> list:
    """Load the canonical resource list from the gateway DB."""
    return await _fetch_paginated(f"/api/pages/{job_id}/resources", "resources")


async def _fetch_paginated(path: str, field: str, page_size: int = 1000) -> list:
    """GET /api/.../<list-endpoint> with offset/limit until exhausted.

    Retries each page on transient failures (timeout, connection drop,
    non-JSON response) up to a few times with backoff. The non-JSON case
    has been observed in production — under load the gateway has
    occasionally served the SPA index.html instead of the API route's
    JSON, which is plausibly route-resolution choosing the catchall by
    accident. Retry usually clears it; we still log enough detail on
    every failure to confirm whether that's what's happening.
    """
    out = []
    offset = 0
    base_url = settings.GATEWAY_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            chunk = await _fetch_one_page(client, f"{base_url}{path}", field, page_size, offset)
            out.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size
    return out


async def _fetch_one_page(client, url: str, field: str, page_size: int, offset: int) -> list:
    """Fetch one paginated chunk with retry-on-transient-failure."""
    import asyncio as _asyncio
    last_err = None
    for attempt in range(4):
        try:
            resp = await client.get(url, params={"limit": page_size, "offset": offset})
            resp.raise_for_status()
            payload = resp.json()
            return payload.get(field, [])
        except (httpx.HTTPError, httpx.TimeoutException, _asyncio.TimeoutError, ValueError) as e:
            last_err = e
            ct = ""
            body = ""
            if isinstance(e, ValueError):
                # JSON parse failed — log enough context to tell whether
                # it was the gateway misrouting (HTML body) or some other
                # corruption.
                try:
                    ct = resp.headers.get("content-type", "")
                    body = resp.text[:300]
                except Exception:
                    pass
            logger.warning(
                f"GET {url} offset={offset} attempt {attempt + 1}/4 failed: "
                f"{type(e).__name__}: {e} | content-type={ct!r} body={body!r}"
            )
            if attempt < 3:
                await _asyncio.sleep(0.5 * (2 ** attempt))
    raise HTTPException(
        status_code=502,
        detail=f"Gateway page-list fetch failed after 4 attempts: {type(last_err).__name__}: {last_err}",
    )


async def _publish(redis_client, job_id: str, event_type: str, data: dict):
    """Publish an event to Redis pub/sub."""
    event = {
        "type": event_type,
        "job_id": job_id,
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await redis_client.publish("extraction_events", json.dumps(event))
