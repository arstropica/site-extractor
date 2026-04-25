"""Extraction service routes — extract, preview, validate."""

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
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
    """Load page index from the API gateway's database via Redis cache or file."""
    # The API gateway stores a page index in the job directory
    index_path = Path(settings.DATA_DIR) / "jobs" / job_id / "page_index.json"
    if index_path.exists():
        return json.loads(index_path.read_text())

    # Fallback: scan the pages directory
    pages_dir = Path(settings.DATA_DIR) / "jobs" / job_id / "pages"
    if not pages_dir.exists():
        return []

    pages = []
    for f in sorted(pages_dir.iterdir()):
        if f.is_file() and f.suffix in (".html", ".htm"):
            pages.append({
                "url": f.stem.replace("_", "/"),
                "local_path": f"pages/{f.name}",
                "status": "downloaded",
            })
    return pages


async def _load_resources(redis_client, job_id: str) -> list:
    """Load resource index."""
    index_path = Path(settings.DATA_DIR) / "jobs" / job_id / "resource_index.json"
    if index_path.exists():
        return json.loads(index_path.read_text())

    assets_dir = Path(settings.DATA_DIR) / "jobs" / job_id / "assets"
    if not assets_dir.exists():
        return []

    resources = []
    for f in sorted(assets_dir.iterdir()):
        if f.is_file():
            resources.append({
                "filename": f.name,
                "local_path": f"assets/{f.name}",
                "size": f.stat().st_size,
                "url": "",
            })
    return resources


async def _publish(redis_client, job_id: str, event_type: str, data: dict):
    """Publish an event to Redis pub/sub."""
    event = {
        "type": event_type,
        "job_id": job_id,
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await redis_client.publish("extraction_events", json.dumps(event))
