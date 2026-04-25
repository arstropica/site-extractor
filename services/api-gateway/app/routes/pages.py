"""Scraped page serving routes — proxies stored pages for the mapping UI iframe."""

from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from ..database import db
from ..config import settings
from ..services.page_injector import inject_picker

router = APIRouter()


@router.get("/{job_id}")
async def list_pages(
    job_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List scraped pages for a job."""
    pages = await db.list_scrape_pages(job_id, status=status, limit=limit, offset=offset)
    count = await db.count_scrape_pages(job_id, status=status)
    return {"pages": pages, "count": count}


@router.get("/{job_id}/resources")
async def list_resources(
    job_id: str,
    category: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List scraped resources (images, documents, etc.) for a job."""
    resources = await db.list_scrape_resources(job_id, category=category, limit=limit, offset=offset)
    return {"resources": resources, "count": len(resources)}


@router.get("/{job_id}/view/{page_id}")
async def view_page(job_id: str, page_id: str):
    """Serve a scraped page for iframe rendering in the mapping UI."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pages = await db.list_scrape_pages(job_id)
    page = next((p for p in pages if p["id"] == page_id), None)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    local_path = page.get("local_path")
    if not local_path:
        raise HTTPException(status_code=404, detail="Page content not available")

    file_path = Path(settings.DATA_DIR) / "jobs" / job_id / local_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Page file not found")

    content_type = page.get("content_type", "text/html")

    # Inject picker script for the mapping UI
    if "html" in content_type:
        html = file_path.read_text(encoding="utf-8", errors="replace")
        injected = inject_picker(html)
        return Response(content=injected, media_type="text/html")

    return FileResponse(file_path, media_type=content_type)


@router.get("/{job_id}/asset/{resource_path:path}")
async def serve_asset(job_id: str, resource_path: str):
    """Serve a scraped asset (image, CSS, etc.) referenced by a stored page."""
    asset_path = Path(settings.DATA_DIR) / "jobs" / job_id / resource_path

    # Prevent directory traversal
    try:
        asset_path = asset_path.resolve()
        base = (Path(settings.DATA_DIR) / "jobs" / job_id).resolve()
        if not str(asset_path).startswith(str(base)):
            raise HTTPException(status_code=403, detail="Access denied")
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")

    return FileResponse(asset_path)


@router.get("/{job_id}/tree")
async def page_tree(job_id: str):
    """Get the page tree structure for visualization."""
    pages = await db.list_scrape_pages(job_id, limit=5000)

    tree = {}
    for page in pages:
        url = page.get("url", "")
        depth = page.get("depth", 0)
        parent = page.get("parent_url")
        tree[url] = {
            "id": page["id"],
            "url": url,
            "depth": depth,
            "parent_url": parent,
            "status": page.get("status"),
            "title": page.get("title"),
            "size": page.get("size", 0),
            "content_type": page.get("content_type"),
        }

    return {"tree": tree, "count": len(tree)}
