"""
Seed built-in schema templates on startup.

Templates are marked is_template=True. Idempotent — re-running won't
duplicate existing templates with the same name.
"""

import logging
import uuid
from datetime import datetime
from typing import List

from ..database import db

logger = logging.getLogger(__name__)


BUILTIN_TEMPLATES = [
    {
        "name": "Article / Blog Post",
        "description": "Title, author, body, published date, and tags.",
        "fields": [
            {"name": "title", "field_type": "string", "is_array": False, "children": None},
            {"name": "author", "field_type": "string", "is_array": False, "children": None},
            {"name": "published_date", "field_type": "string", "is_array": False, "children": None},
            {"name": "body", "field_type": "string", "is_array": False, "children": None},
            {"name": "hero_image", "field_type": "image", "is_array": False, "children": None},
            {"name": "tags", "field_type": "string", "is_array": True, "children": None},
        ],
    },
    {
        "name": "Product Listing",
        "description": "Product name, price, description, image, SKU, and variants.",
        "fields": [
            {"name": "name", "field_type": "string", "is_array": False, "children": None},
            {"name": "price", "field_type": "number", "is_array": False, "children": None},
            {"name": "description", "field_type": "string", "is_array": False, "children": None},
            {"name": "image", "field_type": "image", "is_array": False, "children": None},
            {"name": "sku", "field_type": "string", "is_array": False, "children": None},
            {"name": "url", "field_type": "string", "is_array": False, "children": None},
        ],
    },
    {
        "name": "Person Profile",
        "description": "Name, role/title, bio, photo, and contact info.",
        "fields": [
            {"name": "name", "field_type": "string", "is_array": False, "children": None},
            {"name": "role", "field_type": "string", "is_array": False, "children": None},
            {"name": "bio", "field_type": "string", "is_array": False, "children": None},
            {"name": "photo", "field_type": "image", "is_array": False, "children": None},
            {"name": "email", "field_type": "string", "is_array": False, "children": None},
            {"name": "links", "field_type": "string", "is_array": True, "children": None},
        ],
    },
]


async def seed_templates():
    """Insert built-in schema templates if not already present."""
    existing = await db.list_schemas(templates_only=True)
    existing_names = {s["name"] for s in existing}

    inserted = 0
    for tpl in BUILTIN_TEMPLATES:
        if tpl["name"] in existing_names:
            continue
        now = datetime.utcnow().isoformat()
        await db.create_schema({
            "id": str(uuid.uuid4()),
            "name": tpl["name"],
            "description": tpl["description"],
            "fields": tpl["fields"],
            "is_template": True,
            "created_at": now,
            "updated_at": now,
        })
        inserted += 1

    if inserted:
        logger.info(f"Seeded {inserted} built-in schema templates")
