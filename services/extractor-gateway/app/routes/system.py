"""System routes: health, config."""

from fastapi import APIRouter, Request
import httpx

from ..config import settings

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    services = {
        "api_gateway": "healthy",
        "redis": "unknown",
        "scraper": "unknown",
        "extraction": "unknown",
    }

    # Check Redis
    try:
        await request.app.state.redis.ping()
        services["redis"] = "healthy"
    except Exception:
        services["redis"] = "unhealthy"

    # Check scraper service
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.SCRAPER_SERVICE_URL}/scraper/health")
            services["scraper"] = "healthy" if resp.status_code == 200 else "unhealthy"
    except Exception:
        services["scraper"] = "unhealthy"

    # Check extraction service
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.EXTRACTION_SERVICE_URL}/extraction/health")
            services["extraction"] = "healthy" if resp.status_code == 200 else "unhealthy"
    except Exception:
        services["extraction"] = "unhealthy"

    overall = "healthy" if all(v == "healthy" for v in services.values()) else "degraded"
    return {"status": overall, "services": services, "version": "0.1.0"}
