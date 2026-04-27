"""
Mark orphaned crawls as paused on startup.

When the API gateway restarts, any jobs in `scraping` status were
running in the scraper service's in-memory `active_crawls` map, which
is now empty. Rather than silently re-firing those crawls (which can
duplicate downloads, reset rate-limit budgets, and surprise the user
with unexpected activity after a deploy), we mark them as `paused`
with an explanatory error_message and let the user decide: hit Resume
in the wizard to pick up where the crawl left off, or Cancel to drop
it.
"""

import asyncio
import logging

from ..database import db
from shared.state_machine import IllegalTransition

logger = logging.getLogger(__name__)


async def resume_orphaned_jobs():
    """Mark any jobs left in 'scraping' status as paused with a note.

    Resumable state (crawl_state.json) is preserved on disk, so the
    user's Resume click goes through the normal start_scrape path
    with resume=true and continues from the saved queue + visited set.
    """
    # Wait briefly so the rest of the lifespan has settled
    await asyncio.sleep(3)

    candidates = await db.list_jobs(status="scraping", limit=200)
    if not candidates:
        return

    logger.info(
        f"Startup: found {len(candidates)} orphaned 'scraping' job(s); "
        "marking paused for user-initiated resume"
    )

    for job in candidates:
        job_id = job["id"]
        try:
            await db.update_status(job_id, "paused", extras={
                "error_message": (
                    "Service restarted while this scrape was running. "
                    "Resume to continue from where it left off, or cancel."
                ),
            })
        except IllegalTransition as e:
            logger.warning(f"Cannot mark orphan {job_id} paused: {e}")
        except Exception as e:
            logger.error(f"Failed to mark orphaned job {job_id} as paused: {e!r}")
