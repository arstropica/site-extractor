"""External asset retrieval endpoint.

Serves any file under a job's data directory with content-type and cache
headers populated from the file's companion `<path>.meta.json` (when
present). Designed for external consumers that pull extraction results
and follow asset URLs in the records — the engine rewrites asset
references to point here, so a consumer doesn't need to know anything
about the on-disk layout.

This is byte-faithful retrieval: HTML is served raw (no picker
injection — that's the iframe-only `/api/pages/{job_id}/view/{page_id}`
concern). Path traversal is blocked.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _read_meta(file_path: Path) -> dict:
    """Read the `<file_path>.meta.json` companion if it exists. Returns
    an empty dict on missing / unreadable / malformed meta — callers
    treat absence as 'no extra headers' rather than a hard failure."""
    meta_path = file_path.with_name(file_path.name + ".meta.json")
    if not meta_path.is_file():
        return {}
    try:
        with meta_path.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"meta.json unreadable at {meta_path}: {e}")
        return {}


def _resolve_under_job(job_id: str, file_path: str) -> Path:
    """Resolve `<DATA_DIR>/jobs/<job_id>/<file_path>` and verify the
    resolved path is still under the job's directory. Raises
    HTTPException(403) on traversal, HTTPException(400) on bad input."""
    base = (Path(settings.DATA_DIR) / "jobs" / job_id).resolve()
    try:
        candidate = (base / file_path).resolve()
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid path")
    # `resolve()` followed by string-prefix check would miss the case
    # where `base` itself is the candidate; use is_relative_to (3.9+).
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return candidate


@router.get("/{job_id}/{file_path:path}")
async def get_asset(job_id: str, file_path: str, request: Request):
    """Serve a file from the job's data directory.

    Headers populated from `<file_path>.meta.json` when present:
      - Content-Type ← meta.content_type (else FastAPI guesses)
      - ETag ← meta.etag || meta.content_hash (quoted, weak)
      - Last-Modified ← meta.last_modified (string passthrough)
      - X-Original-URL ← meta.url

    Conditional GET: If-None-Match matching the computed ETag → 304.
    """
    abs_path = _resolve_under_job(job_id, file_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    meta = _read_meta(abs_path)

    # Pick the strongest stable identifier we have. content_hash is the
    # bytes-of-this-file digest the scraper computed; etag is whatever
    # the upstream HTTP server sent (rarely populated for archived
    # assets). Either is fine as a strong validator for our purposes.
    etag_source: Optional[str] = meta.get("etag") or meta.get("content_hash")
    etag_header = f'"{etag_source}"' if etag_source else None

    if etag_header:
        client_inm = request.headers.get("if-none-match")
        if client_inm and client_inm.strip() == etag_header:
            # 304 must omit body and Content-Length per RFC 7232
            return Response(status_code=304, headers={"ETag": etag_header})

    response_headers: dict = {}
    if etag_header:
        response_headers["ETag"] = etag_header
    if meta.get("last_modified"):
        response_headers["Last-Modified"] = str(meta["last_modified"])
    if meta.get("url"):
        response_headers["X-Original-URL"] = str(meta["url"])

    media_type = meta.get("content_type")  # FileResponse falls back to
    # mimetypes.guess_type when this is None — exactly what we want.

    return FileResponse(
        abs_path,
        media_type=media_type,
        headers=response_headers,
    )
