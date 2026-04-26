"""
Redis consumer — syncs scraper output to SQLite.

Consumes page/resource records from Redis lists pushed by the scraper
service and persists them to the SQLite database. Also watches for
scraper status events and updates job records accordingly.
"""

import asyncio
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class RedisConsumer:
    def __init__(self, redis_client, db):
        self.redis = redis_client
        self.db = db
        self._running = False
        self._task = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("Redis consumer started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Redis consumer stopped")

    async def _consume_loop(self):
        """Main loop: drain Redis lists and sync to SQLite."""
        cycles = 0
        while self._running:
            try:
                # Hard ceiling per cycle. Without this, a slow DB write or a
                # wedged Redis read could keep one drain from ever returning,
                # leaving subsequent jobs untouched.
                drained = await asyncio.wait_for(self._drain_all(), timeout=60.0)
                cycles += 1
                # Heartbeat every ~60s of idle so we can confirm liveness from logs
                if cycles % 30 == 0:
                    logger.info(f"Redis consumer alive (cycle {cycles}, drained_this_cycle={drained})")
                await asyncio.sleep(0.5 if drained else 2.0)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                logger.error("Redis consumer cycle exceeded 60s; aborting and retrying")
                await asyncio.sleep(2.0)
            except Exception as e:
                logger.error(f"Redis consumer error: {e!r}", exc_info=True)
                await asyncio.sleep(5.0)

    async def _scan_keys(self, pattern: str) -> list:
        """Use SCAN instead of KEYS so a key set with many entries doesn't
        block the Redis server. Returns a list of matching keys."""
        keys = []
        async for k in self.redis.scan_iter(match=pattern, count=200):
            keys.append(k)
        return keys

    async def _drain_all(self) -> bool:
        """Drain all known scraper lists. Returns True if anything was processed.

        Each per-job drain is isolated in its own try/except so one bad job
        (malformed record, transient DB error, etc.) cannot abort the entire
        cycle and starve other jobs."""
        drained = False

        for key in await self._scan_keys("scraper:pages:*"):
            job_id = key.split(":")[-1]
            try:
                count = await self._drain_pages(job_id)
                if count > 0:
                    drained = True
                    logger.info(f"Drained {count} page record(s) for job {job_id}")
            except Exception as e:
                logger.error(f"Failed draining pages for job {job_id}: {e!r}", exc_info=True)

        for key in await self._scan_keys("scraper:resources:*"):
            job_id = key.split(":")[-1]
            try:
                count = await self._drain_resources(job_id)
                if count > 0:
                    drained = True
                    logger.info(f"Drained {count} resource record(s) for job {job_id}")
            except Exception as e:
                logger.error(f"Failed draining resources for job {job_id}: {e!r}", exc_info=True)

        for key in await self._scan_keys("scraper:result:*"):
            job_id = key.split(":")[-1]
            try:
                # Only count as drained if the call actually did something —
                # otherwise an orphan result key would keep flipping
                # drained_this_cycle=True every cycle forever.
                synced = await self._sync_result(job_id)
                if synced:
                    drained = True
            except Exception as e:
                logger.error(f"Failed syncing result for job {job_id}: {e!r}", exc_info=True)

        return drained

    async def _drain_pages(self, job_id: str) -> int:
        """Drain page records from Redis list into SQLite.

        At-least-once semantics: on INSERT failure (DB lock, malformed
        record, etc.) we RPUSH the raw record back onto the queue so
        the next cycle can retry. INSERT OR IGNORE in add_scrape_page
        makes accidental duplicates a no-op. Without this, an LPOP'd
        record whose INSERT raised was silently dropped — invisible
        record loss between Redis and SQLite.
        """
        count = 0
        while True:
            raw = await self.redis.lpop(f"scraper:pages:{job_id}")
            if not raw:
                break
            try:
                page = json.loads(raw)
                await self.db.add_scrape_page(page)
                count += 1
            except Exception as e:
                logger.error(
                    f"add_scrape_page failed for job {job_id} ({type(e).__name__}: {e}); "
                    "requeuing for retry"
                )
                try:
                    await self.redis.rpush(f"scraper:pages:{job_id}", raw)
                except Exception as ex2:
                    logger.error(
                        f"Requeue also failed for job {job_id}: {ex2!r}; record lost: {raw[:200]}"
                    )
                # Stop draining this list this cycle to avoid hot-looping
                # on a persistent error; next cycle starts fresh.
                break

        if count > 0:
            # Update job counters
            total_pages = await self.db.count_scrape_pages(job_id, status="downloaded")
            total_discovered = await self.db.count_scrape_pages(job_id)
            await self.db.update_job(job_id, {
                "pages_downloaded": total_pages,
                "pages_discovered": total_discovered,
            })

        return count

    async def _drain_resources(self, job_id: str) -> int:
        """Drain resource records from Redis list into SQLite.

        Same at-least-once semantics as _drain_pages — see that
        docstring for rationale.
        """
        count = 0
        while True:
            raw = await self.redis.lpop(f"scraper:resources:{job_id}")
            if not raw:
                break
            try:
                resource = json.loads(raw)
                await self.db.add_scrape_resource(resource)
                count += 1
            except Exception as e:
                logger.error(
                    f"add_scrape_resource failed for job {job_id} ({type(e).__name__}: {e}); "
                    "requeuing for retry"
                )
                try:
                    await self.redis.rpush(f"scraper:resources:{job_id}", raw)
                except Exception as ex2:
                    logger.error(
                        f"Requeue also failed for job {job_id}: {ex2!r}; record lost: {raw[:200]}"
                    )
                break

        if count > 0:
            # Update job counters
            total_resources = await self.db.count_scrape_resources(job_id)
            await self.db.update_job(job_id, {
                "resources_downloaded": total_resources,
                "resources_discovered": total_resources,
            })

        return count

    async def _sync_result(self, job_id: str) -> bool:
        """Sync scraper completion result to job record. Returns True if any
        meaningful work was done (key removed and/or DB updated)."""
        result = await self.redis.hgetall(f"scraper:result:{job_id}")
        if not result:
            return False

        job = await self.db.get_job(job_id)
        if not job:
            # Orphan: result hash for a job that no longer exists in the DB
            # (deleted while still scraping, manual wipe, etc.). Without this
            # delete, the key sits in Redis forever and the loop keeps trying.
            await self.redis.delete(f"scraper:result:{job_id}")
            logger.info(f"Removed orphan result key for missing job {job_id}")
            return True

        # Only update if the job is still in a scraping state
        if job["status"] not in ("scraping", "paused"):
            # Clean up the result key
            await self.redis.delete(f"scraper:result:{job_id}")
            return True

        updates = {
            "status": "scraped",
            "scraped_at": datetime.utcnow().isoformat(),
            "pages_discovered": int(result.get("pages_discovered", 0)),
            "pages_downloaded": int(result.get("pages_downloaded", 0)),
            "resources_discovered": int(result.get("resources_discovered", 0)),
            "resources_downloaded": int(result.get("resources_downloaded", 0)),
            "bytes_downloaded": int(result.get("bytes_downloaded", 0)),
        }
        await self.db.update_job(job_id, updates)

        # Clean up
        await self.redis.delete(f"scraper:result:{job_id}")
        logger.info(
            f"Job {job_id} marked as scraped: "
            f"{updates['pages_downloaded']} pages, {updates['resources_downloaded']} resources"
        )
        return True
