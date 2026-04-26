"""SQLite database for job persistence, schemas, and scrape data."""

import aiosqlite
import json
from typing import Optional, List
from datetime import datetime

from .config import settings


class Database:
    def __init__(self, path: str = None):
        self.path = path or settings.DATABASE_PATH
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        # 30s busy timeout (default 5s was not enough under combined
        # UI-polling + consumer-drain pressure on small VMs — drain
        # writes were timing out and losing records).
        self._db = await aiosqlite.connect(self.path, timeout=30.0)
        self._db.row_factory = aiosqlite.Row
        # WAL mode so concurrent reads (UI polling) don't block writer
        # transactions (consumer drain). NORMAL synchronous is the
        # WAL-recommended default.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA busy_timeout=30000")
        await self._create_tables()

    async def close(self):
        if self._db:
            await self._db.close()

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT NOT NULL DEFAULT 'created',
                scrape_config TEXT NOT NULL,
                extraction_config TEXT,
                extraction_mode TEXT,
                progress REAL DEFAULT 0.0,
                progress_message TEXT DEFAULT '',
                pages_discovered INTEGER DEFAULT 0,
                pages_downloaded INTEGER DEFAULT 0,
                resources_discovered INTEGER DEFAULT 0,
                resources_downloaded INTEGER DEFAULT 0,
                bytes_downloaded INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                scraped_at TEXT,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

            CREATE TABLE IF NOT EXISTS schemas (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                fields TEXT NOT NULL DEFAULT '[]',
                is_template INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_schemas_template ON schemas(is_template);

            CREATE TABLE IF NOT EXISTS scrape_pages (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                url TEXT NOT NULL,
                local_path TEXT,
                status TEXT DEFAULT 'pending',
                content_type TEXT,
                size INTEGER DEFAULT 0,
                depth INTEGER DEFAULT 0,
                parent_url TEXT,
                title TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_pages_job ON scrape_pages(job_id);
            CREATE INDEX IF NOT EXISTS idx_pages_url ON scrape_pages(url);
            CREATE INDEX IF NOT EXISTS idx_pages_status ON scrape_pages(status);

            CREATE TABLE IF NOT EXISTS scrape_resources (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                url TEXT NOT NULL,
                local_path TEXT,
                filename TEXT,
                category TEXT,
                size INTEGER DEFAULT 0,
                mime_type TEXT,
                content_hash TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_resources_job ON scrape_resources(job_id);
            CREATE INDEX IF NOT EXISTS idx_resources_hash ON scrape_resources(content_hash);

            CREATE TABLE IF NOT EXISTS extraction_results (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_results_job ON extraction_results(job_id);
        """)
        await self._db.commit()

        # Migration: add name column to existing jobs tables
        try:
            await self._db.execute("ALTER TABLE jobs ADD COLUMN name TEXT")
            await self._db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_name ON jobs(name)")
            await self._db.commit()
        except Exception:
            pass  # column already exists

        # Migration: add resources_discovered/downloaded columns
        for col in ("resources_discovered", "resources_downloaded"):
            try:
                await self._db.execute(f"ALTER TABLE jobs ADD COLUMN {col} INTEGER DEFAULT 0")
                await self._db.commit()
            except Exception:
                pass  # column already exists

    # ── Jobs ──────────────────────────────────────────────────────────────

    async def create_job(self, job: dict) -> None:
        now = datetime.utcnow().isoformat()
        await self._db.execute("""
            INSERT INTO jobs (id, name, status, scrape_config, extraction_config,
                extraction_mode, progress, progress_message,
                pages_discovered, pages_downloaded, bytes_downloaded,
                error_message, created_at, started_at, scraped_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job['id'], job.get('name'), job.get('status', 'created'),
            json.dumps(job.get('scrape_config', {})),
            json.dumps(job.get('extraction_config')) if job.get('extraction_config') else None,
            job.get('extraction_mode'),
            job.get('progress', 0.0), job.get('progress_message', ''),
            job.get('pages_discovered', 0), job.get('pages_downloaded', 0),
            job.get('bytes_downloaded', 0),
            job.get('error_message'),
            job.get('created_at', now),
            job.get('started_at'), job.get('scraped_at'), job.get('completed_at'),
        ))
        await self._db.commit()

    async def get_job(self, job_id: str) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        job = dict(row)
        job['scrape_config'] = json.loads(job['scrape_config'] or '{}')
        job['extraction_config'] = json.loads(job['extraction_config']) if job['extraction_config'] else None
        return job

    async def update_job(self, job_id: str, updates: dict) -> None:
        set_clauses = []
        params = []
        for key, value in updates.items():
            if key in ('scrape_config', 'extraction_config') and value is not None:
                value = json.dumps(value)
            set_clauses.append(f"{key} = ?")
            params.append(value)
        if not set_clauses:
            return
        params.append(job_id)
        await self._db.execute(
            f"UPDATE jobs SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        await self._db.commit()

    async def list_jobs(self, status: str = None, search: str = None,
                        date_from: str = None, date_to: str = None,
                        limit: int = 50, offset: int = 0) -> List[dict]:
        query = "SELECT * FROM jobs WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if search:
            query += " AND (name LIKE ? OR scrape_config LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        if date_from:
            query += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND created_at <= ?"
            params.append(date_to)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            job['scrape_config'] = json.loads(job['scrape_config'] or '{}')
            job['extraction_config'] = json.loads(job['extraction_config']) if job['extraction_config'] else None
            jobs.append(job)
        return jobs

    async def count_jobs(self, status: str = None, search: str = None,
                         date_from: str = None, date_to: str = None) -> int:
        query = "SELECT COUNT(*) as count FROM jobs WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if search:
            query += " AND (name LIKE ? OR scrape_config LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        if date_from:
            query += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND created_at <= ?"
            params.append(date_to)
        cursor = await self._db.execute(query, params)
        row = await cursor.fetchone()
        return row['count']

    async def delete_job(self, job_id: str, delete_results: bool = True) -> None:
        # DB metadata always goes (page/resource indexes, extraction rows, job
        # row). The `delete_results` flag controls disk files only — handled
        # by the route layer.
        await self._db.execute("DELETE FROM extraction_results WHERE job_id = ?", (job_id,))
        await self._db.execute("DELETE FROM scrape_resources WHERE job_id = ?", (job_id,))
        await self._db.execute("DELETE FROM scrape_pages WHERE job_id = ?", (job_id,))
        await self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await self._db.commit()

    async def clear_scrape_data(self, job_id: str) -> None:
        """Clear scrape_pages, scrape_resources, and reset job counters.

        Used when re-running a scrape so the database doesn't accumulate stale
        rows from the prior run. Disk files and the job row itself are kept.
        """
        await self._db.execute("DELETE FROM scrape_resources WHERE job_id = ?", (job_id,))
        await self._db.execute("DELETE FROM scrape_pages WHERE job_id = ?", (job_id,))
        await self._db.execute(
            """UPDATE jobs SET
                progress = 0.0, progress_message = '',
                pages_discovered = 0, pages_downloaded = 0,
                resources_discovered = 0, resources_downloaded = 0,
                bytes_downloaded = 0, error_message = NULL,
                scraped_at = NULL, completed_at = NULL
               WHERE id = ?""",
            (job_id,),
        )
        await self._db.commit()

    # ── Schemas ───────────────────────────────────────────────────────────

    async def create_schema(self, schema: dict) -> None:
        now = datetime.utcnow().isoformat()
        await self._db.execute("""
            INSERT INTO schemas (id, name, description, fields, is_template, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            schema['id'], schema['name'], schema.get('description', ''),
            json.dumps(schema.get('fields', [])),
            1 if schema.get('is_template') else 0,
            schema.get('created_at', now), schema.get('updated_at', now),
        ))
        await self._db.commit()

    async def get_schema(self, schema_id: str) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM schemas WHERE id = ?", (schema_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        s = dict(row)
        s['fields'] = json.loads(s['fields'] or '[]')
        s['is_template'] = bool(s['is_template'])
        return s

    async def list_schemas(self, templates_only: bool = False) -> List[dict]:
        query = "SELECT * FROM schemas"
        params = []
        if templates_only:
            query += " WHERE is_template = 1"
        query += " ORDER BY updated_at DESC"
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        schemas = []
        for row in rows:
            s = dict(row)
            s['fields'] = json.loads(s['fields'] or '[]')
            s['is_template'] = bool(s['is_template'])
            schemas.append(s)
        return schemas

    async def update_schema(self, schema_id: str, updates: dict) -> None:
        set_clauses = []
        params = []
        for key, value in updates.items():
            if key == 'fields' and value is not None:
                value = json.dumps(value)
            if key == 'is_template':
                value = 1 if value else 0
            set_clauses.append(f"{key} = ?")
            params.append(value)
        if not set_clauses:
            return
        set_clauses.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(schema_id)
        await self._db.execute(
            f"UPDATE schemas SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        await self._db.commit()

    async def delete_schema(self, schema_id: str) -> None:
        await self._db.execute("DELETE FROM schemas WHERE id = ?", (schema_id,))
        await self._db.commit()

    # ── Scrape Pages ──────────────────────────────────────────────────────

    async def add_scrape_page(self, page: dict) -> None:
        now = datetime.utcnow().isoformat()
        await self._db.execute("""
            INSERT OR IGNORE INTO scrape_pages
                (id, job_id, url, local_path, status, content_type, size, depth, parent_url, title, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            page['id'], page['job_id'], page['url'],
            page.get('local_path'), page.get('status', 'pending'),
            page.get('content_type'), page.get('size', 0),
            page.get('depth', 0), page.get('parent_url'),
            page.get('title'), now,
        ))
        await self._db.commit()

    async def update_scrape_page(self, page_id: str, updates: dict) -> None:
        set_clauses = []
        params = []
        for key, value in updates.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)
        if not set_clauses:
            return
        params.append(page_id)
        await self._db.execute(
            f"UPDATE scrape_pages SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        await self._db.commit()

    async def list_scrape_pages(self, job_id: str, status: str = None,
                                 limit: int = 200, offset: int = 0) -> List[dict]:
        query = "SELECT * FROM scrape_pages WHERE job_id = ?"
        params = [job_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY depth ASC, url ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def count_scrape_pages(self, job_id: str, status: str = None) -> int:
        query = "SELECT COUNT(*) as count FROM scrape_pages WHERE job_id = ?"
        params = [job_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        cursor = await self._db.execute(query, params)
        row = await cursor.fetchone()
        return row['count']

    async def count_scrape_resources(self, job_id: str, category: str = None) -> int:
        query = "SELECT COUNT(*) as count FROM scrape_resources WHERE job_id = ?"
        params = [job_id]
        if category:
            query += " AND category = ?"
            params.append(category)
        cursor = await self._db.execute(query, params)
        row = await cursor.fetchone()
        return row['count']

    async def get_scrape_page_by_url(self, job_id: str, url: str) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM scrape_pages WHERE job_id = ? AND url = ?",
            (job_id, url)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ── Scrape Resources ──────────────────────────────────────────────────

    async def add_scrape_resource(self, resource: dict) -> None:
        now = datetime.utcnow().isoformat()
        await self._db.execute("""
            INSERT OR IGNORE INTO scrape_resources
                (id, job_id, url, local_path, filename, category, size, mime_type, content_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resource['id'], resource['job_id'], resource['url'],
            resource.get('local_path'), resource.get('filename'),
            resource.get('category'), resource.get('size', 0),
            resource.get('mime_type'), resource.get('content_hash'), now,
        ))
        await self._db.commit()

    async def get_resource_by_hash(self, content_hash: str) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM scrape_resources WHERE content_hash = ? LIMIT 1",
            (content_hash,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_scrape_resources(self, job_id: str, category: str = None,
                                     limit: int = 200, offset: int = 0) -> List[dict]:
        query = "SELECT * FROM scrape_resources WHERE job_id = ?"
        params = [job_id]
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY filename ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Extraction Results ────────────────────────────────────────────────

    async def save_extraction_results(self, job_id: str, result_id: str, data: list) -> None:
        now = datetime.utcnow().isoformat()
        await self._db.execute("""
            INSERT OR REPLACE INTO extraction_results (id, job_id, data, created_at)
            VALUES (?, ?, ?, ?)
        """, (result_id, job_id, json.dumps(data), now))
        await self._db.commit()

    async def get_extraction_results(self, job_id: str) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM extraction_results WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        r = dict(row)
        r['data'] = json.loads(r['data'] or '[]')
        return r


db = Database()
