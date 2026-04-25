# Site Extractor — Requirements Specification

## Overview

A site spider and content extraction tool delivered as a Docker Compose service. The application presents a web UI that allows users to create two-phase extraction jobs: first scraping websites, then extracting structured data from the scraped content.

---

## Architecture

### Service Decomposition

| Service | Role | Port |
|---|---|---|
| `api-gateway` | REST API + WebSocket hub + serves React UI + orchestration | 12000 |
| `scraper-service` | HTTP and Playwright/Chromium crawling engine | 12001 (internal) |
| `extraction-service` | CSS selector execution, boundary-scoped extraction | 12002 (internal) |
| `redis` | Job state, pub/sub for real-time progress | 6379 (internal) |

- The API gateway orchestrates jobs and proxies to worker services.
- The scraper service handles crawling and publishes page/resource records via Redis.
- The extraction service handles CSS selector execution against stored pages.
- A Redis consumer in the API gateway syncs scraper output to SQLite.
- Extraction previews are capped by a configurable record limit to bound response size.

### Tech Stack

**Frontend**
- React 19 + TypeScript
- Vite (build tool)
- Tailwind CSS 4 (compile-time via @tailwindcss/vite)
- FlyonUI (component library)
- Radix UI (accessible primitives: dialog, dropdown, tabs, toast, etc.)
- Tabler icons via Iconify
- Zustand (state management)
- TanStack React Query (data fetching)
- React Router v6 (routing)

**Backend**
- FastAPI + Python 3.11 + Uvicorn
- aiosqlite (SQLite async)
- Redis (aioredis)
- Playwright for Python (headless browser)
- httpx or aiohttp (lightweight HTTP scraping)

**Deployment**
- Docker Compose with multi-stage builds
- CPU-only (no GPU use case)
- Port range: 12000–12999
- Storage: Docker volume mounted at `/data`

---

## Phase 1: Scraper

### Crawl Modes

Two mutually selectable modes per job:

1. **Lightweight HTTP** — Uses httpx/aiohttp with configurable User-Agent string. Fast, low resource. Best for static sites.
2. **Headless Browser** — Uses Playwright with Chromium. Executes JavaScript, captures dynamically rendered content. Required for SPAs and AJAX-heavy pages.

### Seed URLs

- One or more seed URLs per job.
- Each seed URL begins at depth 0.

### Depth Limit

- Measured in **link hops** from the seed URL (not URL path depth).
- Configurable per job. Default: 3.

### Domain Filtering

- **Type**: Allowlist.
- **Default**: The domain of each seed URL is automatically added.
- **Matching**: Strict domain match with wildcard support (e.g., `*.example.com` matches `sub.example.com`).
- **Path filters**: Optional path prefix filters (e.g., `/docs/*`) restrict crawling to specific URL paths within allowed domains.
- **Cross-domain redirect behavior**: If a redirect targets a domain not in the allowlist, the redirect target URL is recorded but not downloaded.

### Robots.txt

- **Default**: Respected.
- **Per-job toggle**: User can opt out of robots.txt compliance.

### Rate Limiting

- **Delay between requests**: Configurable. Default: 500ms.
- **Max concurrent requests per domain**: Configurable. Default: 2.
- **Max total concurrent requests**: Configurable. Default: 10.

### Proxy Support

- HTTP/HTTPS proxy only (no SOCKS5).
- Single proxy per job.
- Configured via environment variable (`HTTP_PROXY` / `HTTPS_PROXY`).

### Authentication

Four methods, selectable per job:

1. **Basic Auth** — Username + password sent with each request.
2. **Bearer Token** — Token included in Authorization header.
3. **Cookie Injection** — User provides name=value cookie pairs.
4. **Browser Login Sequence** — *Deferred to v2.* Interactive browser login requires remote browser streaming infrastructure. For v1, users should log in via their own browser, export cookies, and use the Cookie Injection method.

- Credentials are **stored with the job** (encrypted at rest).

### Resource Filters

Resources are categorized. Each category has include/exclude toggle with extension lists. **Exclude takes priority over include.**

| Category | Default Extensions | Default State |
|---|---|---|
| **Web Pages** | html, htm, php, asp, aspx, jsp | Enabled |
| **Images** | jpg, jpeg, png, gif, webp, svg, ico, bmp, tiff | Disabled |
| **Media** | mp4, avi, mov, wmv, webm, mkv, mp3, wav, ogg, flac | Disabled |
| **Documents** | pdf, doc, docx, xls, xlsx, ppt, pptx, txt, csv, rtf | Disabled |
| **Archives** | zip, tar, gz, rar, 7z | Disabled |
| **Code** | json, xml, yaml, css, js | Disabled |

- Categories are customizable: users can rename, add, or remove categories and their extensions.
- Detection: HEAD request first to check Content-Type (MIME). If HEAD fails, download and inspect MIME.
- **Deduplication**: By URL and content hash. Duplicate resources are stored once.

### Max Download Size

- **Environment variable**: `MAX_DOWNLOAD_SIZE` (default: 500MB).
- **Per-job override**: User can set a different limit in the job config.
- Applies to total downloaded content per job.

### Crawl Resume

- If a scrape is interrupted (failure, cancellation, pause), it can be resumed.
- Crawl state (visited URLs, queue, downloaded files) is persisted to allow resumption.

### Redirect Handling

- Redirects are followed silently.
- All redirects are logged (source URL → target URL → final URL).
- Cross-domain redirects: if target domain is not in the allowlist, the link is recorded but not followed.

### Page Storage Format

For each scraped page:

- **Raw HTML**: As received (HTTP mode) or as rendered by the browser (Playwright mode).
- **CSS**: Inlined or stored as separate files for visual rendering.
- **No JavaScript**: JS is not stored or executed during preview.
- **Assets**: Referenced assets (images, etc.) that match enabled resource filters are downloaded. HTML `src`/`href` attributes are rewritten to point to local copies.
- **Result**: A fully navigable local mirror of the scraped site.
- **Non-document files** (images, PDFs, etc.): Displayed in a file listing view rather than rendered inline.

---

## Phase 2: Extraction

### Extraction Modes

Two mutually exclusive modes per job (user must choose one):

1. **Document-Based**: Uses CSS selectors to extract content from HTML pages into a schema.
2. **File-Based**: Uses regex patterns to categorize scraped filenames.

### Extraction Schema

#### Structure

- **Fields**: Named key-value pairs. Keys are strings, values have a type.
- **Types**: `string`, `number`, `image`.
  - `string`: Extracted text content.
  - `number`: Extracted numeric value.
  - `image`: Downloads the image locally, stores the local file path, displays as an image in preview/results.
- **Collections** (arrays): Defined with `[]` syntax. Represent repeating groups.
- **Records** (objects): Defined with `{}` syntax. Represent nested structures.
- **Max nesting depth**: 5 levels.

#### Example Schema

```json
{
  "title": "string",
  "detail": "string",
  "images": [
    {
      "preview": "image",
      "url": "string",
      "alt": "string"
    }
  ],
  "movie": {
    "title": "string",
    "runtime": "number",
    "year": "number"
  }
}
```

#### Schema Management

- Schemas are **reusable** across jobs.
- Schemas are saved independently with a name and description.
- **Dedicated "Schemas" page** in the nav for managing saved schemas (create, edit, delete).
- Within the wizard (step 3), users can **load** a saved schema or **save** the current schema.
- **Template presets** are available for common patterns (e.g., blog posts, product listings).

#### Schema Builder UI

- **Visual tree builder**: Add fields via buttons, set name/type via dropdowns, drag to nest. Fully graphical.
- **Raw JSON editor**: JSON with syntax highlighting and validation. Toggle between visual and JSON views.

### Document-Based Extraction

#### Boundary-Scoped Extraction Model

Schemas are standalone entities that define structure but not mapping. The extraction mapping is per-job and uses a **cumulative boundary model**:

- **Root boundary**: A CSS selector defining the top-level extraction scope. Each match produces one record. If omitted, the entire page (`<body>`) is the scope — one record per page.
- **Record boundaries**: Nested record (object) fields can optionally define their own boundary selector to narrow scope within the parent boundary.
- **Collection boundaries + iterators**: Collection (array) fields define both a boundary (scope) and an iterator (repeating element selector). The iterator identifies each item in the collection within the boundary.
- **Cumulative scoping**: Boundaries are cumulative — if root is `.products` and a nested collection boundary is `.card`, the effective scope is `.products .card`.
- **Optional boundaries**: Omitting a boundary inherits the parent scope.

#### Example Mapping

```
Root boundary: div.product          → one record per .product element
  title:       .name                → textContent from .name within each div.product
  price:       .price               → textContent, parsed as number
  images:      boundary: null, iterator: img    → iterate <img> elements
    url:         (self)             → src attribute of each <img>
    alt:         (self)             → alt attribute
```

#### Leaf Field Extraction

Each leaf field maps a CSS selector + optional attribute modifier:
- **string**: Element's `textContent` (default), or specified attribute value.
- **number**: Element's `textContent`, parsed as a number, or specified attribute.
- **image**: Element's `src` attribute (default), downloaded locally.
- **Attribute modifier**: Any field can specify an explicit attribute (e.g., `href`, `data-id`, `title`) to extract instead of text content.
- **Disjunctions**: `a[id=foo] | span[ref=bar]` — multiple selectors target the same field. First match wins.

#### Content Mapping UI

- Scraped pages are rendered in an **iframe** via an API proxy endpoint.
- A **picker script** is injected into served pages for point-and-click element selection.
- **Hover highlighting**: Blue overlay on hovered elements when picker is active.
- **Click capture**: Generates a CSS selector (class-preferring, falls back to structural paths) and sends it to the parent via `postMessage`.
- **Highlight-all-matches**: After selecting, all elements matching the generated selector are highlighted with green overlays, with a match count badge.
- Generated selectors are **manually editable** (hybrid point-and-click + manual input).
- Link clicks within the iframe are **intercepted** so users navigate between scraped pages without leaving the mapping UI.
- **Workflow**: Select a schema field → click "Pick Element" → click in the iframe → selector auto-fills → matches highlight.

#### URL Pattern Filtering

- An optional URL pattern (e.g., `example.com/products/*`) restricts which scraped pages the mapping applies to.
- Pages not matching the pattern are skipped during extraction.

#### Live Preview

- As selectors are defined, a **dynamic preview** shows extracted data in real-time.
- Preview shows how many pages matched and sample extracted values.
- Preview is capped by a configurable record limit.

### File-Based Extraction

#### How It Works

- User defines **named regex patterns** that match against scraped filenames.
- Each pattern is associated with a schema key name.

#### Example

```
report:       ^report-[0-9]{4}-Q[0-9]{1,2}\.pdf$
spreadsheet:  ^spreadsheet-[0-9]{4}-Q[0-9]{1,2}\.xlsx$
```

#### Output

Categorized listing with metadata:

```json
{
  "report": [
    {
      "filename": "report-2024-Q1.pdf",
      "path": "/data/job-123/report-2024-Q1.pdf",
      "size": 245000,
      "mime": "application/pdf"
    }
  ],
  "spreadsheet": [
    {
      "filename": "spreadsheet-2024-Q2.xlsx",
      "path": "/data/job-123/spreadsheet-2024-Q2.xlsx",
      "size": 180000,
      "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
  ]
}
```

- **No content extraction** from inside files — metadata only (filename, local path, size, MIME type).

---

## Job Lifecycle

### States

```
created → scraping → scraped → mapping → extracting → completed
              ↓                                ↓
           failed                           failed
```

- **Two-phase**: Scraping and extraction are separate phases.
- After scraping completes, the job moves to `scraped`. The user then configures extraction.
- The extraction phase can be **re-run** without re-scraping (e.g., with a different schema or different selectors).

### Job Controls

- **Cancel**: Stop a running scrape or extraction.
- **Pause**: Pause a running scrape (persists crawl state for later resumption).
- **Resume**: Resume a paused scrape from where it left off.

### Deletion

Two deletion options:
1. **Delete job only**: Removes the job record but leaves the scraped data and results on disk.
2. **Delete job + results**: Removes everything (job record, scraped data, extraction results).

### Persistence

- Jobs persist indefinitely until manually deleted.
- Job records stored in SQLite.
- Scraped data and results stored on the Docker volume (`/data`).

---

## UI

### Pages

| Page | Description |
|---|---|
| **Job History** (Dashboard) | List of all jobs. Columns: date, status, mode (file/doc), duration, link to job. Search by URL. Filter by URL, date range, status. |
| **Schemas** | Dedicated page for managing saved extraction schemas (CRUD). |
| **Job Wizard** | 5-step wizard for creating and running jobs (see below). |

### Job Wizard — 5 Steps

| Step | Name | Description |
|---|---|---|
| 1 | **Scraper Config** | Seed URLs, crawl mode (HTTP/browser), domain filters, path filters, depth limit, resource filter categories, rate limiting, auth config, robots.txt toggle, max download size. |
| 2 | **Scrape Monitor** | Real-time WebSocket progress. Displays: pages discovered (URL + depth), pages downloaded (URL + status + size), resources downloaded (filename + type), errors (URL + message), overall progress (pages done / estimated total, bytes / max). Live page-tree visualization (bonus). "Scraping Complete — Continue" button when done. |
| 3 | **Schema Builder** | Visual tree builder + raw JSON editor toggle. Load saved schema, save current schema. Template presets. |
| 4 | **Content Mapper** | Document-based: iframe page preview + selector mapping UI with live preview. File-based: regex pattern editor with matched file preview. |
| 5 | **Results** | Tabular display with flattened dot-notation columns + expandable rows. Paginated, sortable, filterable. Export as JSON or CSV. Preview mode (first N rows) before full extraction. Re-run extraction button to re-extract with current mappings without re-scraping. |

### Wizard Navigation

- Users can navigate back to any **completed** step.
- Returning to a completed job from the history page lands on **step 5 (Results)**.
- Step 2 shows "Scraping Complete — Continue" button (does not auto-advance).

### Layout

- Follows reaction-maker patterns: sidebar navigation (Job History, Schemas), header, main content area.
- No sidebar job list (history page serves this purpose).
- Dark theme by default.
- Real-time WebSocket updates displayed dynamically in the UI.

### Clone Job (Nice-to-Have)

- From the history page, clone a previous job's configuration into a new job.

---

## WebSocket Events (Scrape Monitor)

Events broadcast during scraping:

| Event | Data |
|---|---|
| `PAGE_DISCOVERED` | URL, depth, source URL |
| `PAGE_DOWNLOADED` | URL, HTTP status, size, content type |
| `RESOURCE_DOWNLOADED` | Filename, type/category, size |
| `SCRAPE_ERROR` | URL, error message |
| `SCRAPE_PROGRESS` | Pages done, estimated total, bytes downloaded, max bytes |
| `SCRAPE_STATUS` | Status change (scraping, paused, completed, failed) |
| `PAGE_TREE_UPDATE` | Tree structure update for live visualization (bonus) |

---

## Data Persistence

### SQLite Tables

- `jobs` — Job records (id, seed URLs, config, status, phase, timestamps, error messages).
- `schemas` — Saved extraction schemas (id, name, description, schema JSON, created/updated timestamps).
- `extraction_results` — Extraction output per job (id, job_id, data JSON, format metadata).
- `scrape_pages` — Index of scraped pages per job (id, job_id, URL, local path, status, content type, size).
- `scrape_resources` — Index of downloaded resources (id, job_id, URL, local path, category, size, MIME, content hash).

### File Storage (Docker Volume: `/data`)

```
/data/
├── jobs/
│   └── {job_id}/
│       ├── pages/          # Scraped HTML + CSS
│       ├── assets/         # Downloaded resources (images, docs, etc.)
│       ├── results/        # Extraction output (JSON, CSV)
│       └── crawl_state.json  # For resume support
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DOCKER_PORT` | Host port for API gateway | `12000` |
| `MAX_DOWNLOAD_SIZE` | Max total download per job (bytes) | `524288000` (500MB) |
| `HTTP_PROXY` | HTTP proxy URL | (none) |
| `HTTPS_PROXY` | HTTPS proxy URL | (none) |
| `DATA_PATH` | Host path for data volume mount | `./data` |
| `REDIS_URL` | Redis connection string | `redis://redis:6379` |
| `ENCRYPTION_KEY` | Key for encrypting stored credentials | (required) |

---

## Open Questions / Future Considerations

- Schema template presets: which specific templates to ship initially (to be defined during implementation).
- Page-tree visualization complexity: best-effort bonus feature during scrape monitoring.
- Clone job: nice-to-have, implement if time permits.
- Browser login sequence (v2): embedded browser window for interactive login + session capture.
- Resource filter customization: allow users to add/remove/rename filter categories (currently fixed defaults).
- Date range filter on job history page (currently URL search + status filter only).
- Credential encryption at rest (cryptography library is included but encryption not yet implemented).
