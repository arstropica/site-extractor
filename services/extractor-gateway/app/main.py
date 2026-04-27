"""
Site Extractor API Gateway

Central orchestrator for the site scraping and extraction pipeline.
Manages jobs, schemas, extraction, and real-time WebSocket updates.
"""

import asyncio
import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import redis.asyncio as redis

from .config import settings
from .database import db
from .routes import jobs, schemas, scraper, extraction, pages, system, internal
from .services.websocket import ws_manager
from .services.auto_resume import resume_orphaned_jobs
from .services.seed_templates import seed_templates
from shared.state_machine import IllegalTransition

# Without this, logger.info(...) calls from our app modules are silently
# dropped — uvicorn only configures its own loggers, not Python's root,
# so we have no visibility into application internals. Set INFO so
# request-level logs, errors, and warnings show up in container output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db.connect()
    # socket_timeout prevents an await on a half-open Redis connection from
    # hanging forever (which silently wedged the consumer in production —
    # pages stayed in the queue, no exception ever fired to trigger retry).
    # health_check_interval keeps connections fresh; retry_on_timeout makes
    # transient blips self-recover instead of needing a service restart.
    app.state.redis = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_timeout=10.0,
        socket_connect_timeout=5.0,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )
    app.state.ws_manager = ws_manager

    # Start Redis subscriber for scraper/extraction events → WebSocket relay
    app.state.redis_sub = redis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = app.state.redis_sub.pubsub()
    await pubsub.subscribe("scraper_events", "extraction_events")

    async def relay_events():
        """Relay Redis pub/sub events to WebSocket clients + update job status."""
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    await ws_manager.broadcast(event)

                    # Sync status changes to SQLite
                    event_type = event.get("type")
                    job_id = event.get("job_id")
                    data = event.get("data", {})

                    # Background event relay: if a transition is illegal
                    # (e.g., job already cancelled) we log and skip rather
                    # than blow up the relay loop. This is a recovery path
                    # for redelivered/late events, not a place to enforce
                    # caller correctness — the dedicated route handlers
                    # already enforced it on the way in.

                    # Persist mid-flight counter snapshots so a UI refresh
                    # or any API poll mid-scrape sees current progress
                    # rather than a stale 0 left over from job-create. Maps
                    # the scraper's *_total/*_done event shape onto the job
                    # row's *_discovered/*_downloaded columns.
                    if event_type == "SCRAPE_PROGRESS" and job_id:
                        progress_updates = {}
                        if isinstance(data.get("pages_total"), int):
                            progress_updates["pages_discovered"] = data["pages_total"]
                        if isinstance(data.get("pages_done"), int):
                            progress_updates["pages_downloaded"] = data["pages_done"]
                        if isinstance(data.get("pages_errored"), int):
                            progress_updates["pages_errored"] = data["pages_errored"]
                        if isinstance(data.get("resources_total"), int):
                            progress_updates["resources_discovered"] = data["resources_total"]
                        if isinstance(data.get("resources_done"), int):
                            progress_updates["resources_downloaded"] = data["resources_done"]
                        if isinstance(data.get("resources_errored"), int):
                            progress_updates["resources_errored"] = data["resources_errored"]
                        if isinstance(data.get("bytes_downloaded"), int):
                            progress_updates["bytes_downloaded"] = data["bytes_downloaded"]
                        if progress_updates:
                            try:
                                await db.update_job(job_id, progress_updates)
                            except Exception as e:
                                logger.debug(f"SCRAPE_PROGRESS persist skipped: {e}")

                    if event_type == "SCRAPE_STATUS" and job_id:
                        status = data.get("status")
                        try:
                            if status == "failed":
                                await db.update_status(job_id, "failed", extras={
                                    "error_message": data.get("error", "Unknown error"),
                                    "failed_stage": "scrape",
                                })
                            elif status == "paused":
                                await db.update_status(job_id, "paused")
                        except IllegalTransition as e:
                            logger.debug(f"Skipping illegal SCRAPE_STATUS event: {e}")

                    elif event_type == "EXTRACTION_STATUS" and job_id:
                        status = data.get("status")
                        try:
                            if status == "completed":
                                await db.update_status(job_id, "completed", extras={
                                    "completed_at": datetime.utcnow().isoformat(),
                                })
                            elif status == "failed":
                                await db.update_status(job_id, "failed", extras={
                                    "error_message": data.get("error", "Extraction failed"),
                                    "failed_stage": "extract",
                                })
                        except IllegalTransition as e:
                            logger.debug(f"Skipping illegal EXTRACTION_STATUS event: {e}")

                except (json.JSONDecodeError, Exception) as e:
                    logger.debug(f"Event relay error: {e}")
        except asyncio.CancelledError:
            pass

    app.state.relay_task = asyncio.create_task(relay_events())

    # Seed built-in schema templates (idempotent)
    await seed_templates()

    # Auto-resume orphaned scraping jobs (best-effort; runs in background)
    app.state.resume_task = asyncio.create_task(resume_orphaned_jobs())

    yield

    # Shutdown
    app.state.relay_task.cancel()
    if hasattr(app.state, "resume_task"):
        app.state.resume_task.cancel()
    await pubsub.unsubscribe("scraper_events", "extraction_events")
    await app.state.redis_sub.close()
    await db.close()
    await app.state.redis.close()


app = FastAPI(
    title="Site Extractor API",
    version="0.1.0",
    description="Site spider and content extraction pipeline",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(schemas.router, prefix="/api/schemas", tags=["Schemas"])
app.include_router(scraper.router, prefix="/api/scraper", tags=["Scraper"])
app.include_router(extraction.router, prefix="/api/extraction", tags=["Extraction"])
app.include_router(pages.router, prefix="/api/pages", tags=["Pages"])
app.include_router(system.router, prefix="/api", tags=["System"])
app.include_router(internal.router, prefix="/api/internal", tags=["Internal"])


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ── Serve UI static files ─────────────────────────────────────────────────────

_static_dir = Path(__file__).resolve().parent.parent / "static"

if _static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="ui-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve static files or fall back to index.html for SPA routing.

        Defensive 404 for /api/* and /ws so this catchall can never
        accidentally serve HTML for an API route — the symptom we hit in
        production was extraction-service receiving index.html for
        /api/pages/<id> and choking on JSONDecodeError. Concrete API
        routes should always win during normal route resolution, but
        making the catchall explicitly opt out is cheap insurance against
        any future routing quirk.
        """
        if full_path.startswith("api/") or full_path == "api" or full_path.startswith("ws"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = _static_dir / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_static_dir / "index.html")
