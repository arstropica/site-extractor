"""Scraped page serving routes — proxies stored pages for the mapping UI iframe."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from ..database import db
from ..config import settings
from ..services.page_injector import inject_picker

logger = logging.getLogger(__name__)

router = APIRouter()


def _disk_id_for_filename(filename: str) -> str:
    """Stable id for a disk-only page record (md5 of filename).

    Pages that never landed in SQLite (consumer drain hiccup, race during
    re-scrape, etc.) need a stable id we can echo back to /view/<id> later.
    md5 is good enough — non-cryptographic, just a deterministic mapping.
    """
    return hashlib.md5(filename.encode()).hexdigest()


def _scan_disk_pages(job_id: str) -> list:
    """Enumerate pages from <job>/pages on disk, reading freshness sidecars.

    Returns the same record shape as db.list_scrape_pages() so the two can
    be union'd. Disk is the source of truth — extraction reads from here —
    so the API listing must include anything found here even if SQLite
    didn't get the matching record.
    """
    pages_dir = Path(settings.DATA_DIR) / "jobs" / job_id / "pages"
    if not pages_dir.is_dir():
        return []
    out = []
    for f in sorted(pages_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix not in (".html", ".htm"):
            continue
        meta = {}
        meta_path = f.with_suffix(f.suffix + ".meta.json")
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        out.append({
            "id": _disk_id_for_filename(f.name),
            "job_id": job_id,
            "url": meta.get("url", ""),
            "local_path": f"pages/{f.name}",
            "status": "downloaded",
            "content_type": meta.get("content_type"),
            "size": f.stat().st_size,
            "depth": 0,
            "parent_url": None,
            "title": meta.get("title"),
        })
    return out


@router.get("/{job_id}")
async def list_pages(
    job_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List scraped pages for a job, unioned with on-disk pages.

    SQLite is populated from Redis by the consumer — if any drain cycle
    silently drops records (which the user has hit), the dropdown
    listing was missing pages even though the files were on disk and
    the extraction service was happily using them. Backfill from disk
    so the API listing always matches what extraction sees.
    """
    db_pages = await db.list_scrape_pages(job_id, status=status, limit=limit, offset=offset)
    db_count = await db.count_scrape_pages(job_id, status=status)

    db_local_paths = {p.get("local_path") for p in db_pages if p.get("local_path")}
    disk_pages = _scan_disk_pages(job_id)
    backfill = [p for p in disk_pages if p["local_path"] not in db_local_paths]
    if backfill:
        logger.warning(
            f"Job {job_id}: backfilling {len(backfill)} page(s) from disk that "
            f"are missing from SQLite (consumer drain may have dropped them)"
        )
    return {"pages": db_pages + backfill, "count": db_count + len(backfill)}


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

    pages = await db.list_scrape_pages(job_id, limit=1000)
    page = next((p for p in pages if p["id"] == page_id), None)

    if not page:
        # The dropdown may have been served a disk-id (md5(filename)) for a
        # page that never made it into SQLite — try resolving that.
        pages_dir = Path(settings.DATA_DIR) / "jobs" / job_id / "pages"
        if pages_dir.is_dir():
            for f in pages_dir.iterdir():
                if not f.is_file() or f.suffix not in (".html", ".htm"):
                    continue
                if _disk_id_for_filename(f.name) == page_id:
                    page = {
                        "local_path": f"pages/{f.name}",
                        "content_type": "text/html",
                    }
                    break

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


@router.get("/{job_id}/assets/{resource_path:path}")
async def serve_asset(job_id: str, resource_path: str):
    """Serve a scraped asset (image, CSS, etc.) referenced by a stored page.

    Path is `assets` (plural) to match what page_storage.save_page() writes
    when it rewrites img/link/source URLs in the saved HTML — the rewrite
    produces `../assets/<filename>`, which the iframe-served page resolves
    to `/api/pages/<job_id>/assets/<filename>` relative to the view route.
    """
    # Saved HTML rewrites are bare filenames living under <jobdir>/assets/,
    # but accept either form so a request like /assets/assets/foo.png also
    # resolves cleanly.
    if not resource_path.startswith("assets/"):
        resource_path = f"assets/{resource_path}"
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
