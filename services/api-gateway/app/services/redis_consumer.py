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
                await self._sync_result(job_id)
                drained = True
            except Exception as e:
                logger.error(f"Failed syncing result for job {job_id}: {e!r}", exc_info=True)

        return drained

    async def _drain_pages(self, job_id: str) -> int:
        """Drain page records from Redis list into SQLite."""
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
                logger.warning(f"Failed to persist page record: {e}")

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
        """Drain resource records from Redis list into SQLite."""
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
                logger.warning(f"Failed to persist resource record: {e}")

        if count > 0:
            # Update job counters
            total_resources = await self.db.count_scrape_resources(job_id)
            await self.db.update_job(job_id, {
                "resources_downloaded": total_resources,
                "resources_discovered": total_resources,
            })

        return count

    async def _sync_result(self, job_id: str) -> None:
        """Sync scraper completion result to job record."""
        result = await self.redis.hgetall(f"scraper:result:{job_id}")
        if not result:
            return

        job = await self.db.get_job(job_id)
        if not job:
            return

        # Only update if the job is still in a scraping state
        if job["status"] not in ("scraping", "paused"):
            # Clean up the result key
            await self.redis.delete(f"scraper:result:{job_id}")
            return

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
