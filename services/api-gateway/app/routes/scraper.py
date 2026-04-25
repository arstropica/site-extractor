"""Scraper proxy routes — forwards commands to the scraper service."""

from fastapi import APIRouter, HTTPException, Request
import httpx

from ..config import settings

router = APIRouter()


@router.get("/status/{job_id}")
async def scraper_status(job_id: str):
    """Get real-time scraper status for a job."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.SCRAPER_SERVICE_URL}/scraper/status/{job_id}")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Scraper service error: {str(e)}")
