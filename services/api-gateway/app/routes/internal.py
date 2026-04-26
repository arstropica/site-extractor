"""Internal write endpoints used by sibling services (scraper, extraction).

The gateway is the sole owner of the SQLite database; worker services
never touch it directly. They POST records here and we INSERT.

The /api/internal/* prefix is a naming convention — the host port
forward (12000 → 8000) exposes the entire FastAPI app, so these are
reachable from outside the docker network in principle. They're safe
to expose because they're idempotent (INSERT OR IGNORE) and don't
leak data, but the convention says: clients of the public API don't
hit /internal. If we ever need real isolation, easy to add a shared
header secret or split the FastAPI app onto a second port.
"""

import logging
from datetime import datetime
from fastapi import APIRouter, Request

from ..database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/pages")
async def add_pages(request: Request):
    """Bulk-insert page records produced by the scraper.

    Returns how many were inserted. Counter recompute happens once
    per call (not per record), keyed on whatever job_id the records
    belong to. Mixing job_ids in one batch is allowed but inefficient.
    """
    body = await request.json()
    pages = body.get("pages", [])
    inserted = 0
    job_ids = set()
    for p in pages:
        try:
            await db.add_scrape_page(p)
            inserted += 1
            if p.get("job_id"):
                job_ids.add(p["job_id"])
        except Exception as e:
            # Log but don't fail the whole batch — record skipped, others proceed
            logger.error(f"add_scrape_page failed for {p.get('id')}: {e!r}")

    # Counter recompute per job touched
    for jid in job_ids:
        total_dl = await db.count_scrape_pages(jid, status="downloaded")
        total_disc = await db.count_scrape_pages(jid)
        await db.update_job(jid, {
            "pages_downloaded": total_dl,
            "pages_discovered": total_disc,
        })
    return {"inserted": inserted}


@router.post("/resources")
async def add_resources(request: Request):
    """Bulk-insert resource records produced by the scraper."""
    body = await request.json()
    resources = body.get("resources", [])
    inserted = 0
    job_ids = set()
    for r in resources:
        try:
            await db.add_scrape_resource(r)
            inserted += 1
            if r.get("job_id"):
                job_ids.add(r["job_id"])
        except Exception as e:
            logger.error(f"add_scrape_resource failed for {r.get('id')}: {e!r}")

    for jid in job_ids:
        total = await db.count_scrape_resources(jid)
        await db.update_job(jid, {
            "resources_downloaded": total,
            "resources_discovered": total,
        })
    return {"inserted": inserted}


@router.post("/jobs/{job_id}/scraped")
async def mark_scraped(job_id: str, request: Request):
    """Mark a job as scraped with final counters.

    Only transitions when current status is one of (scraping, paused);
    otherwise no-op. Replaces the old scraper:result:* Redis hash +
    consumer _sync_result dance with a direct UPDATE.
    """
    body = await request.json()
    job = await db.get_job(job_id)
    if not job:
        return {"updated": False, "reason": "job not found"}
    if job["status"] not in ("scraping", "paused"):
        return {"updated": False, "reason": f"status={job['status']}"}
    await db.update_job(job_id, {
        "status": "scraped",
        "scraped_at": datetime.utcnow().isoformat(),
        "pages_discovered": int(body.get("pages_discovered", 0)),
        "pages_downloaded": int(body.get("pages_downloaded", 0)),
        "resources_discovered": int(body.get("resources_discovered", 0)),
        "resources_downloaded": int(body.get("resources_downloaded", 0)),
        "bytes_downloaded": int(body.get("bytes_downloaded", 0)),
    })
    return {"updated": True}
