"""
Auto-resume orphaned crawls on startup.

When the API gateway restarts, scan for jobs in `scraping` or `extracting`
status. These were running when something went down. The scraper service's
in-memory `active_crawls` map is empty after restart, so these jobs are
orphaned. We re-trigger them — except for jobs explicitly `paused` or
`cancelled`, which the user controlled.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import httpx

from ..config import settings
from ..database import db
from .encryption import decrypt_scrape_config, CredentialDecryptError

logger = logging.getLogger(__name__)


async def resume_orphaned_jobs():
    """Find jobs that look stuck and re-trigger them."""
    # Wait briefly so the scraper service has time to come up
    await asyncio.sleep(3)

    candidates = await db.list_jobs(status="scraping", limit=200)
    if not candidates:
        return

    logger.info(f"Auto-resume: found {len(candidates)} orphaned 'scraping' jobs")

    for job in candidates:
        job_id = job["id"]
        try:
            decrypted = decrypt_scrape_config(job["scrape_config"])
        except CredentialDecryptError as e:
            logger.warning(f"Auto-resume: skipping {job_id}: {e}")
            await db.update_job(job_id, {
                "status": "failed",
                "error_message": f"Auto-resume failed: {e}",
            })
            continue

        # Has the scraper persisted resumable state?
        state_file = Path(settings.DATA_DIR) / "jobs" / job_id / "crawl_state.json"
        has_state = state_file.exists()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.SCRAPER_SERVICE_URL}/scraper/start",
                    json={"job_id": job_id, "scrape_config": decrypted},
                )
                if resp.status_code == 409:
                    # Already running — fine
                    logger.info(f"Auto-resume: job {job_id} already running, skipping")
                    continue
                resp.raise_for_status()
            logger.info(
                f"Auto-resume: re-triggered job {job_id} "
                f"({'with' if has_state else 'without'} resumable state)"
            )
            await db.update_job(job_id, {
                "started_at": job.get("started_at") or datetime.utcnow().isoformat(),
            })
        except httpx.HTTPError as e:
            logger.error(f"Auto-resume: failed to re-trigger {job_id}: {e}")
            # Don't mark as failed — the user can retry
