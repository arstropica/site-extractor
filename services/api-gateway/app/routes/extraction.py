"""Extraction routes — proxies to extraction service, manages results."""

import json
import csv
import io
from datetime import datetime
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
import httpx

from ..database import db
from ..config import settings
from ..services.websocket import ws_manager

router = APIRouter()


@router.post("/{job_id}/start")
async def start_extraction(job_id: str, request: Request):
    """Start extraction by forwarding to the extraction service."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("scraped", "completed", "mapping"):
        raise HTTPException(status_code=400, detail=f"Cannot extract from status '{job['status']}'")
    if not job.get("extraction_config"):
        raise HTTPException(status_code=400, detail="No extraction config set on job")

    # Write page index for the extraction service to consume
    await _write_page_index(job_id)

    await db.update_job(job_id, {"status": "extracting"})

    # Load schema fields if schema_id is set
    extraction_config = job["extraction_config"]
    schema_fields = []
    schema_id = extraction_config.get("schema_id")
    if schema_id:
        schema = await db.get_schema(schema_id)
        if schema:
            schema_fields = schema["fields"]

    # Forward to extraction service
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{settings.EXTRACTION_SERVICE_URL}/extraction/extract",
                json={
                    "job_id": job_id,
                    "extraction_config": extraction_config,
                    "schema_fields": schema_fields,
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        await db.update_job(job_id, {
            "status": "failed",
            "error_message": f"Extraction service error: {str(e)}",
        })
        raise HTTPException(status_code=502, detail=f"Extraction service error: {str(e)}")

    # Persist results to SQLite
    result_id = result.get("result_id", "")
    rows_extracted = result.get("rows_extracted", 0)

    # Read the result file written by the extraction service
    result_path = Path(settings.DATA_DIR) / "jobs" / job_id / "results" / f"{result_id}.json"
    if result_path.exists():
        result_data = json.loads(result_path.read_text())
        await db.save_extraction_results(job_id, result_id, result_data)

    await db.update_job(job_id, {
        "status": "completed",
        "completed_at": datetime.utcnow().isoformat(),
    })

    return {
        "job_id": job_id,
        "result_id": result_id,
        "rows_extracted": rows_extracted,
        "status": "completed",
    }


@router.post("/{job_id}/preview")
async def preview_extraction(job_id: str, request: Request):
    """Preview extraction results via the extraction service."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    body = await request.json()
    limit = body.get("limit", 20)
    extraction_config = body.get("extraction_config", job.get("extraction_config", {}))
    schema_fields = body.get("schema_fields", [])

    # If no schema_fields provided, try loading from schema_id
    if not schema_fields and extraction_config:
        schema_id = extraction_config.get("schema_id")
        if schema_id:
            schema = await db.get_schema(schema_id)
            if schema:
                schema_fields = schema["fields"]

    # Write page index
    await _write_page_index(job_id)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.EXTRACTION_SERVICE_URL}/extraction/preview",
                json={
                    "job_id": job_id,
                    "extraction_config": extraction_config,
                    "schema_fields": schema_fields,
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Extraction service error: {str(e)}")


@router.post("/{job_id}/validate-selector")
async def validate_selector(job_id: str, request: Request):
    """Validate a CSS selector against scraped pages."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    body = await request.json()
    selector = body.get("selector", "")
    limit = body.get("limit", 10)

    await _write_page_index(job_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.EXTRACTION_SERVICE_URL}/extraction/validate-selector",
                json={"job_id": job_id, "selector": selector, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Extraction service error: {str(e)}")


@router.get("/{job_id}/results")
async def get_results(
    job_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None),
    sort_dir: str = Query("asc"),
):
    """Get extraction results for a job."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.get_extraction_results(job_id)
    if not result:
        return {"results": [], "count": 0}

    data = result.get("data", [])
    total = len(data)

    if sort_by and data:
        reverse = sort_dir.lower() == "desc"
        try:
            data = sorted(
                data,
                key=lambda row: row.get("data", {}).get(sort_by, "") if isinstance(row, dict) else "",
                reverse=reverse,
            )
        except (TypeError, KeyError):
            pass

    paginated = data[offset:offset + limit]
    return {"results": paginated, "count": total}


# Smart quote / typographic character replacements for ASCII normalization
NORMALIZE_REPLACEMENTS = {
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2026": "...",  # horizontal ellipsis
    "\u00a0": " ",  # non-breaking space
}


def _normalize_text(value):
    """Replace common typographic characters with ASCII equivalents, recursively."""
    if isinstance(value, str):
        for src, dst in NORMALIZE_REPLACEMENTS.items():
            value = value.replace(src, dst)
        return value
    if isinstance(value, dict):
        return {k: _normalize_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_text(v) for v in value]
    return value


@router.get("/{job_id}/results/export/{format}")
async def export_results(
    job_id: str,
    format: str,
    normalize: bool = Query(False, description="Convert smart quotes/dashes to ASCII"),
):
    """Export extraction results as JSON or CSV."""
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="Format must be 'json' or 'csv'")

    result = await db.get_extraction_results(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="No results found")

    data = result.get("data", [])
    if normalize:
        data = _normalize_text(data)

    if format == "json":
        return StreamingResponse(
            io.BytesIO(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=extraction-{job_id[:8]}.json"},
        )

    if not data:
        raise HTTPException(status_code=404, detail="No data to export")

    def flatten(obj, prefix=""):
        items = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    items.update(flatten(v, new_key))
                else:
                    items[new_key] = v
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new_key = f"{prefix}.{i}"
                if isinstance(v, (dict, list)):
                    items.update(flatten(v, new_key))
                else:
                    items[new_key] = v
        else:
            items[prefix] = obj
        return items

    flat_rows = [flatten(row.get("data", row) if isinstance(row, dict) else row) for row in data]

    all_keys = []
    seen = set()
    for row in flat_rows:
        for key in row:
            if key not in seen:
                all_keys.append(key)
                seen.add(key)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(flat_rows)

    # UTF-8 BOM helps Excel render Unicode correctly
    csv_bytes = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=extraction-{job_id[:8]}.csv"},
    )


async def _write_page_index(job_id: str):
    """Write a page index JSON file for the extraction service to consume."""
    pages = await db.list_scrape_pages(job_id, status="downloaded", limit=10000)
    index_path = Path(settings.DATA_DIR) / "jobs" / job_id / "page_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(pages, indent=2))

    resources = await db.list_scrape_resources(job_id, limit=10000)
    res_path = Path(settings.DATA_DIR) / "jobs" / job_id / "resource_index.json"
    res_path.write_text(json.dumps(resources, indent=2))
