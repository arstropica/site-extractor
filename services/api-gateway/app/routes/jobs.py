"""Job management routes."""

import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Query
import httpx

from ..database import db
from ..config import settings
from ..services.websocket import ws_manager
from ..services.encryption import (
    encrypt_scrape_config,
    decrypt_scrape_config,
    CredentialDecryptError,
)

router = APIRouter()


@router.post("")
async def create_job(request: Request):
    body = await request.json()
    scrape_config = body.get("scrape_config", {})
    name = (body.get("name") or "").strip() or None

    if not scrape_config.get("seed_urls"):
        raise HTTPException(status_code=400, detail="At least one seed URL is required")

    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Auto-populate domain filter defaults from seed URLs
    domain_filter = scrape_config.get("domain_filter", {})
    if not domain_filter.get("allowed_domains"):
        from urllib.parse import urlparse
        domains = list(set(urlparse(u).netloc for u in scrape_config["seed_urls"] if urlparse(u).netloc))
        domain_filter["allowed_domains"] = domains
        scrape_config["domain_filter"] = domain_filter

    # Create job data directory
    job_dir = Path(settings.DATA_DIR) / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "pages").mkdir(exist_ok=True)
    (job_dir / "assets").mkdir(exist_ok=True)
    (job_dir / "results").mkdir(exist_ok=True)

    # Encrypt sensitive auth fields before persisting
    encrypted_config = encrypt_scrape_config(scrape_config)

    await db.create_job({
        "id": job_id,
        "name": name,
        "status": "created",
        "scrape_config": encrypted_config,
        "created_at": now,
    })

    return {"job_id": job_id, "status": "created", "message": "Job created"}


@router.get("")
async def list_jobs(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    jobs = await db.list_jobs(
        status=status, search=search, date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )
    count = await db.count_jobs(
        status=status, search=search, date_from=date_from, date_to=date_to,
    )

    items = []
    for job in jobs:
        config = job.get("scrape_config", {})
        seed_urls = config.get("seed_urls", [])

        duration = None
        if job.get("completed_at") and job.get("created_at"):
            try:
                t_end = datetime.fromisoformat(job["completed_at"])
                t_start = datetime.fromisoformat(job["created_at"])
                duration = (t_end - t_start).total_seconds()
            except (ValueError, TypeError):
                pass

        items.append({
            "id": job["id"],
            "name": job.get("name"),
            "status": job["status"],
            "extraction_mode": job.get("extraction_mode"),
            "seed_urls": seed_urls,
            "pages_downloaded": job.get("pages_downloaded", 0),
            "resources_downloaded": job.get("resources_downloaded", 0),
            "created_at": job["created_at"],
            "completed_at": job.get("completed_at"),
            "duration_seconds": duration,
        })

    return {"jobs": items, "count": count}


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Decrypt auth fields so the wizard can re-display them
    try:
        job["scrape_config"] = decrypt_scrape_config(job["scrape_config"])
    except CredentialDecryptError:
        # Don't fail the read; mark auth as broken so the user sees the issue
        if job.get("scrape_config", {}).get("auth"):
            job["scrape_config"]["auth"] = {
                "method": job["scrape_config"]["auth"].get("method", "none"),
                "_decrypt_error": "Stored credentials are unreadable. ENCRYPTION_KEY may have changed.",
            }
    return job


@router.patch("/{job_id}")
async def update_job(job_id: str, request: Request):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    body = await request.json()
    allowed_fields = {"name", "extraction_config", "extraction_mode", "status"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}
    if "name" in updates and updates["name"] is not None:
        updates["name"] = updates["name"].strip() or None
    if updates:
        await db.update_job(job_id, updates)

    return await db.get_job(job_id)


@router.post("/{job_id}/clone")
async def clone_job(job_id: str):
    """Clone an existing job's configuration into a new job (status='created')."""
    src = await db.get_job(job_id)
    if not src:
        raise HTTPException(status_code=404, detail="Job not found")

    new_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    new_name = f"{src.get('name') or f'Job {job_id[:8]}'} (copy)"

    # Create new directory structure for the clone
    job_dir = Path(settings.DATA_DIR) / "jobs" / new_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "pages").mkdir(exist_ok=True)
    (job_dir / "assets").mkdir(exist_ok=True)
    (job_dir / "results").mkdir(exist_ok=True)

    # The source's scrape_config is already encrypted at rest; we copy as-is.
    # Don't carry over runtime state (progress, timestamps, errors).
    await db.create_job({
        "id": new_id,
        "name": new_name,
        "status": "created",
        "scrape_config": src["scrape_config"],
        "extraction_config": src.get("extraction_config"),
        "extraction_mode": src.get("extraction_mode"),
        "created_at": now,
    })

    return {"job_id": new_id, "status": "created", "message": "Job cloned"}


@router.post("/{job_id}/start-scrape")
async def start_scrape(job_id: str, request: Request):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("created", "paused", "failed", "cancelled", "scraped", "completed"):
        raise HTTPException(status_code=400, detail=f"Cannot start scrape from status '{job['status']}'")

    # Resume only when the prior run was paused mid-flight. Every other
    # entry path (re-run after completion, retry after failure) treats this
    # as a fresh crawl and clears stale page/resource rows so they don't
    # accumulate; the scraper's per-URL HEAD freshness check still avoids
    # re-downloading unchanged content from disk.
    is_resume = job["status"] == "paused"
    if not is_resume:
        # Order matters: drop the Redis lists FIRST so the redis_consumer
        # can't drain stale records back into scrape_pages/scrape_resources
        # during the brief window between the DB wipe and the scraper's own
        # cleanup_for_fresh_run().
        await request.app.state.redis.delete(
            f"scraper:pages:{job_id}",
            f"scraper:resources:{job_id}",
            f"scraper:result:{job_id}",
            f"scraper:signal:{job_id}",
        )
        await db.clear_scrape_data(job_id)

    # Decrypt sensitive auth fields before forwarding (scraper sees plaintext only)
    try:
        decrypted_config = decrypt_scrape_config(job["scrape_config"])
    except CredentialDecryptError as e:
        await db.update_job(job_id, {"status": "failed", "error_message": str(e)})
        raise HTTPException(status_code=400, detail=str(e))

    # Forward to scraper service
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.SCRAPER_SERVICE_URL}/scraper/start",
                json={
                    "job_id": job_id,
                    "scrape_config": decrypted_config,
                    "resume": is_resume,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Scraper service error: {str(e)}")

    await db.update_job(job_id, {
        "status": "scraping",
        "started_at": datetime.utcnow().isoformat(),
    })

    await ws_manager.broadcast_event("SCRAPE_STATUS", job_id, {"status": "scraping"})

    return {"job_id": job_id, "status": "scraping", "message": "Scrape started", "resume": is_resume}


@router.post("/{job_id}/pause")
async def pause_job(job_id: str, request: Request):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "scraping":
        raise HTTPException(status_code=400, detail="Can only pause a running scrape")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{settings.SCRAPER_SERVICE_URL}/scraper/pause/{job_id}")
    except httpx.HTTPError:
        pass

    await db.update_job(job_id, {"status": "paused"})
    await ws_manager.broadcast_event("SCRAPE_STATUS", job_id, {"status": "paused"})
    return {"job_id": job_id, "status": "paused"}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("scraping", "paused", "extracting"):
        raise HTTPException(status_code=400, detail="Job is not running")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{settings.SCRAPER_SERVICE_URL}/scraper/cancel/{job_id}")
    except httpx.HTTPError:
        pass

    await db.update_job(job_id, {"status": "cancelled"})
    await ws_manager.broadcast_event("SCRAPE_STATUS", job_id, {"status": "cancelled"})
    return {"job_id": job_id, "status": "cancelled"}


@router.delete("/{job_id}")
async def delete_job(job_id: str, request: Request, delete_data: bool = Query(True)):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] in ("scraping", "extracting"):
        raise HTTPException(
            status_code=400,
            detail="Cancel the job before deleting it.",
        )

    # Always wipe transient Redis state and the on-disk crawl_state metadata —
    # the `delete_data` flag only governs the saved pages/assets/results.
    await request.app.state.redis.delete(
        f"scraper:pages:{job_id}",
        f"scraper:resources:{job_id}",
        f"scraper:result:{job_id}",
        f"scraper:signal:{job_id}",
    )

    job_dir = Path(settings.DATA_DIR) / "jobs" / job_id
    state_file = job_dir / "crawl_state.json"
    if state_file.exists():
        state_file.unlink(missing_ok=True)

    if delete_data:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)

    await db.delete_job(job_id, delete_results=delete_data)
    return {"message": "Job deleted", "data_deleted": delete_data}
