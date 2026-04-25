"""Extraction schema management routes."""

import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Query

from ..database import db

router = APIRouter()


@router.post("")
async def create_schema(request: Request):
    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Schema name is required")

    schema_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    await db.create_schema({
        "id": schema_id,
        "name": name,
        "description": body.get("description", ""),
        "fields": body.get("fields", []),
        "is_template": body.get("is_template", False),
        "created_at": now,
        "updated_at": now,
    })

    return await db.get_schema(schema_id)


@router.get("")
async def list_schemas(templates_only: bool = Query(False)):
    schemas = await db.list_schemas(templates_only=templates_only)
    return {"schemas": schemas, "count": len(schemas)}


@router.get("/{schema_id}")
async def get_schema(schema_id: str):
    schema = await db.get_schema(schema_id)
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    return schema


@router.patch("/{schema_id}")
async def update_schema(schema_id: str, request: Request):
    schema = await db.get_schema(schema_id)
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")

    body = await request.json()
    allowed_fields = {"name", "description", "fields", "is_template"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}

    if updates:
        await db.update_schema(schema_id, updates)

    return await db.get_schema(schema_id)


@router.delete("/{schema_id}")
async def delete_schema(schema_id: str):
    schema = await db.get_schema(schema_id)
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    await db.delete_schema(schema_id)
    return {"message": "Schema deleted"}
