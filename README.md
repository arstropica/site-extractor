# Site Extractor

**Two-Phase Site Spider and Content Extraction Tool**

A self-hosted Docker Compose application for crawling websites and extracting structured data from the captured content. Wizard-driven UI guides users through scraper configuration, schema definition, point-and-click content mapping, and result export — with real-time progress, resumable jobs, and a boundary-scoped extraction model that handles complex nested layouts.

> **For developers / agents working on the code**: see `CLAUDE.md` for the architectural contracts (pipeline state derivation, status state-machine, client-store rules). The README covers operations and usage; CLAUDE.md covers what must hold to keep the system coherent.

---

## Features

- **Two-phase pipeline** — Phase 1 spiders the site to a local mirror; Phase 2 extracts structured data from stored pages. Phases are decoupled so mappings can be rebuilt without re-crawling.
- **Two crawl modes** — Lightweight HTTP via httpx, or headless Chromium via Playwright for SPA / JavaScript-rendered content.
- **Boundary-scoped extraction** — Cumulative CSS selector boundaries (root → record → collection iterator) handle nested and repeating layouts. Mappings live per-job; schemas are reusable.
- **File-based extraction** — Regex-categorized filename listing for asset-heavy crawls (PDFs, media, archives).
- **Point-and-click content mapper** — Scraped pages render in a sandboxed iframe with an injected picker that generates CSS selectors and live-highlights matches.
- **Schema templates** — Reusable schemas with built-in templates (Article/Blog, Product Listing, Person Profile) editable in either a visual tree builder or a raw JSON editor.
- **Cross-page record merging** — `merge_by` + `url_regex` collapse multiple pages-per-entity (e.g., separate Overview / Bio / GameLog tabs) into single merged records keyed by an extracted ID.
- **Resource filtering** — Per-category include/exclude with extension lists; HEAD-first probing with size guards. Opt-in content-hash deduplication trades fidelity for disk savings (default off so every URL the page references produces its own row and file).
- **Authentication** — Basic, Bearer token, or cookie injection per job. Credentials are Fernet-encrypted at rest with `enc:v1:` versioning. Browser-driven login is deferred to v2.
- **Domain allowlist + path filters** — Strict domain match with wildcard support; cross-domain redirects are recorded but not followed.
- **Real-time progress** — WebSocket relay of scraper / extraction events: page tree updates, resource discovery, progress, status changes, errors.
- **Resumable jobs** — Crawl state persists; orphaned `scraping` jobs are auto-resumed at startup.
- **Result export** — JSON or CSV download from the Results step or via direct API.

---

## Quick Start

### Prerequisites

- Docker & Docker Compose (v2)
- ~2 GB RAM headroom for the scraper service when using browser mode (Playwright Chromium)
- Outbound HTTP/HTTPS access to the sites you intend to crawl

CPU-only — there is no GPU dependency.

### Installation

```bash
# Clone repository
git clone <your-fork-url> site-extractor
cd site-extractor

# Configure environment
cp .env.example .env
# Edit .env — at minimum set ENCRYPTION_KEY (see below)

# Create the Docker network (one-time, idempotent)
docker network create ${DOCKER_NETWORK:-extractor_network} 2>/dev/null || true

# Start services
docker compose up -d --build

# Verify
curl http://localhost:12000/api/health | jq .

# Open the UI
open http://localhost:12000/
```

#### Generating a secure `ENCRYPTION_KEY`

The key can be any sufficiently random string — internally it is SHA-256 hashed to derive the Fernet key used for credential encryption at rest. Pick **one** of the following:

```bash
# OpenSSL (available everywhere)
openssl rand -base64 48

# Python stdlib
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Native Fernet key (also valid — it's just a random string)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output into `.env` as `ENCRYPTION_KEY=<value>`. **Do not commit `.env`** — it is gitignored. **Do not change the key after creating jobs** — rotating it makes existing encrypted credentials undecryptable; the gateway will raise `CredentialDecryptError` and you will need to recreate the affected jobs.

### Quick Test (via API)

```bash
# Create a job
curl -X POST http://localhost:12000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Example crawl",
    "scrape_config": {
      "seed_urls": ["https://example.com/"],
      "crawl_mode": "http",
      "depth_limit": 1,
      "respect_robots": true,
      "request_delay_ms": 500
    }
  }'
# → { "job_id": "...", "status": "created", "message": "..." }

JOB_ID="<paste-job-id-from-response>"

# Start the scrape
curl -X POST "http://localhost:12000/api/jobs/$JOB_ID/start-scrape"

# Poll status
curl "http://localhost:12000/api/jobs/$JOB_ID" | jq '{status, pages_downloaded, resources_downloaded, progress}'
```

Most users will drive the wizard at `http://localhost:12000/` rather than the API. The API is documented at `http://localhost:12000/docs` (FastAPI auto-generated Swagger UI).

---

## Architecture

```
┌──────────────────┐
│   Browser (UI)   │  React 19 + Vite + Tailwind 4 + FlyonUI
└────────┬─────────┘
         │ HTTP / WebSocket
         ▼
┌──────────────────┐
│ extractor-gateway│ :12000 ← REST + WS hub, serves React SPA
│   (FastAPI)      │         SQLite persistence, encrypted credentials
└────┬─────────┬───┘
     │         │
     │         └────────────► extraction-service :8002 (internal)
     │                          CSS selector engine, boundary scoping,
     │                          merge_by, image download
     │
     └──────────────────────► scraper-service :8001 (internal)
                                httpx + Playwright Chromium,
                                sliding-window dispatcher,
                                HEAD-first asset download

┌──────────────────┐
│      redis       │ :6379 (internal) ← pub/sub + page/resource lists
└──────────────────┘
```

**4 services** (all coordinated via `docker-compose.yml`):

- **extractor-gateway** — FastAPI orchestrator. Sole owner of SQLite (jobs, schemas, page index, resource index, extraction results). Hosts REST endpoints, WebSocket relay to clients, encrypted credential store, the explicit job-status state-machine validator, and the compiled React SPA as static assets.
- **scraper-service** — Crawler with two modes (httpx / Playwright Chromium). Sliding-window async dispatcher, per-domain + global semaphores, HEAD-first asset probing with streaming size guard. Posts page/resource records and final-state updates to the gateway over HTTP (`/api/internal/*`); the scraper never touches the database directly.
- **extraction-service** — Boundary-scoped CSS extraction engine, URL-pattern filtering, image download, and `merge_by` cross-page record consolidation. Reads the canonical page list from the gateway over HTTP.
- **redis** — Pub/sub channels (`scraper_events`, `extraction_events`) for cross-service progress events that the gateway relays to WebSocket clients, plus per-job pause/cancel signal keys.

The gateway is the only public service; the scraper and extraction services are reachable only on the internal Docker network.

**Pipeline state contract** — Job status changes through dedicated endpoints (`/start-scrape`, `/pause`, `/cancel`, `/extraction/{id}/start`); the gateway validates every transition against an explicit graph (`shared/state_machine.py`). PATCH does NOT accept `status`. Failure is partitioned by stage (`failed_stage` ∈ `scrape | extract`) so the wizard marks the right step red. See `CLAUDE.md` for the full architectural contract — read it before non-trivial changes.

---

## API Examples

The gateway exposes a small REST surface; full Swagger UI is at `http://localhost:12000/docs`.

### Create + start a job

```bash
curl -X POST http://localhost:12000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Player profiles",
    "scrape_config": {
      "seed_urls": ["https://www.example.com/players"],
      "crawl_mode": "browser",
      "depth_limit": 2,
      "domain_filter": {
        "allowed_domains": ["www.example.com"],
        "path_filters": ["/players/*"]
      },
      "respect_robots": false,
      "request_delay_ms": 500,
      "max_concurrent_per_domain": 2,
      "max_concurrent_total": 8
    }
  }'

curl -X POST http://localhost:12000/api/jobs/$JOB_ID/start-scrape
```

### Configure extraction with merge_by + url_regex

```bash
curl -X PATCH http://localhost:12000/api/jobs/$JOB_ID \
  -H "Content-Type: application/json" \
  -d '{
    "extraction_config": {
      "mode": "document",
      "schema_id": "<your-schema-id>",
      "document": {
        "merge_by": "player_id",
        "boundaries": [],
        "field_mappings": [
          { "field_path": "player_id",  "url_regex": "/id/(\\d+)" },
          { "field_path": "first_name", "selector": ".PlayerHeader__Name span:nth-of-type(1)" },
          { "field_path": "last_name",  "selector": ".PlayerHeader__Name span:nth-of-type(2)" }
        ]
      }
    }
  }'

curl -X POST http://localhost:12000/api/extraction/$JOB_ID/start
```

### Preview, results, export

```bash
# Preview without persisting (capped by limit)
curl -X POST http://localhost:12000/api/extraction/$JOB_ID/preview \
  -H "Content-Type: application/json" \
  -d '{ "extraction_config": {...}, "schema_fields": [...], "limit": 20 }'

# Paginated results
curl "http://localhost:12000/api/extraction/$JOB_ID/results?limit=50&offset=0"

# Export
curl -OJ "http://localhost:12000/api/extraction/$JOB_ID/results/export/json"
curl -OJ "http://localhost:12000/api/extraction/$JOB_ID/results/export/csv?normalize=true"
```

### Fetch a stored asset

`GET /api/asset/{job_id}/{file_path:path}` serves any file under the job's data directory with content-type and cache headers populated from the file's `<path>.meta.json` companion. Designed for external consumers that pull extraction results and follow asset URLs in the records — the extraction engine rewrites image fields like `../assets/<name>` to `/api/asset/<job_id>/assets/<name>`, so a consumer reading `/api/extraction/{job_id}/results` can follow logo / image fields directly without knowing the on-disk layout.

```bash
# Fetch an asset by job-relative path. The path can point at anything
# under the job's data directory: assets/, pages/, results/, etc.
curl -OJ "http://localhost:12000/api/asset/$JOB_ID/assets/abc123_logo.gif"

# Conditional GET — pass back the ETag from a prior response;
# unchanged bytes return 304 Not Modified with no body.
ETAG=$(curl -sI "http://localhost:12000/api/asset/$JOB_ID/assets/abc123_logo.gif" | awk -F'"' '/^etag:/ {print "\""$2"\""}')
curl -i -H "If-None-Match: $ETAG" "http://localhost:12000/api/asset/$JOB_ID/assets/abc123_logo.gif"
```

Response headers (when `<path>.meta.json` exists):

| Header | Source |
|---|---|
| `Content-Type` | `meta.content_type` (else FastAPI's filesystem guess) |
| `ETag` | `meta.etag` ‖ `meta.content_hash` (the latter is the bytes-of-this-file digest the scraper computed) |
| `Last-Modified` | `meta.last_modified` (string passthrough) |
| `X-Original-URL` | `meta.url` — lets consumers correlate stored bytes with the source they came from |
| `Content-Length` | filesystem (always accurate; meta could be stale) |

Path traversal (`..`, encoded or raw) returns **403**; missing files return **404**. HTML is served raw — the picker injection used by the in-app mapper iframe lives only on `/api/pages/{job_id}/view/{page_id}`.

### WebSocket events

Connect to `ws://localhost:12000/ws` to receive `PAGE_DISCOVERED`, `PAGE_DOWNLOADED`, `RESOURCE_DISCOVERED`, `RESOURCE_DOWNLOADED`, `SCRAPE_PROGRESS`, `SCRAPE_STATUS`, `PAGE_TREE_UPDATE`, `EXTRACTION_PROGRESS`, and `EXTRACTION_STATUS` events in real time.

---

## Documentation

- **[Requirements Specification](requirements.md)** — Full functional spec covering scraper, extraction, schema, UI behavior, and job lifecycle
- **Live OpenAPI:** <http://localhost:12000/docs> (Swagger UI)
- **WebSocket events:** see `services/shared/models.py` (`WSEventType` enum)

---

## Technology Stack

**Frontend**

- React 19 + TypeScript + Vite 8
- Tailwind CSS 4 (compile-time via `@tailwindcss/vite`)
- FlyonUI 2.4 component library
- Radix UI primitives (Dialog, Dropdown, Select, Switch, Tabs, Toast, Tooltip)
- Zustand state management
- TanStack React Query for data fetching + cache invalidation
- React Router v6
- Tabler icons via Iconify

**Backend**

- FastAPI 0.115 + Python 3.11 + Uvicorn
- Pydantic v2 for shared API contracts
- aiosqlite for async SQLite persistence
- Redis 7 (pub/sub + lists) via `redis-py` async client
- httpx 0.28 for HTTP scraping
- Playwright 1.49 + Chromium for browser scraping
- BeautifulSoup 4 + SoupSieve (`:has()`-capable CSS selectors) + lxml for extraction
- cryptography (Fernet) for credential encryption at rest

**Deployment**

- Multi-stage Docker builds (Node UI build → Python runtime)
- Docker Compose v2
- SQLite database + host-bind data directory (configurable via `DATA_PATH`, defaults to `./data`)

---

## Configuration

### Environment Variables

All variables are read from the `.env` file at the project root (or the host environment). `docker-compose.yml` propagates the relevant subset into each service container.

| Name                       | Description                                                                                                                                                                                                                                            | Default                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------- |
| `DOCKER_PORT`              | Host port published by `extractor-gateway`; UI + REST + WS are all served from this port.                                                                                                                                                                    | `12000`                          |
| `DOCKER_NETWORK`           | Name of the external Docker bridge network all services attach to. Declared `external: true` in compose, so create it once with `docker network create $DOCKER_NETWORK` (or reuse one from another stack to share networking).                         | `extractor_network`              |
| `DATA_PATH`                | Host path bind-mounted to `/data` inside every service. Holds scraped pages, downloaded assets, and the SQLite database. Created on first run if it does not exist. Use an absolute path for clarity in production.                                    | `./data`                         |
| `REDIS_URL`                | Redis connection URL used by every service for pub/sub and list-based sync.                                                                                                                                                                            | `redis://redis:6379`             |
| `DATABASE_PATH`            | SQLite database file path inside the extractor-gateway container.                                                                                                                                                                                            | `/data/extractor.db`             |
| `DATA_DIR`                 | Root directory inside each container for scraped pages, downloaded assets, and the database. Mapped to the host via `DATA_PATH`.                                                                                                                       | `/data`                          |
| `SCRAPER_SERVICE_URL`      | Internal URL the extractor-gateway uses to reach the scraper service.                                                                                                                                                                                        | `http://scraper-service:8001`    |
| `EXTRACTION_SERVICE_URL`   | Internal URL the extractor-gateway uses to reach the extraction service.                                                                                                                                                                                     | `http://extraction-service:8002` |
| `ENCRYPTION_KEY`           | Secret used to derive a Fernet key for encrypting stored job credentials (basic auth, bearer tokens, cookies). **Change in production.** Rotating this key invalidates all stored credentials and the gateway will fail loudly when it cannot decrypt. | `change-me-in-production`        |
| `MAX_DOWNLOAD_SIZE`        | Total bytes a single job is allowed to download across all pages and assets. Per-job override available via `scrape_config.max_download_size`. Units: bytes.                                                                                           | `524288000` (500 MB)             |
| `MAX_ASSET_SIZE`           | Per-asset size cap. Files larger than this are skipped without downloading (HEAD probe first; falls back to streaming guard). Units: bytes.                                                                                                            | `52428800` (50 MB)               |
| `SCRAPER_RETRY_LIMIT`      | Number of retry attempts per URL on transient failures (5xx, timeouts, connection resets). Per-job override via `scrape_config.retry_limit`.                                                                                                           | `1`                              |
| `SCRAPER_RETRY_BACKOFF_MS` | Backoff delay between retries, in milliseconds.                                                                                                                                                                                                        | `2000`                           |
| `BROWSER_POOL_SIZE`        | Number of concurrent Playwright browser contexts the scraper keeps open in browser mode.                                                                                                                                                               | `5`                              |
| `HTTP_PROXY`               | Optional HTTP proxy URL passed through to httpx and Playwright.                                                                                                                                                                                        | `` (unset)                       |
| `HTTPS_PROXY`              | Optional HTTPS proxy URL passed through to httpx and Playwright.                                                                                                                                                                                       | `` (unset)                       |

### Per-job overrides

Several environment defaults can be overridden per job via the `scrape_config` payload:

- `max_download_size` — overrides `MAX_DOWNLOAD_SIZE`
- `retry_limit` — overrides `SCRAPER_RETRY_LIMIT`
- `request_delay_ms`, `max_concurrent_per_domain`, `max_concurrent_total` — rate limiting
- `respect_robots`, `user_agent`, `auth`, `domain_filter`, `resource_filters` — crawl behavior
- `dedup.enabled` — when `true`, skip downloading a resource whose bytes match a file already saved in this scrape (default `false`; see Troubleshooting → "Crawl skipping files" for the fidelity trade-off)

Defaults are defined in `services/shared/models.py` (`ScrapeConfig`) and `DEFAULT_RESOURCE_FILTERS`.

---

## Development

```bash
# Build + start
docker compose up -d --build

# Tail logs
docker compose logs -f extractor-gateway
docker compose logs -f scraper-service
docker compose logs -f extraction-service

# Rebuild a single service after code changes
docker compose up -d --build extractor-gateway

# Inspect Redis
docker compose exec redis redis-cli
> KEYS *
> LRANGE scraper:pages 0 10
> SUBSCRIBE scraper_events

# Inspect SQLite
docker compose exec extractor-gateway sqlite3 /data/extractor.db
sqlite> .tables
sqlite> SELECT id, name, status, pages_downloaded FROM jobs ORDER BY created_at DESC LIMIT 10;

# UI dev server (proxies /api → :12000)
cd services/ui
npm install
npm run dev
```

---

## Troubleshooting

### Service won't start / unhealthy

```bash
docker compose ps
docker compose logs extractor-gateway --tail 100
docker compose logs scraper-service --tail 100
```

The scraper container has a 30-second `start_period` because Playwright Chromium initialization is heavy.

### Crawl skipping files

- Check the **Resource Filters** for the job — non-HTML extensions (e.g. `m4v`, `pdf`) need to be in an enabled category.
- Check `MAX_ASSET_SIZE` — files larger than this are skipped during HEAD probe. Bump it for media-heavy crawls (e.g. `MAX_ASSET_SIZE=262144000` for 250 MB).
- Check `respect_robots` — sites often disallow asset paths in `robots.txt`.
- Check the **Duplicates** toggle — when on, two URLs serving identical bytes only save the first one (the second has no row, no file, no event). References to the duplicate URL in extracted data won't resolve. Turn it off if downstream consumers expect every URL the page references to produce a fetchable resource.

### Stored credentials fail to decrypt

Rotating `ENCRYPTION_KEY` invalidates all stored credentials. The gateway raises `CredentialDecryptError` on read rather than silently producing garbage. Recreate affected jobs with fresh credentials, or restore the previous key.

### Browser mode never finishes loading

Some sites never reach `networkidle` (long-polling, telemetry, etc.). The scraper falls back to `domcontentloaded` + best-effort `load` with a 15s timeout, so pages still capture; if they don't, increase `request_delay_ms` and reduce concurrency.

### Port 12000 already in use

Set `DOCKER_PORT=<free-port>` in `.env` and `docker compose up -d`.

---

## Roadmap

### v1 (Complete)

- [x] Two-phase scrape + extract pipeline
- [x] HTTP + Playwright crawl modes
- [x] Sliding-window async dispatcher with per-domain + global rate limiting
- [x] Boundary-scoped extraction with URL pattern filtering
- [x] File-based regex extraction
- [x] Schema templates + visual builder + JSON editor
- [x] Point-and-click content mapper with iframe injection
- [x] Real-time WebSocket progress events
- [x] Resumable jobs (auto-resume orphans on startup)
- [x] Encrypted credential storage (Fernet, fail-loudly)
- [x] `merge_by` + `url_regex` cross-page record consolidation
- [x] JSON / CSV export
- [x] Server-authoritative pipeline state with explicit transition graph (gateway rejects illegal transitions; PATCH does not accept `status`)
- [x] Wizard step indicators derived from the job record (no client-side accumulator); cloned and re-run jobs reflect their actual state, not the previous session's
- [x] Failure attribution — `failed_stage` ∈ `scrape | extract` distinguishes which step needs attention
- [x] Cold clone with full extraction-config carry-over (`POST /api/jobs/{id}/clone` with optional `name_override`)

### v2 (Planned)

- [ ] Browser-driven interactive login (remote browser streaming, replaces cookie-export workflow)
- [ ] Multi-strategy merge (`first_non_null` is the only strategy today)
- [ ] Scheduled / recurring jobs
- [ ] Job-level cost / quota dashboards
- [ ] Pluggable post-processing (transform pipelines on extracted records)

---

## License

(TBD)
